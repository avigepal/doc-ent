"""Auto-ingest: the periodic version of clicking Scan -> Convert ->
Summarize on the dashboard yourself. Runs on a Celery Beat schedule
(see app/celery_app.py's beat_schedule) so dropping files into raw/ gets
picked up without anyone calling the API.

Polling (not inotify/watchdog filesystem events) on purpose: raw/ may
end up being a mount point to a NAS/network share rather than local
disk (an open question in the plan), and filesystem events are
unreliable over network filesystems — a timer isn't.
"""

from app.celery_app import celery_app
from app.config import settings
from app.db import SessionLocal
from app.pipeline_runner import run_convert_enqueue, run_index_enqueue, run_scan, run_summarize_enqueue


@celery_app.task(name="app.tasks.auto_ingest.auto_ingest_cycle")
def auto_ingest_cycle() -> dict:
    session = SessionLocal()
    try:
        scan_result = run_scan(session)
        convert_enqueued = run_convert_enqueue(session)
        # summarization is optional: it's the slowest stage and search
        # doesn't depend on it (AUTO_SUMMARIZE=false skips it here)
        summarize_enqueued = run_summarize_enqueue(session) if settings.auto_summarize else 0
        index_enqueued = run_index_enqueue(session)
        return {
            "scanned": scan_result["total_files"],
            "convert_enqueued": convert_enqueued,
            "summarize_enqueued": summarize_enqueued,
            "index_enqueued": index_enqueued,
        }
    finally:
        session.close()
