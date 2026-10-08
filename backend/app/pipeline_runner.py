"""Shared scan/convert/summarize logic used by both the manual /ingest/*
endpoints (app/main.py) and the automatic periodic task (app/tasks/
auto_ingest.py). Keeping it here means "click the button" and "wait for
the next tick" always do exactly the same thing.

The timer runs every minute, so everything here is built to be cheap and
idempotent on a corpus where nothing changed: unchanged files aren't
re-hashed, files already queued aren't queued again (see queue_guard.py),
and a file whose content changed is sent back through the pipeline.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import case, delete, exists, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import settings
from app.ingestion.prune import paths_to_prune
from app.ingestion.summary import summarize
from app.ingestion.walker import KnownFile, ScannedFile, scan_directory, scan_file
from app.models import ChunkRecord, FileRecord, JobRecord
from app.queue_guard import clear_enqueued, enqueue_once


# Queues that have a working task behind them (see run_convert_enqueue).
# Files routed anywhere else -- unsupported types, plus OCR-queue files
# until that converter exists -- get status "unsupported" instead of
# sitting at "discovered" and being picked up (and failing) every cycle.
RUNNABLE_QUEUES = ("convert_fast", "convert_email_archive", "convert_vision")

# A "running" job older than this is assumed dead (killed worker), not busy.
_RUNNING_JOB_MAX_AGE = timedelta(hours=2)

# Statuses of a file that has been through (or failed in) the pipeline.
# If its content changes, it has to go through again.
_PROCESSED_STATUSES = ("converted", "summarized", "failed")


def _upsert_file(session: Session, f: ScannedFile) -> None:
    stmt = pg_insert(FileRecord).values(
        path=f.path,
        sha256=f.sha256,
        mime_type=f.mime_type,
        size_bytes=f.size_bytes,
        mtime_ns=f.mtime_ns,
        queue=f.queue,
    ).on_conflict_do_update(
        index_elements=[FileRecord.path],
        set_={
            "sha256": f.sha256,
            "mime_type": f.mime_type,
            "size_bytes": f.size_bytes,
            "mtime_ns": f.mtime_ns,
            "queue": f.queue,
            # A failed file whose content changed gets another chance;
            # any other status is left alone.
            "status": case(
                (
                    (FileRecord.status == "failed") & (FileRecord.sha256 != f.sha256),
                    "discovered",
                ),
                else_=FileRecord.status,
            ),
        },
    )
    session.execute(stmt)


def _reconcile_statuses(session: Session) -> None:
    # Reconcile status with routing. Only touches "discovered"/"unsupported"
    # rows, so a converted or failed file is never reset by a rescan; a file
    # that becomes convertible later (e.g. vision gets implemented) flips back.
    session.execute(
        update(FileRecord)
        .where(FileRecord.queue.not_in(RUNNABLE_QUEUES), FileRecord.status == "discovered")
        .values(status="unsupported")
    )
    session.execute(
        update(FileRecord)
        .where(FileRecord.queue.in_(RUNNABLE_QUEUES), FileRecord.status == "unsupported")
        .values(status="discovered")
    )


def _load_known(session: Session) -> tuple[dict[str, KnownFile], dict[str, str]]:
    """What the DB already knows about each file: lets the scan skip
    re-reading files whose size and mtime haven't moved, and spot files
    whose content changed. Returns ({path: KnownFile}, {path: status})."""
    known: dict[str, KnownFile] = {}
    statuses: dict[str, str] = {}
    rows = session.execute(
        select(
            FileRecord.path,
            FileRecord.size_bytes,
            FileRecord.mtime_ns,
            FileRecord.mime_type,
            FileRecord.sha256,
            FileRecord.status,
        )
    ).all()
    for path, size_bytes, mtime_ns, mime_type, sha256, status in rows:
        known[path] = KnownFile(size_bytes=size_bytes, mtime_ns=mtime_ns, mime_type=mime_type, sha256=sha256)
        statuses[path] = status
    return known, statuses


def changed_paths(
    known: dict[str, KnownFile], statuses: dict[str, str], scanned: list[ScannedFile]
) -> list[str]:
    """Files already processed (or failed) whose content hash is now
    different. Unsupported files have no hash, and unprocessed files will
    be picked up anyway, so neither counts as a change."""
    changed = []
    for f in scanned:
        old = known.get(f.path)
        if (
            old is not None
            and old.sha256
            and f.sha256
            and old.sha256 != f.sha256
            and statuses.get(f.path) in _PROCESSED_STATUSES
        ):
            changed.append(f.path)
    return changed


def _reprocess_changed(session: Session, paths: list[str]) -> None:
    """Send files whose content changed back through the pipeline: drop the
    chunks built from the old version (they'd otherwise stay searchable and
    be wrong), reset the status, and clear queue claims so they're queued on
    the very next step instead of waiting out the dedupe window."""
    if not paths:
        return
    ids = list(session.execute(select(FileRecord.id).where(FileRecord.path.in_(paths))).scalars())
    session.execute(delete(ChunkRecord).where(ChunkRecord.file_id.in_(ids)))
    session.execute(update(FileRecord).where(FileRecord.id.in_(ids)).values(status="discovered"))
    clear_enqueued(ids)


def run_scan(session: Session) -> dict:
    raw_dir = Path(settings.data_dir) / "raw"
    known, statuses = _load_known(session)
    scanned = list(scan_directory(raw_dir, known))

    for f in scanned:
        _upsert_file(session, f)

    _reconcile_statuses(session)
    changed = changed_paths(known, statuses, scanned)
    _reprocess_changed(session, changed)

    # jobs and chunks go with the file (ON DELETE CASCADE), so a deleted
    # file's failed job stops flagging its folder.
    current = set(session.execute(select(FileRecord.path)).scalars())
    stale = paths_to_prune(current, {f.path for f in scanned})
    if stale:
        session.execute(delete(FileRecord).where(FileRecord.path.in_(stale)))
    session.commit()

    result = summarize(scanned)
    return {
        "raw_dir": str(raw_dir),
        "total_files": result.total_files,
        "total_bytes": result.total_bytes,
        "by_queue": result.by_queue,
        "by_mime": result.by_mime,
        "changed": len(changed),
    }


def register_files(session: Session, paths: list[Path]) -> list[int]:
    """Hash, classify and upsert just these files -- the upload endpoint's
    fast path. run_scan walks the whole corpus, which grows with every file
    ever ingested; registering a handful of uploads shouldn't cost that.
    Returns their file ids."""
    scanned = [scan_file(p) for p in paths]
    for f in scanned:
        _upsert_file(session, f)
    _reconcile_statuses(session)
    session.commit()
    return list(
        session.execute(select(FileRecord.id).where(FileRecord.path.in_([f.path for f in scanned]))).scalars()
    )


def run_convert_enqueue(
    session: Session, file_ids: list[int] | None = None, queue: str | None = None
) -> dict[str, int]:
    """Queue conversion for every "discovered" file, or -- when file_ids is
    given -- only for those (the upload path, which also passes `queue` to
    use its dedicated lane). A file already queued within the dedupe window
    is skipped; the counts are of tasks actually queued."""
    from app.tasks.convert import convert_email_archive, convert_fast, convert_vision

    task_by_queue = {
        "convert_fast": convert_fast,
        "convert_email_archive": convert_email_archive,
        "convert_vision": convert_vision,
    }

    enqueued: dict[str, int] = {}
    for file_queue, task in task_by_queue.items():
        query = select(FileRecord.id).where(FileRecord.queue == file_queue, FileRecord.status == "discovered")
        if file_ids is not None:
            query = query.where(FileRecord.id.in_(file_ids))
        rows = session.execute(query).scalars().all()
        enqueued[file_queue] = sum(1 for file_id in rows if enqueue_once(task, file_id, queue=queue))

    return enqueued


def run_summarize_enqueue(session: Session) -> int:
    from app.tasks.summarize import summarize_file

    # Skip files whose summarize job is already running; queued-but-not-
    # started duplicates are caught by the queue guard and, as a backstop,
    # at the start of summarize_file. Only recent jobs count: a worker
    # killed mid-job (restart, rebuild) leaves its row "running" forever,
    # and that must not block the file for good.
    summarizing = exists().where(
        JobRecord.file_id == FileRecord.id,
        JobRecord.job_type == "summarize",
        JobRecord.state == "running",
        JobRecord.created_at > datetime.now(timezone.utc) - _RUNNING_JOB_MAX_AGE,
    )
    rows = session.execute(
        select(FileRecord.id).where(FileRecord.status == "converted", ~summarizing)
    ).scalars().all()

    return sum(1 for file_id in rows if enqueue_once(summarize_file, file_id))


def run_index_enqueue(session: Session) -> int:
    """Enqueue index_file for every converted/summarized file with no chunks
    yet. A file that already has an index job running or done is skipped
    too, so an empty document (zero chunks) isn't re-enqueued every cycle."""
    from app.tasks.index import index_file

    has_chunks = exists().where(ChunkRecord.file_id == FileRecord.id)
    has_index_job = exists().where(
        JobRecord.file_id == FileRecord.id,
        JobRecord.job_type == "index",
        JobRecord.state.in_(("running", "done")),
    )
    rows = session.execute(
        select(FileRecord.id).where(
            FileRecord.status.in_(("converted", "summarized")),
            ~has_chunks,
            ~has_index_job,
        )
    ).scalars().all()

    return sum(1 for file_id in rows if enqueue_once(index_file, file_id))


def run_retry_failed(session: Session) -> int:
    """Puts every failed file on a runnable queue back to "discovered" so the
    next convert enqueue retries it. A file is only parked as "failed" after
    its retries run out, and nothing else clears that, so this is the way to
    retry after fixing the cause (e.g. a missing system dependency)."""
    ids = list(
        session.execute(
            update(FileRecord)
            .where(FileRecord.status == "failed", FileRecord.queue.in_(RUNNABLE_QUEUES))
            .values(status="discovered")
            .returning(FileRecord.id)
        ).scalars()
    )
    session.commit()
    clear_enqueued(ids)
    return len(ids)
