"""DB reads backing GET /ingestion/progress.

The page polls every few seconds, so this asks Postgres for counts (GROUP BY) and
for the few rows it lists, instead of loading every file and job row into Python
-- that was fine for hundreds of files and slow at 56,000. The result has the
same shape build_progress() / build_pipeline() in progress.py produce from plain
rows; tests/test_progress_sql.py checks the two against each other on a live
database.

A file's state comes from each (file, job type)'s MOST RECENT job only: a retried
job leaves one row per attempt, and an old failed attempt must not keep a file
looking broken once a later one succeeded. In SQL that is "no newer job of the
same file and type" (NEWEST below).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app import scan_status
from app.config import settings
from app.conversion.docling_health import docling_status
from app.ingestion.progress import (
    _STAGE_LABELS,
    PIPELINE_LIMIT,
    READY_WINDOW,
    RECENT_LIMIT,
    _folder_of,
)
from app.ingestion.uploads import UPLOAD_FOLDER_NAME
from app.pipeline_control import is_paused

# The failure and running lists stop at this many rows; the counts cover all of them.
LIST_LIMIT = 200

# true when the job row `j` has no newer job of the same file and type
NEWEST = "NOT EXISTS (SELECT 1 FROM jobs n WHERE n.file_id = j.file_id AND n.job_type = j.job_type AND n.id > j.id)"

# top-level folder under raw/, same rule as top_level_folder(): NULL for a file directly in raw/
_NORM = "replace(f.path, chr(92), '/')"
FOLDER = (
    f"CASE WHEN left({_NORM}, :blen) = :base AND position('/' in substr({_NORM}, :blen + 1)) > 0 "
    f"THEN split_part(substr({_NORM}, :blen + 1), '/', 1) END"
)

# a file with a running conversion job (newest of its type)
CONVERTING = (
    "EXISTS (SELECT 1 FROM jobs j WHERE j.file_id = f.id AND j.job_type LIKE 'convert%' "
    f"AND j.state = 'running' AND {NEWEST})"
)

# converted files still waiting to be searchable: no finished or failed indexing, no chunks yet
INDEXING_FROM = """
    FROM files f
    LEFT JOIN LATERAL (
        SELECT state, created_at FROM jobs WHERE file_id = f.id AND job_type = 'index' ORDER BY id DESC LIMIT 1
    ) li ON true
    WHERE f.status IN ('converted', 'summarized')
      AND coalesce(li.state, '') NOT IN ('done', 'failed')
      AND NOT EXISTS (SELECT 1 FROM chunks c WHERE c.file_id = f.id)
"""


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _elapsed(now: datetime, since: datetime | None) -> int:
    return max(0, int((now - since).total_seconds())) if since else 0


def get_ingestion_progress(session: Session, raw_dir: str, pipeline_limit: int = PIPELINE_LIMIT) -> dict:
    now = datetime.now(timezone.utc)
    base = raw_dir.replace("\\", "/").rstrip("/") + "/"
    anchor = {"base": base, "blen": len(base)}

    def rows(sql: str, **params):
        return session.execute(text(sql), {**anchor, **params}).all()

    def folder_name(path: str) -> str:
        return _folder_of(path, raw_dir)

    # ---- files by folder and status
    totals = {"files": 0, "unsupported": 0, "discovered": 0, "converted": 0, "summarized": 0, "running": 0, "failed": 0}
    folders: dict[str, dict[str, int]] = {}

    def bucket(name: str) -> dict[str, int]:
        return folders.setdefault(name, {"total": 0, "converted": 0, "summarized": 0, "running": 0, "failed": 0})

    for folder, status, n in rows(f"SELECT {FOLDER} AS folder, f.status, count(*) FROM files f GROUP BY 1, 2"):
        if status == "unsupported":
            totals["unsupported"] += n
            continue
        totals["files"] += n
        if status in ("discovered", "summarized", "converted"):
            totals[status] += n
        b = bucket(folder or "(root)")
        b["total"] += n
        if status in ("converted", "summarized"):
            b["converted"] += n
        if status == "summarized":
            b["summarized"] += n
    # a summarized file has also been converted: count it in both stages
    totals["converted"] += totals["summarized"]

    # ---- files with a running or failed job (a file counts once, whatever its job types)
    for folder, state, n in rows(
        f"SELECT {FOLDER} AS folder, j.state, count(DISTINCT j.file_id) "
        f"FROM jobs j JOIN files f ON f.id = j.file_id "
        f"WHERE j.state IN ('running', 'failed') AND {NEWEST} GROUP BY 1, 2"
    ):
        key = "running" if state == "running" else "failed"
        totals[key] += n
        bucket(folder or "(root)")[key] += n

    # ---- running jobs, failed jobs, and what finished last
    def entry(path: str, size_bytes: int, job_type: str) -> dict:
        return {
            "file": Path(path).name,
            "folder": folder_name(path),
            "stage": _STAGE_LABELS.get(job_type, job_type),
            "size_bytes": size_bytes,
        }

    active = [
        {**entry(path, size, job_type), "elapsed_seconds": _elapsed(now, created_at)}
        for path, size, job_type, created_at in rows(
            f"SELECT f.path, f.size_bytes, j.job_type, j.created_at FROM jobs j JOIN files f ON f.id = j.file_id "
            f"WHERE j.state = 'running' AND {NEWEST} ORDER BY j.created_at LIMIT :cap",
            cap=LIST_LIMIT,
        )
    ]

    failures = [
        {**entry(path, size, job_type), "error": error, "retries": retries or 0, "at": _iso(updated_at)}
        for path, size, job_type, error, retries, updated_at in rows(
            f"SELECT f.path, f.size_bytes, j.job_type, j.error, j.retries, j.updated_at "
            f"FROM jobs j JOIN files f ON f.id = j.file_id "
            f"WHERE j.state = 'failed' AND {NEWEST} ORDER BY j.updated_at DESC LIMIT :cap",
            cap=LIST_LIMIT,
        )
    ]
    failures_total = rows(f"SELECT count(*) FROM jobs j WHERE j.state = 'failed' AND {NEWEST}")[0][0]

    recent = [
        {**entry(path, size, job_type), "finished_at": _iso(updated_at)}
        for path, size, job_type, updated_at in rows(
            f"SELECT f.path, f.size_bytes, j.job_type, j.updated_at FROM jobs j JOIN files f ON f.id = j.file_id "
            f"WHERE j.state = 'done' AND {NEWEST} ORDER BY j.updated_at DESC LIMIT :n",
            n=RECENT_LIMIT,
        )
    ]

    # ---- the pipeline: queued -> converting -> indexing
    # Per file, the earliest running conversion; a file can have more than one convert-type job.
    converting_since: dict[int, tuple[str, int, datetime]] = {}
    for file_id, path, size, created_at in rows(
        f"SELECT f.id, f.path, f.size_bytes, j.created_at FROM files f JOIN jobs j ON j.file_id = f.id "
        f"WHERE f.status = 'discovered' AND j.state = 'running' AND j.job_type LIKE 'convert%' AND {NEWEST}"
    ):
        if file_id not in converting_since or created_at < converting_since[file_id][2]:
            converting_since[file_id] = (path, size, created_at)

    queued_total = max(0, totals["discovered"] - len(converting_since))
    indexing_total = rows(f"SELECT count(*) {INDEXING_FROM}")[0][0]
    counts = {"queued": queued_total, "converting": len(converting_since), "indexing": indexing_total}

    def listed(file_id: int, path: str, size: int, stage: str, since: datetime | None) -> dict:
        return {
            "id": file_id,
            "file": Path(path).name,
            "folder": folder_name(path),
            "stage": stage,
            "size_bytes": size,
            "since": _iso(since),
            "elapsed_seconds": _elapsed(now, since),
        }

    converting = [listed(i, p, s, "converting", since) for i, (p, s, since) in converting_since.items()]
    indexing = [
        listed(file_id, path, size, "indexing", index_created or converted_at or discovered_at)
        for file_id, path, size, discovered_at, index_created, converted_at in rows(
            f"SELECT f.id, f.path, f.size_bytes, f.discovered_at, li.created_at, "
            f"(SELECT max(updated_at) FROM jobs WHERE file_id = f.id AND job_type LIKE 'convert%') "
            f"{INDEXING_FROM} ORDER BY f.id LIMIT :n",
            n=pipeline_limit,
        )
    ]
    queued = [
        listed(file_id, path, size, "queued", discovered_at)
        for file_id, path, size, discovered_at in rows(
            f"SELECT f.id, f.path, f.size_bytes, f.discovered_at FROM files f "
            f"WHERE f.status = 'discovered' AND NOT {CONVERTING} "
            f"ORDER BY f.discovered_at NULLS LAST, f.id LIMIT :n",
            n=pipeline_limit,
        )
    ]
    in_flight: list[dict] = []
    for group in (converting, indexing, queued):  # most advanced first, longest waiting first within a stage
        in_flight.extend(sorted(group, key=lambda f: -f["elapsed_seconds"]))

    recently_ready = [
        {"id": file_id, "file": Path(path).name, "folder": folder_name(path), "finished_at": _iso(updated_at)}
        for file_id, path, updated_at in rows(
            f"SELECT j.file_id, f.path, j.updated_at FROM jobs j JOIN files f ON f.id = j.file_id "
            f"WHERE j.job_type = 'index' AND j.state = 'done' AND j.updated_at > :cutoff "
            f"AND f.status IN ('converted', 'summarized') AND {NEWEST} ORDER BY j.updated_at DESC",
            cutoff=now - READY_WINDOW,
        )
    ]

    return {
        "totals": totals,
        # chat attachments aren't a corpus folder; their files still count in the totals
        "folders": [
            {"name": name, **values} for name, values in sorted(folders.items()) if name != UPLOAD_FOLDER_NAME
        ],
        "active": active,
        "failures": failures,
        "failures_total": failures_total,
        "recent": recent,
        "pipeline": {
            "queue": counts,
            "files": in_flight[:pipeline_limit],
            "files_total": sum(counts.values()),
            "recently_ready": recently_ready,
        },
        # With AUTO_SUMMARIZE off, "done" means converted (and indexed), not
        # summarized -- the UI needs to know which to count.
        "summaries_enabled": settings.auto_summarize,
        "paused": is_paused(),
        "scan": scan_status.read(),
        "docling": docling_status(),
    }
