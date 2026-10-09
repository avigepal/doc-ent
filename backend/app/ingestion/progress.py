"""Live ingestion progress for the dashboard's Progress page.

Pure shaping, same split as folder_status.py / folder_status_query.py:
the DB reads live in progress_query.py, the grouping lives here so it is
testable with plain tuples.

Status is derived from each (file, job_type)'s MOST RECENT job only — a
retried job leaves one row per attempt, and an old failed attempt must not
keep a file looking broken once a later one succeeded.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.ingestion.folder_status import top_level_folder
from app.ingestion.uploads import UPLOAD_FOLDER_NAME

# (file_id, path, status, size_bytes)
FileRow = tuple[int, str, str, int]
# (file_id, job_type, state, error, retries, created_at, updated_at)
JobRow = tuple[int, str, str, str | None, int, datetime, datetime]

_STAGE_LABELS = {
    "convert_fast": "Converting",
    "convert_email_archive": "Converting email",
    "convert_ocr": "OCR",
    "convert_vision": "Vision",
    "summarize": "Summarizing",
}

RECENT_LIMIT = 10


# Files still moving through the pipeline are listed one by one, up to this many
# (the counts always cover all of them).
PIPELINE_LIMIT = 100

# A file counts as "just became ready" for this long after its index job finished,
# so the dashboard can toast it even if it polls a few seconds late.
READY_WINDOW = timedelta(minutes=2)

# (file_id, path, status, size_bytes, discovered_at)
PipelineFileRow = tuple[int, str, str, int, datetime | None]

_PIPELINE_ORDER = {"converting": 0, "indexing": 1, "queued": 2}


def _folder_of(path: str, raw_dir: str) -> str:
    return top_level_folder(path, raw_dir) or "(root)"


def pipeline_candidates(files: list[PipelineFileRow], jobs: list[JobRow]) -> list[int]:
    """Converted files whose indexing isn't recorded as done: the ones the
    database has to be asked about ("do chunks exist?") to tell indexing from
    ready. Everything else is decided from the job rows alone."""
    index_done = {j[0] for j in jobs if j[1] == "index" and j[2] == "done"}
    return [f[0] for f in files if f[2] in ("converted", "summarized") and f[0] not in index_done]


def build_pipeline(
    raw_dir: str,
    files: list[PipelineFileRow],
    jobs: list[JobRow],
    chunked_ids: set[int],
    now: datetime,
) -> dict[str, Any]:
    """Where each unfinished file is: queued -> converting -> indexing -> ready.

    `jobs` are each file's latest job per type (as for build_progress);
    `chunked_ids` are the ids among pipeline_candidates() that already have
    chunks. Failed files are left out -- the Failures list already has them,
    with the reason. Returns the per-stage counts, the unfinished files
    (most advanced first) and the files that finished indexing moments ago."""
    jobs_by_file: dict[int, dict[str, tuple]] = {}
    for file_id, job_type, state, error, _retries, created_at, updated_at in jobs:
        jobs_by_file.setdefault(file_id, {})[job_type] = (state, created_at, updated_at)

    counts = {"queued": 0, "converting": 0, "indexing": 0}
    in_flight: list[dict[str, Any]] = []
    recently_ready: list[dict[str, Any]] = []

    for file_id, path, status, size_bytes, discovered_at in files:
        file_jobs = jobs_by_file.get(file_id, {})
        index = file_jobs.get("index")
        since: datetime | None
        if status == "discovered":
            running = [v for t, v in file_jobs.items() if t.startswith("convert") and v[0] == "running"]
            stage = "converting" if running else "queued"
            since = running[0][1] if running else discovered_at
        elif status in ("converted", "summarized"):
            if (index and index[0] == "done") or file_id in chunked_ids:
                if index and index[0] == "done" and index[2] and now - index[2] <= READY_WINDOW:
                    recently_ready.append(
                        {
                            "id": file_id,
                            "file": Path(path).name,
                            "folder": _folder_of(path, raw_dir),
                            "finished_at": _iso(index[2]),
                            "_sort": index[2],
                        }
                    )
                continue
            if index and index[0] == "failed":
                continue
            stage = "indexing"
            converted_at = max((v[2] for t, v in file_jobs.items() if t.startswith("convert") and v[2]), default=None)
            since = index[1] if index else converted_at or discovered_at
        else:
            continue  # failed or unsupported

        counts[stage] += 1
        in_flight.append(
            {
                "id": file_id,
                "file": Path(path).name,
                "folder": _folder_of(path, raw_dir),
                "stage": stage,
                "size_bytes": size_bytes,
                "since": _iso(since),
                "elapsed_seconds": max(0, int((now - since).total_seconds())) if since else 0,
            }
        )

    in_flight.sort(key=lambda f: (_PIPELINE_ORDER[f["stage"]], -f["elapsed_seconds"]))
    recently_ready.sort(key=lambda r: r["_sort"], reverse=True)
    return {
        "queue": counts,
        "files": in_flight[:PIPELINE_LIMIT],
        "files_total": len(in_flight),
        "recently_ready": [{k: v for k, v in r.items() if k != "_sort"} for r in recently_ready],
    }


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def build_progress(raw_dir: str, files: list[FileRow], jobs: list[JobRow], now: datetime) -> dict[str, Any]:
    by_id = {f[0]: f for f in files}

    # Unsupported files (.exe, .dll, ...) can never be processed, so they're
    # counted apart instead of making "N of M fully processed" unreachable.
    supported = [f for f in files if f[2] != "unsupported"]
    totals = {
        "files": len(supported),
        "unsupported": len(files) - len(supported),
        "discovered": 0,
        "converted": 0,
        "summarized": 0,
        "running": 0,
        "failed": 0,
    }
    folders: dict[str, dict[str, int]] = {}

    def folder_bucket(name: str) -> dict[str, int]:
        return folders.setdefault(
            name, {"total": 0, "converted": 0, "summarized": 0, "running": 0, "failed": 0}
        )

    for _, path, status, _ in supported:
        bucket = folder_bucket(_folder_of(path, raw_dir))
        bucket["total"] += 1
        if status in ("converted", "summarized"):
            bucket["converted"] += 1
        if status == "summarized":
            bucket["summarized"] += 1
        if status in totals:
            totals[status] += 1

    # a summarized file has also been converted — count it in both stages
    totals["converted"] += totals["summarized"]

    active: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    recent: list[dict[str, Any]] = []
    running_files: set[int] = set()
    failed_files: set[int] = set()

    for file_id, job_type, state, error, retries, created_at, updated_at in jobs:
        file = by_id.get(file_id)
        if file is None:
            continue
        _, path, _, size_bytes = file
        entry = {
            "file": Path(path).name,
            "folder": _folder_of(path, raw_dir),
            "stage": _STAGE_LABELS.get(job_type, job_type),
            "size_bytes": size_bytes,
        }
        if state == "running":
            running_files.add(file_id)
            active.append({**entry, "elapsed_seconds": max(0, int((now - created_at).total_seconds()))})
        elif state == "failed":
            failed_files.add(file_id)
            failures.append({**entry, "error": error, "retries": retries, "at": _iso(updated_at)})
        elif state == "done":
            recent.append({**entry, "finished_at": _iso(updated_at), "_sort": updated_at})

    for file_id in running_files:
        folder_bucket(_folder_of(by_id[file_id][1], raw_dir))["running"] += 1
    for file_id in failed_files:
        folder_bucket(_folder_of(by_id[file_id][1], raw_dir))["failed"] += 1
    totals["running"] = len(running_files)
    totals["failed"] = len(failed_files)

    active.sort(key=lambda a: -a["elapsed_seconds"])
    failures.sort(key=lambda f: f["at"] or "", reverse=True)
    recent.sort(key=lambda r: r["_sort"], reverse=True)
    recent = [{k: v for k, v in r.items() if k != "_sort"} for r in recent[:RECENT_LIMIT]]

    return {
        "totals": totals,
        # chat attachments aren't a corpus folder; their files still count in the totals
        "folders": [
            {"name": name, **counts} for name, counts in sorted(folders.items()) if name != UPLOAD_FOLDER_NAME
        ],
        "active": active,
        "failures": failures,
        "recent": recent,
    }
