"""Full reports over a big folder run for minutes. Running one inside the HTTP
request ties it to the browser tab: close the tab and the work is lost. Here
the request instead hands the report to a worker (the `report` queue) and
follows its progress; the worker records the finished report in History
whether or not anyone is still watching.

  request:  create_job -> queue the task -> follow_job (progress events, then the report)
  worker:   execute_job (runs the same report code the request would have run)

The job's state lives in the report_jobs table (ReportJobRecord). If no worker
picks the job up in time, the request runs the report itself, as it did before.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import ReportJobRecord

QUEUED, RUNNING, DONE, FAILED, CANCELLED = "queued", "running", "done", "failed", "cancelled"

WAITING_TEXT = "Waiting for a report worker…"
STOPPED_TEXT = "The report was stopped."

# how often a running job is checked for a stop request while it streams text
_CANCEL_CHECK_EVERY_TOKENS = 25


@dataclass
class JobView:
    id: str
    state: str
    params: dict = field(default_factory=dict)
    status_text: str = ""
    answer: str = ""
    sources: list = field(default_factory=list)
    error: str | None = None
    history_id: int | None = None
    cancel_requested: bool = False


class DbJobStore:
    """report_jobs rows. Every call commits, so the other side (request or worker) sees it at once."""

    def __init__(self, session: Session):
        self._session = session

    def create(self, params: dict) -> str:
        job_id = uuid.uuid4().hex
        self._session.add(ReportJobRecord(id=job_id, state=QUEUED, params=params))
        self._session.commit()
        return job_id

    def get(self, job_id: str) -> JobView | None:
        row = self._session.execute(select(ReportJobRecord).where(ReportJobRecord.id == job_id)).scalar_one_or_none()
        view = (
            None
            if row is None
            else JobView(
                row.id, row.state, row.params, row.status_text, row.answer, row.sources, row.error,
                row.history_id, row.cancel_requested,
            )
        )
        self._session.rollback()  # end the read transaction: a polling loop must not sit "idle in transaction"
        return view

    def _update(self, job_id: str, *where, **values) -> bool:
        result = self._session.execute(
            update(ReportJobRecord).where(ReportJobRecord.id == job_id, *where).values(**values)
        )
        self._session.commit()
        return result.rowcount > 0

    def claim(self, job_id: str) -> bool:
        """queued -> running; False when it was cancelled, or already taken."""
        return self._update(job_id, ReportJobRecord.state == QUEUED, state=RUNNING)

    def cancel_if_queued(self, job_id: str) -> bool:
        return self._update(job_id, ReportJobRecord.state == QUEUED, state=CANCELLED)

    def request_cancel(self, job_id: str) -> bool:
        """Stop a queued job at once, or ask a running one to stop at its next check."""
        if self.cancel_if_queued(job_id):
            return True
        return self._update(job_id, ReportJobRecord.state == RUNNING, cancel_requested=True)

    def set_status(self, job_id: str, text: str) -> None:
        self._update(job_id, status_text=text)

    def mark_cancelled(self, job_id: str) -> None:
        self._update(job_id, state=CANCELLED)

    def finish(self, job_id: str, answer: str, sources: list, history_id: int | None) -> None:
        self._update(job_id, state=DONE, answer=answer, sources=sources, history_id=history_id)

    def fail(self, job_id: str, error: str) -> None:
        self._update(job_id, state=FAILED, error=error)


# ---------- the worker's side ----------

def execute_job(
    store,
    job_id: str,
    make_events: Callable[[dict], Iterable[dict]],
    record_history: Callable[[dict, dict], int | None],
) -> None:
    """Runs one job: `make_events(params)` is the report's event stream (status /
    meta / token / extra, as the request would have streamed it); the finished
    report goes to `record_history(params, result)`, which returns the History row's id."""
    job = store.get(job_id)
    if job is None or not store.claim(job_id):
        return  # gone, stopped while it waited, or taken by another worker

    answer_parts: list[str] = []
    meta: dict = {}
    events = make_events(job.params)
    try:
        tokens = 0
        for evt in events:
            kind = evt["event"]
            if kind == "status":
                store.set_status(job_id, evt["data"]["text"])
            elif kind == "token":
                answer_parts.append(evt["data"]["text"])
                tokens += 1
            elif kind == "meta":
                meta = evt["data"]
            if kind == "status" or tokens >= _CANCEL_CHECK_EVERY_TOKENS:
                tokens = 0
                current = store.get(job_id)
                if current is None or current.cancel_requested:
                    getattr(events, "close", lambda: None)()
                    store.mark_cancelled(job_id)
                    return

        answer = "".join(answer_parts)
        sources = meta.get("sources", [])
        result = {"answer": answer, "sources": sources, "grounded": meta.get("grounded", False)}
        history_id = record_history(job.params, result)
        store.finish(job_id, answer, sources, history_id)
    except Exception as exc:
        store.fail(job_id, str(exc) or exc.__class__.__name__)


# ---------- the request's side ----------

def _status(text: str) -> dict:
    return {"event": "status", "data": {"text": text}}


def follow_job(
    store,
    job_id: str,
    *,
    poll_seconds: float = 2.0,
    queue_wait_seconds: float = 45.0,
    keepalive_seconds: float = 15.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Iterator[dict]:
    """The events of a job the request is watching: its progress as "status" events, then
    the finished report (meta, token, extra) and a "recorded" event carrying the
    History id the worker saved it under. Returns False -- having cancelled the
    job -- if no worker took it within `queue_wait_seconds`, so the caller runs the
    report itself; True otherwise."""
    started = last_emit = clock()
    shown = ""
    yield {"event": "job", "data": {"job_id": job_id}}

    while True:
        job = store.get(job_id)
        if job is None:
            raise RuntimeError("The report job disappeared.")

        if job.state == QUEUED:
            if clock() - started >= queue_wait_seconds and store.cancel_if_queued(job_id):
                return False
            text = WAITING_TEXT
        elif job.state == RUNNING:
            text = job.status_text
        elif job.state == DONE:
            yield {"event": "meta", "data": {"sources": job.sources, "grounded": True}}
            yield {"event": "token", "data": {"text": job.answer}}
            yield {"event": "extra", "data": {"cross_doc": None, "statistical": None}}
            yield {"event": "recorded", "data": {"history_id": job.history_id}}
            return True
        elif job.state == FAILED:
            raise RuntimeError(job.error or "The report failed.")
        else:  # cancelled
            yield {"event": "meta", "data": {"sources": [], "grounded": False}}
            yield {"event": "token", "data": {"text": STOPPED_TEXT}}
            yield {"event": "extra", "data": {"cross_doc": None, "statistical": None}}
            return True

        if (text and text != shown) or clock() - last_emit >= keepalive_seconds:
            shown = text or shown
            last_emit = clock()
            yield _status(shown)
        sleep(poll_seconds)
