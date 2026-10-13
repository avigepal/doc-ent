"""Live status of the file scan, for the Progress page's "Scanning… N files" line.

A scan of a big folder hashes every file (110 GB takes a long while), so the
page needs to say it is working. The scan writes its running count to Redis
as it goes. A scan that was killed halfway leaves "active" behind, so a status
that has not been updated for STALE_AFTER_S seconds is reported as not active.

Fail-open like the queue guard: Redis trouble must never stop a scan.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

KEY = "scan:status"
STALE_AFTER_S = 600
_TTL_S = 24 * 3600

_started_at: str | None = None


def _client(client=None):
    if client is not None:
        return client
    from app.queue_guard import _redis

    return _redis()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _write(payload: dict, client=None) -> None:
    try:
        _client(client).set(KEY, json.dumps(payload), ex=_TTL_S)
    except Exception:
        logger.warning("could not write the scan status", exc_info=True)


def begin(client=None) -> None:
    global _started_at
    _started_at = _now().isoformat()
    _write({"active": True, "files_seen": 0, "started_at": _started_at, "updated_at": time.time()}, client)


def update(files_seen: int, client=None) -> None:
    _write(
        {"active": True, "files_seen": files_seen, "started_at": _started_at, "updated_at": time.time()},
        client,
    )


def finish(files_seen: int, client=None) -> None:
    _write(
        {
            "active": False,
            "files_seen": files_seen,
            "started_at": _started_at,
            "finished_at": _now().isoformat(),
            "updated_at": time.time(),
        },
        client,
    )


def read(client=None) -> dict | None:
    """The last scan's status, or None if there has been none (or Redis is unreachable)."""
    try:
        raw = _client(client).get(KEY)
    except Exception:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if data.get("active") and time.time() - data.get("updated_at", 0) > STALE_AFTER_S:
        data["active"] = False  # the scan died without saying so
    data.pop("updated_at", None)
    return data
