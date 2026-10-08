"""DB queries backing the /folders endpoint's per-folder status. Needs a
live Postgres — not unit-tested here (same pattern as other DB code);
the actual aggregation logic lives in folder_status.py and is tested
there with plain Python data.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ingestion.folder_status import FolderStatus, build_folder_statuses
from app.models import FileRecord, JobRecord


def get_folder_statuses(session: Session, raw_dir: str) -> list[FolderStatus]:
    files = session.execute(select(FileRecord.id, FileRecord.path, FileRecord.status)).all()

    # Status is based on each (file, job_type)'s MOST RECENT attempt only
    # — e.g. summarize_file retried after a transient LLM-unreachable
    # error creates a new JobRecord row per attempt, so an older failed
    # row must not keep flagging a file red forever once a later retry
    # succeeds (see app/tasks/summarize.py's retry handling).
    latest_per_type = (
        select(JobRecord.file_id, JobRecord.job_type, func.max(JobRecord.id).label("latest_id"))
        .group_by(JobRecord.file_id, JobRecord.job_type)
        .subquery()
    )
    latest_states = session.execute(
        select(JobRecord.file_id, JobRecord.state).join(
            latest_per_type, JobRecord.id == latest_per_type.c.latest_id
        )
    ).all()

    running_file_ids = {file_id for file_id, state in latest_states if state == "running"}
    failed_file_ids = {file_id for file_id, state in latest_states if state == "failed"}

    return build_folder_statuses(raw_dir, list(files), running_file_ids, failed_file_ids)
