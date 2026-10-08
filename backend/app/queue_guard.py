"""Queue each file once per stage.

The auto-ingest timer runs every minute and a conversion/summary/index takes
minutes, so without a guard the same file is queued again on every tick
while its first task is still waiting or running -- thousands of duplicate
tasks for a bulk load. enqueue_once() claims a short-lived Redis key per
(task, file) with SET NX; only the caller that wins the claim queues the
task.

Fails open: if Redis can't be reached the task is queued anyway. Duplicates
are harmless (the tasks skip work that's already done), so a Redis hiccup
must never stop ingestion.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Dedicated lane for files uploaded from the dashboard: its own worker, so a
# fresh upload never waits behind a bulk-ingest backlog on the shared queues.
UPLOAD_QUEUE = "upload"

# Every task enqueue_once() is used for, so a file can have all of its
# claims cleared at once (content changed, or a failed file is retried).
STAGE_TASK_NAMES = (
    "app.tasks.convert.convert_fast",
    "app.tasks.convert.convert_email_archive",
    "app.tasks.convert.convert_vision",
    "app.tasks.summarize.summarize_file",
    "app.tasks.index.index_file",
)

_client = None


def _redis():
    global _client
    if _client is None:
        import redis

        from app.config import settings

        _client = redis.Redis.from_url(settings.redis_url)
    return _client


def _key(task_name: str, file_id: int) -> str:
    return f"enqueued:{task_name}:{file_id}"


def enqueue_once(task, file_id: int, ttl: int | None = None, client=None, queue: str | None = None) -> bool:
    """Queue the task for file_id unless it was already queued within `ttl`
    seconds (default settings.queue_dedupe_seconds). `queue` overrides the
    task's normal routing (the upload lane). Returns True when the task was
    actually queued."""
    if ttl is None:
        from app.config import settings

        ttl = settings.queue_dedupe_seconds

    try:
        claimed = (client or _redis()).set(_key(task.name, file_id), 1, nx=True, ex=ttl)
    except Exception:
        logger.warning("queue guard unavailable; queueing %s for file %s anyway", task.name, file_id, exc_info=True)
        claimed = True

    if not claimed:
        return False
    if queue:
        task.apply_async(args=[file_id], queue=queue)
    else:
        task.delay(file_id)
    return True


def clear_enqueued(file_ids: list[int], client=None) -> None:
    """Forget every stage's claim for these files so they can be queued
    again right away (e.g. their content changed). Best effort."""
    if not file_ids:
        return
    keys = [_key(name, file_id) for file_id in file_ids for name in STAGE_TASK_NAMES]
    try:
        (client or _redis()).delete(*keys)
    except Exception:
        logger.warning("could not clear queue-guard keys", exc_info=True)
