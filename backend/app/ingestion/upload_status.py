"""Progress of freshly uploaded files, for the Ask page's upload notice:
"queued -> converting -> indexing -> ready". The stage logic is pure and
unit-tested; `get_upload_status` is the DB read behind GET
/ingest/upload/status.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ChunkRecord, FileRecord, JobRecord

# Stages after which the UI stops polling.
TERMINAL_STAGES = ("ready", "failed", "unsupported")

# {job_type: (state, error)} -- the most recent job of each type for a file
LatestJobs = dict[str, tuple[str, str | None]]


def upload_stage(status: str, chunk_count: int, jobs: LatestJobs) -> tuple[str, str | None]:
    """(stage, detail) for one file."""
    if status == "unsupported":
        return "unsupported", "This file type can't be processed."

    if status == "failed":
        for job_type, (state, error) in jobs.items():
            if job_type.startswith("convert") and state == "failed" and error:
                return "failed", error
        return "failed", "Processing failed."

    if status == "discovered":
        converting = any(t.startswith("convert") and s == "running" for t, (s, _) in jobs.items())
        return ("converting" if converting else "queued"), None

    # converted / summarized: searchable once its chunks exist
    if chunk_count > 0:
        return "ready", None
    index_state = jobs.get("index", (None, None))[0]
    if index_state == "done":
        return "ready", "No searchable text was found."
    return "indexing", None


def get_upload_status(session: Session, file_ids: list[int]) -> list[dict]:
    if not file_ids:
        return []

    files = session.execute(
        select(FileRecord.id, FileRecord.path, FileRecord.status).where(FileRecord.id.in_(file_ids))
    ).all()

    chunk_counts = dict(
        session.execute(
            select(ChunkRecord.file_id, func.count())
            .where(ChunkRecord.file_id.in_(file_ids))
            .group_by(ChunkRecord.file_id)
        ).all()
    )

    latest_ids = (
        select(func.max(JobRecord.id))
        .where(JobRecord.file_id.in_(file_ids))
        .group_by(JobRecord.file_id, JobRecord.job_type)
    )
    jobs_by_file: dict[int, LatestJobs] = {}
    for job in session.execute(select(JobRecord).where(JobRecord.id.in_(latest_ids))).scalars():
        jobs_by_file.setdefault(job.file_id, {})[job.job_type] = (job.state, job.error)

    result = []
    for file_id, path, status in files:
        stage, detail = upload_stage(status, chunk_counts.get(file_id, 0), jobs_by_file.get(file_id, {}))
        result.append(
            {"id": file_id, "name": path.replace("\\", "/").rsplit("/", 1)[-1], "stage": stage, "detail": detail}
        )
    return sorted(result, key=lambda r: r["id"])
