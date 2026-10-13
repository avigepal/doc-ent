"""Stop / resume for the whole ingestion pipeline (the Progress page's button).

Stopping does three things: sets a flag the auto-ingest timer checks (so it
stops queuing work), empties the work queues, and forgets the "already
queued" claims (so Resume can queue the remaining files again). Files being
converted at that moment finish on their own; nothing is deleted and every
file keeps its status, so Resume simply carries on.

Redis holds the flag. If Redis can't be reached the pipeline is treated as
not stopped: a Redis hiccup must never freeze ingestion.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

PAUSE_KEY = "pipeline:paused"

# Every queue the pipeline feeds. "report" is left out: a report is a request
# someone is waiting on, not bulk ingestion.
PIPELINE_QUEUES = (
    "convert_fast",
    "convert_ocr",
    "convert_vision",
    "convert_email_archive",
    "upload",
    "summarize",
    "reduce",
    "correlate",
    "auto_ingest",
)


def _client(client=None):
    if client is not None:
        return client
    from app.queue_guard import _redis

    return _redis()


def is_paused(client=None) -> bool:
    try:
        return bool(_client(client).exists(PAUSE_KEY))
    except Exception:
        logger.warning("could not read the pause flag; treating the pipeline as running", exc_info=True)
        return False


def resume(client=None) -> None:
    _client(client).delete(PAUSE_KEY)


def clear_claims(client=None) -> int:
    """Forget every "this file is already queued" claim. Returns how many."""
    redis = _client(client)
    removed = 0
    batch: list = []
    for key in redis.scan_iter(match="enqueued:*", count=1000):
        batch.append(key)
        if len(batch) >= 1000:
            removed += redis.delete(*batch)
            batch = []
    if batch:
        removed += redis.delete(*batch)
    return removed


def purge_queues() -> int:
    """Drop every waiting task from the pipeline queues. Returns how many."""
    from app.celery_app import celery_app

    purged = 0
    with celery_app.connection_for_write() as connection:
        channel = connection.default_channel
        for queue in PIPELINE_QUEUES:
            purged += channel.queue_purge(queue) or 0
    return purged


def stop(client=None) -> dict:
    redis = _client(client)
    redis.set(PAUSE_KEY, 1)  # first, so the timer can't queue more while the queues are emptied
    purged = purge_queues()
    cleared = clear_claims(redis)
    return {"paused": True, "purged": purged, "claims_cleared": cleared}
