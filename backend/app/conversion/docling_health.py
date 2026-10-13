"""Is the Docling server answering? Backs the status badge on the Progress page.

An outage otherwise only shows up as a pile of failed files. The page polls every
few seconds, and a dead server can take seconds to time out, so the probe runs in a
background thread: docling_status() always answers at once with the last result,
and starts a refresh when that result is older than TTL_S.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

import httpx

TTL_S = 10.0
PROBE_TIMEOUT_S = 3.0

_cache: dict = {"at": 0.0, "value": None}
_refreshing = False
_lock = threading.Lock()


def probe(url: str, api_key: str = "", client: httpx.Client | None = None) -> dict:
    """One call to the server's /health. Never raises; the result says what happened.
    The API key is sent but never returned."""
    started = time.monotonic()
    online, error = False, None
    headers = {"X-Api-Key": api_key} if api_key else {}
    try:
        http = client or httpx.Client(timeout=PROBE_TIMEOUT_S)
        response = http.get(url.rstrip("/") + "/health", headers=headers)
        if response.status_code in (401, 403):
            error = "the server rejected the API key"
        elif response.status_code >= 400:
            error = f"the server answered {response.status_code}"
        else:
            online = True
    except httpx.TimeoutException:
        error = "no answer (timed out)"
    except httpx.HTTPError as exc:
        error = f"can't connect ({exc.__class__.__name__})"
    return {
        "url": url,
        "online": online,
        "latency_ms": round((time.monotonic() - started) * 1000) if online else None,
        "error": error,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def _refresh() -> None:
    global _refreshing
    try:
        from app.config import settings

        value = probe(settings.docling_url, settings.docling_api_key)
        _cache["value"], _cache["at"] = value, time.monotonic()
    finally:
        _refreshing = False


def docling_status() -> dict:
    """The last probe of the configured server, at once. `online` is None until the first
    probe has finished. A refresh starts in the background when the result is stale."""
    global _refreshing
    stale = _cache["value"] is None or time.monotonic() - _cache["at"] >= TTL_S
    if stale:
        with _lock:
            if not _refreshing:
                _refreshing = True
                threading.Thread(target=_refresh, daemon=True).start()
    if _cache["value"] is not None:
        return _cache["value"]

    from app.config import settings

    return {"url": settings.docling_url, "online": None, "latency_ms": None, "error": None, "checked_at": None}
