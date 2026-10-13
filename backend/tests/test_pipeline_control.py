from app import pipeline_control


class FakeRedis:
    def __init__(self, keys=()):
        self.data = {key: 1 for key in keys}

    def set(self, key, value):
        self.data[key] = value

    def exists(self, key):
        return 1 if key in self.data else 0

    def delete(self, *keys):
        return sum(1 for key in keys if self.data.pop(key, None) is not None)

    def scan_iter(self, match, count):
        prefix = match.rstrip("*")
        return [key for key in list(self.data) if key.startswith(prefix)]


def test_stop_sets_the_flag_empties_the_queues_and_forgets_the_queue_claims(monkeypatch):
    redis = FakeRedis(["enqueued:convert:1", "enqueued:convert:2", "unrelated"])
    monkeypatch.setattr(pipeline_control, "purge_queues", lambda: 42)

    result = pipeline_control.stop(redis)

    assert result == {"paused": True, "purged": 42, "claims_cleared": 2}
    assert pipeline_control.is_paused(redis)
    assert "unrelated" in redis.data  # only the queue claims go


def test_resume_clears_the_flag():
    redis = FakeRedis([pipeline_control.PAUSE_KEY])
    pipeline_control.resume(redis)
    assert not pipeline_control.is_paused(redis)


def test_an_unreachable_redis_means_not_paused():
    class Broken:
        def exists(self, key):
            raise ConnectionError("down")

    assert pipeline_control.is_paused(Broken()) is False


def test_the_report_queue_is_never_purged():
    assert "report" not in pipeline_control.PIPELINE_QUEUES


def test_manual_ingest_calls_are_refused_while_stopped_but_not_otherwise(monkeypatch):
    import pytest
    from fastapi import HTTPException

    from app.main import _refuse_while_stopped

    monkeypatch.setattr(pipeline_control, "is_paused", lambda client=None: True)
    with pytest.raises(HTTPException) as refused:
        _refuse_while_stopped()
    assert refused.value.status_code == 409

    monkeypatch.setattr(pipeline_control, "is_paused", lambda client=None: False)
    _refuse_while_stopped()  # does not raise
