"""Shared scan/convert/summarize logic used by both the manual /ingest/*
endpoints (app/main.py) and the automatic periodic task (app/tasks/
auto_ingest.py). Keeping it here means "click the button" and "wait for
the next tick" always do exactly the same thing.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import settings
from app.ingestion.summary import summarize
from app.ingestion.walker import scan_directory
from app.models import FileRecord


def run_scan(session: Session) -> dict:
    raw_dir = Path(settings.data_dir) / "raw"
    scanned = list(scan_directory(raw_dir))

    for f in scanned:
        stmt = pg_insert(FileRecord).values(
            path=f.path,
            sha256=f.sha256,
            mime_type=f.mime_type,
            size_bytes=f.size_bytes,
            queue=f.queue,
        ).on_conflict_do_update(
            index_elements=[FileRecord.path],
            set_={
                "sha256": f.sha256,
                "mime_type": f.mime_type,
                "size_bytes": f.size_bytes,
                "queue": f.queue,
            },
        )
        session.execute(stmt)
    session.commit()

    result = summarize(scanned)
    return {
        "raw_dir": str(raw_dir),
        "total_files": result.total_files,
        "total_bytes": result.total_bytes,
        "by_queue": result.by_queue,
        "by_mime": result.by_mime,
    }


def run_convert_enqueue(session: Session) -> dict[str, int]:
    from app.tasks.convert import convert_email_archive, convert_fast

    task_by_queue = {
        "convert_fast": convert_fast,
        "convert_email_archive": convert_email_archive,
    }

    enqueued: dict[str, int] = {}
    for queue, task in task_by_queue.items():
        rows = session.execute(
            select(FileRecord.id).where(FileRecord.queue == queue, FileRecord.status == "discovered")
        ).scalars().all()
        for file_id in rows:
            task.delay(file_id)
        enqueued[queue] = len(rows)

    return enqueued


def run_summarize_enqueue(session: Session) -> int:
    from app.tasks.summarize import summarize_file

    rows = session.execute(
        select(FileRecord.id).where(FileRecord.status == "converted")
    ).scalars().all()

    for file_id in rows:
        summarize_file.delay(file_id)

    return len(rows)
