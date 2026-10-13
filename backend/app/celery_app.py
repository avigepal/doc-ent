from celery import Celery

from app.config import settings

celery_app = Celery("pipeline", broker=settings.redis_url, backend=settings.redis_url)

celery_app.conf.task_routes = {
    "app.tasks.convert.*": {"queue": "convert_fast"},
    "app.tasks.summarize.*": {"queue": "summarize"},
    "app.tasks.correlate.*": {"queue": "correlate"},
    # shares the correlate worker, which already reaches the embedding server
    "app.tasks.index.*": {"queue": "correlate"},
    "app.tasks.auto_ingest.*": {"queue": "auto_ingest"},
    "app.tasks.report.*": {"queue": "report"},
}

# Queue names must match docker-compose.yml's celery-* service `-Q` flags.
celery_app.conf.task_default_queue = "convert_fast"

if settings.auto_ingest_interval_seconds > 0:
    celery_app.conf.beat_schedule = {
        "auto-ingest": {
            "task": "app.tasks.auto_ingest.auto_ingest_cycle",
            "schedule": float(settings.auto_ingest_interval_seconds),
            # While one cycle runs (a first scan of a big folder takes a long time) the
            # timer keeps queuing more behind it. One that waited a full interval is
            # dropped instead of piling up: the next tick covers it.
            "options": {"expires": float(settings.auto_ingest_interval_seconds)},
        },
    }

import app.tasks.convert  # noqa: E402,F401
import app.tasks.summarize  # noqa: E402,F401
import app.tasks.correlate  # noqa: E402,F401
import app.tasks.index  # noqa: E402,F401
import app.tasks.auto_ingest  # noqa: E402,F401
import app.tasks.report  # noqa: E402,F401
import app.job_cleanup  # noqa: E402,F401
