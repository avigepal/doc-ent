"""DB queries backing the /folders endpoint's per-folder status. Needs a
live Postgres — not unit-tested here (same pattern as other DB code);
the actual aggregation logic lives in folder_status.py and is tested
there with plain Python data.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.folder_status import FolderStatus, build_folder_statuses
from app.models import FileRecord, JobRecord


def get_folder_statuses(session: Session, raw_dir: str) -> list[FolderStatus]:
    files = session.execute(select(FileRecord.id, FileRecord.path, FileRecord.status)).all()

    running_file_ids = set(
        session.execute(select(JobRecord.file_id).where(JobRecord.state == "running")).scalars().all()
    )
    failed_file_ids = set(
        session.execute(select(JobRecord.file_id).where(JobRecord.state == "failed")).scalars().all()
    )

    return build_folder_statuses(raw_dir, list(files), running_file_ids, failed_file_ids)
