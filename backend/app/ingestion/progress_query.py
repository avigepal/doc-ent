"""DB reads backing GET /ingestion/progress. Needs a live Postgres, so like
folder_status_query.py it isn't unit-tested; the shaping is in progress.py."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.ingestion.progress import build_progress
from app.models import FileRecord, JobRecord


def get_ingestion_progress(session: Session, raw_dir: str) -> dict:
    files = [
        (r.id, r.path, r.status, r.size_bytes)
        for r in session.execute(
            select(FileRecord.id, FileRecord.path, FileRecord.status, FileRecord.size_bytes)
        ).all()
    ]

    latest_ids = select(func.max(JobRecord.id)).group_by(JobRecord.file_id, JobRecord.job_type)
    jobs = [
        (j.file_id, j.job_type, j.state, j.error, j.retries or 0, j.created_at, j.updated_at)
        for j in session.execute(select(JobRecord).where(JobRecord.id.in_(latest_ids))).scalars()
    ]

    progress = build_progress(raw_dir, files, jobs, datetime.now(timezone.utc))
    # With AUTO_SUMMARIZE off, "done" means converted (and indexed), not
    # summarized -- the UI needs to know which to count.
    progress["summaries_enabled"] = settings.auto_summarize
    return progress
