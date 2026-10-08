"""Close out jobs a killed worker left behind.

A worker that dies mid-task (restart, rebuild, crash) never gets to update
its job row, so it stays "running" forever -- the Progress page and folder
spinners then show a file as processing indefinitely. When a worker starts,
anything still marked running for the job types it handles was necessarily
orphaned by its predecessor, so it's marked "interrupted" (a state the
status views ignore, unlike "failed", which would flag the folder red).

Assumes one worker per queue, as in docker-compose.yml: with two workers on
the same queue, restarting one would wrongly close the other's live jobs.
"""

from __future__ import annotations

import logging

from celery.signals import worker_ready
from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import JobRecord
from app.queue_guard import clear_enqueued

logger = logging.getLogger(__name__)

# Which job types each queue's worker runs. All convert.* tasks are routed
# to the convert_fast queue and index tasks to correlate (see celery_app.py).
QUEUE_JOB_TYPES: dict[str, tuple[str, ...]] = {
    "convert_fast": ("convert_fast", "convert_email_archive", "convert_vision", "convert_ocr"),
    "summarize": ("summarize",),
    "correlate": ("index",),
    # the upload lane's worker runs both stages itself
    "upload": ("convert_fast", "convert_email_archive", "convert_vision", "index"),
}


def job_types_for_queues(queues: list[str]) -> list[str]:
    types: list[str] = []
    for queue in queues:
        for job_type in QUEUE_JOB_TYPES.get(queue, ()):
            if job_type not in types:
                types.append(job_type)
    return types


def mark_orphaned_jobs(session: Session, queues: list[str]) -> int:
    """Mark still-"running" jobs that belonged to these queues' workers as
    interrupted, and release their queue claims so the next auto-ingest tick
    can re-queue the files (the claims would otherwise block that for the
    dedupe window). Matches on the queue the job ran on; jobs recorded
    before that column existed (NULL) fall back to matching by job type.
    Returns how many jobs were closed."""
    closed: list[int] = []
    for queue in queues:
        job_types = list(QUEUE_JOB_TYPES.get(queue, ()))
        if not job_types:
            continue
        rows = session.execute(
            update(JobRecord)
            .where(
                JobRecord.state == "running",
                JobRecord.job_type.in_(job_types),
                or_(JobRecord.queue == queue, JobRecord.queue.is_(None)),
            )
            .values(state="interrupted", error="worker restarted before the job finished")
            .returning(JobRecord.file_id)
        ).all()
        closed.extend(file_id for (file_id,) in rows)
    session.commit()
    clear_enqueued(sorted(set(closed)))
    return len(closed)


@worker_ready.connect
def _recover_orphaned_jobs(sender=None, **_) -> None:
    """Runs once when a worker finishes starting. Best effort: a failure
    here must never keep the worker from starting."""
    try:
        queues = [q.name for q in sender.task_consumer.queues]
        session = SessionLocal()
        try:
            closed = mark_orphaned_jobs(session, queues)
        finally:
            session.close()
        if closed:
            logger.warning("closed %d job(s) left running by a previous worker", closed)
    except Exception:
        logger.warning("could not clean up orphaned jobs", exc_info=True)
