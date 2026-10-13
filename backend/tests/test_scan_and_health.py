import json
import threading
import time

import httpx

from app import scan_status
from app.conversion import docling_health
from app.ingestion.walker import scan_directory


class FakeRedis:
    def __init__(self):
        self.data = {}

    def set(self, key, value, ex=None):
        self.data[key] = value

    def get(self, key):
        return self.data.get(key)


# ---------- scan status ----------


def test_scan_status_follows_a_scan_from_begin_to_finish():
    redis = FakeRedis()
    assert scan_status.read(redis) is None

    scan_status.begin(redis)
    scan_status.update(400, redis)
    during = scan_status.read(redis)
    assert during["active"] is True and during["files_seen"] == 400

    scan_status.finish(450, redis)
    after = scan_status.read(redis)
    assert after["active"] is False and after["files_seen"] == 450 and after["finished_at"]


def test_a_scan_that_stopped_reporting_is_not_called_active():
    redis = FakeRedis()
    redis.data[scan_status.KEY] = json.dumps(
        {"active": True, "files_seen": 10, "started_at": "x", "updated_at": time.time() - scan_status.STALE_AFTER_S - 5}
    )
    assert scan_status.read(redis)["active"] is False


def test_an_unreachable_redis_gives_no_scan_status_and_does_not_break_a_scan():
    class Broken:
        def get(self, key):
            raise ConnectionError("down")

        def set(self, *args, **kwargs):
            raise ConnectionError("down")

    assert scan_status.read(Broken()) is None
    scan_status.begin(Broken())  # must not raise
    scan_status.finish(1, Broken())


# ---------- the walker keeps going past a file it can't read ----------


def test_one_unreadable_file_does_not_abort_the_scan(tmp_path, monkeypatch):
    for name in ("a.txt", "bad.txt", "c.txt"):
        (tmp_path / name).write_text(name)

    from app.ingestion import walker

    real = walker.scan_file

    def flaky(path, known=None):
        if path.name == "bad.txt":
            raise PermissionError("locked")
        return real(path, known)

    monkeypatch.setattr(walker, "scan_file", flaky)

    assert [f.path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for f in scan_directory(tmp_path)] == ["a.txt", "c.txt"]


# ---------- Docling health ----------


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_healthy_server_is_online_with_a_latency():
    result = docling_health.probe("http://gpu:5001/", "k", _client(lambda r: httpx.Response(200, json={"status": "ok"})))
    assert result["online"] is True and result["error"] is None and result["latency_ms"] >= 0


def test_the_probe_calls_health_with_the_key_and_never_returns_it():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200)

    result = docling_health.probe("http://gpu:5001", "secret", _client(handler))
    assert str(seen[0].url) == "http://gpu:5001/health" and seen[0].headers["x-api-key"] == "secret"
    assert "secret" not in json.dumps(result)


def test_each_way_a_server_can_be_down_is_named():
    def refused(request):
        raise httpx.ConnectError("refused")

    def slow(request):
        raise httpx.ReadTimeout("slow")

    assert "can't connect" in docling_health.probe("http://x", "", _client(refused))["error"]
    assert "timed out" in docling_health.probe("http://x", "", _client(slow))["error"]
    assert "API key" in docling_health.probe("http://x", "", _client(lambda r: httpx.Response(401)))["error"]
    assert "500" in docling_health.probe("http://x", "", _client(lambda r: httpx.Response(500)))["error"]
    assert docling_health.probe("http://x", "", _client(lambda r: httpx.Response(500)))["online"] is False


def test_docling_status_answers_at_once_and_refreshes_in_the_background(monkeypatch):
    calls = []
    release = threading.Event()  # holds the probe back so "nothing probed yet" can be observed

    def fake_probe(url, api_key="", client=None):
        calls.append(url)
        release.wait(2)
        return {"url": url, "online": True, "latency_ms": 5, "error": None, "checked_at": "now"}

    monkeypatch.setattr(docling_health, "probe", fake_probe)
    monkeypatch.setattr(docling_health, "_cache", {"at": 0.0, "value": None})
    monkeypatch.setattr(docling_health, "_refreshing", False)

    first = docling_health.docling_status()
    assert first["online"] is None  # nothing probed yet; the call did not wait
    release.set()

    deadline = time.time() + 2
    while docling_health.docling_status()["online"] is None and time.time() < deadline:
        time.sleep(0.01)
    assert docling_health.docling_status()["online"] is True
    assert len(calls) == 1  # fresh result: no second probe within the TTL
