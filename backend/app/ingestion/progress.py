"""Live ingestion progress for the dashboard's Progress page.

Pure shaping, same split as folder_status.py / folder_status_query.py:
the DB reads live in progress_query.py, the grouping lives here so it is
testable with plain tuples.

Status is derived from each (file, job_type)'s MOST RECENT job only — a
retried job leaves one row per attempt, and an old failed attempt must not
keep a file looking broken once a later one succeeded.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from app.ingestion.folder_status import top_level_folder

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


def _folder_of(path: str, raw_dir: str) -> str:
    return top_level_folder(path, raw_dir) or "(root)"


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
        "folders": [{"name": name, **counts} for name, counts in sorted(folders.items())],
        "active": active,
        "failures": failures,
        "recent": recent,
    }
