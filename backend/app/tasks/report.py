"""Runs a queued full report (see app/search/report_jobs.py) on the `report`
queue, so it keeps going when the browser that asked for it is closed.
"""

from types import SimpleNamespace

from app.celery_app import celery_app
from app.db import SessionLocal


@celery_app.task(name="app.tasks.report.run_report_job")
def run_report_job(job_id: str) -> None:
    from app.history.store import record_query
    from app.search.report_jobs import DbJobStore, execute_job
    from app.search.report_runner import report_stream

    session = SessionLocal()
    try:

        def make_events(params: dict):
            # background=False: this IS the worker; it must not queue the report again
            return report_stream(session, SimpleNamespace(**params), background=False)

        def record_history(params: dict, result: dict) -> int:
            return record_query(
                session,
                question=params["question"],
                result=result,
                folders=params.get("folders"),
                author=params.get("author"),
                title=params.get("title"),
                chat_only=params.get("chat_only", False),
                conversation_id=params.get("conversation_id", ""),
                route="report",
            )

        execute_job(DbJobStore(session), job_id, make_events, record_history)
    finally:
        session.close()
