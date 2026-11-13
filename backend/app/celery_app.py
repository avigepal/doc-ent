from celery import Celery

from app.config import settings

celery_app = Celery("pipeline", broker=settings.redis_url, backend=settings.redis_url)

celery_app.conf.task_routes = {
    "app.tasks.convert.*": {"queue": "convert_fast"},
    "app.tasks.convert.convert_ocr": {"queue": "convert_ocr"},
    "app.tasks.summarize.*": {"queue": "summarize"},
    "app.tasks.correlate.*": {"queue": "correlate"},
    # shares the correlate worker, which already reaches the embedding server
    "app.tasks.index.*": {"queue": "correlate"},
    "app.tasks.report.*": {"queue": "report"},
}

# Queue names must match docker-compose.yml's celery-* service `-Q` flags.
celery_app.conf.task_default_queue = "convert_fast"

import app.tasks.convert  # noqa: E402,F401
import app.tasks.summarize  # noqa: E402,F401
import app.tasks.correlate  # noqa: E402,F401
import app.tasks.index  # noqa: E402,F401
import app.tasks.report  # noqa: E402,F401
import app.job_cleanup  # noqa: E402,F401
