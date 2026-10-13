"""Runs a full report for a request: picks the documents (report_scope.py), then
either reads them right here or, for a report over a big folder, hands it to a
worker and follows its progress (report_jobs.py). Used by the /query/stream
endpoint and, with `background=False`, by the worker itself.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.search.report import ReportLimits, _nothing_to_report, refine_events, report_events
from app.search.report_jobs import DbJobStore, follow_job
from app.search.report_scope import resolve_report_scope

logger = logging.getLogger(__name__)


def report_limits() -> ReportLimits:
    return ReportLimits(
        batch_chars=settings.report_batch_chars,
        max_files=settings.report_max_files,
        max_chars=settings.report_max_chars,
        notes_chars=settings.report_notes_chars,
        parallel=settings.report_parallel,
    )


def report_scope(session: Session, payload, request: str, confirmed: bool):
    """Generator: status events, then returns the ReportScope -- which documents a
    full report reads. With nothing to read (a direct chat has no documents) the
    report events say so instead of the model chatting."""
    from app.tasks.correlate import _raw_dir, _text_llm

    return resolve_report_scope(
        session,
        raw_dir=_raw_dir,
        request=request,
        folders=payload.folders,
        author=payload.author,
        title=payload.title,
        file_ids=payload.file_ids,
        chat_only=payload.chat_only,
        llm=_text_llm,
        limits=report_limits(),
        subject_max_files=settings.report_subject_max_files,
        confirm_above=settings.report_confirm_above,
        seconds_per_call=settings.report_seconds_per_call,
        confirmed=confirmed,
    )


def job_params(payload) -> dict:
    return {
        "question": payload.question,
        "folders": list(payload.folders or []),
        "author": payload.author,
        "title": payload.title,
        "file_ids": list(payload.file_ids) if payload.file_ids else None,
        "chat_only": bool(payload.chat_only),
        "conversation_id": payload.conversation_id or "",
        "confirm_report": True,
    }


def _follow_in_background(session: Session, payload):
    """Generator: queue the report for a worker and follow it. Returns True when the
    worker ran it, False when none took it (the caller then runs the report itself)."""
    from app.tasks.report import run_report_job

    store = DbJobStore(session)
    job_id = store.create(job_params(payload))
    try:
        run_report_job.apply_async(args=[job_id], queue="report")
    except Exception:
        logger.warning("could not queue the report job; running it in the request", exc_info=True)
        store.cancel_if_queued(job_id)
        return False
    return (yield from follow_job(store, job_id, queue_wait_seconds=settings.report_queue_wait_seconds))


def report_stream(session: Session, payload, background: bool = True):
    """Events for a full report: every passage of the files in the request's
    scope is read, not just the best matches (in a folder too big for that, the
    passages that mention the subject of the request). Notes saved by an earlier
    report on the same request are reused. For a big folder the first answer is
    a plan (how many documents, how long) and the report waits for confirmation;
    once confirmed it runs in a worker."""
    from app.search.report_notes import DbNotesStore
    from app.tasks.correlate import _text_llm

    scope = yield from report_scope(session, payload, payload.question, payload.confirm_report)
    if scope.nothing:
        yield from _nothing_to_report(scope.nothing)
        return
    if scope.plan:
        yield {
            "event": "route",
            "data": {"action": "report_plan", "label": "", "query": payload.question, "source": "user"},
        }
        yield from _nothing_to_report(scope.plan)
        return
    if background and scope.narrowed and settings.report_background:
        if (yield from _follow_in_background(session, payload)):
            return
    yield from report_events(
        payload.question, scope.documents, _text_llm, scope.limits, DbNotesStore(session), scope.prompt_extra
    )


def report_refine_stream(session: Session, payload, request: str, previous_report: str):
    """The same report notes (saved ones, so nothing is read again) with the new instruction applied."""
    from app.search.report_notes import DbNotesStore
    from app.tasks.correlate import _text_llm

    # the report being changed was already confirmed, so the same documents are read again, without asking
    scope = yield from report_scope(session, payload, request, True)
    if scope.nothing:
        yield from _nothing_to_report(scope.nothing)
    else:
        yield from refine_events(
            request, payload.question, previous_report, scope.documents, _text_llm, scope.limits, DbNotesStore(session)
        )
