import pytest

from app.ingestion.upload_status import TERMINAL_STAGES, upload_stage
from app import queue_guard


@pytest.mark.parametrize(
    "status, chunks, jobs, expected",
    [
        ("unsupported", 0, {}, "unsupported"),
        ("discovered", 0, {}, "queued"),
        ("discovered", 0, {"convert_fast": ("running", None)}, "converting"),
        ("converted", 0, {"convert_fast": ("done", None)}, "indexing"),
        ("converted", 0, {"index": ("running", None)}, "indexing"),
        ("converted", 5, {"index": ("done", None)}, "ready"),
        ("summarized", 7, {}, "ready"),
        ("failed", 0, {"convert_fast": ("failed", "boom")}, "failed"),
    ],
)
def test_upload_stage(status, chunks, jobs, expected):
    assert upload_stage(status, chunks, jobs)[0] == expected


def test_failed_stage_carries_the_conversion_error():
    stage, detail = upload_stage("failed", 0, {"convert_fast": ("failed", "no text extracted")})
    assert (stage, detail) == ("failed", "no text extracted")


def test_indexed_with_no_chunks_is_ready_but_says_so():
    stage, detail = upload_stage("converted", 0, {"index": ("done", None)})
    assert stage == "ready"
    assert "No searchable text" in detail


def test_polling_stops_only_on_terminal_stages():
    assert set(TERMINAL_STAGES) == {"ready", "failed", "unsupported"}
    assert "converting" not in TERMINAL_STAGES and "indexing" not in TERMINAL_STAGES


class _Redis:
    def __init__(self):
        self.keys = set()

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.keys:
            return None
        self.keys.add(key)
        return True


class _Task:
    name = "app.tasks.convert.convert_fast"

    def __init__(self):
        self.calls = []

    def delay(self, file_id):
        self.calls.append(("delay", file_id))

    def apply_async(self, args, queue):
        self.calls.append(("apply_async", args, queue))


def test_enqueue_once_uses_the_given_lane_when_a_queue_is_passed():
    task = _Task()
    queue_guard.enqueue_once(task, 4, ttl=60, client=_Redis(), queue=queue_guard.UPLOAD_QUEUE)
    assert task.calls == [("apply_async", [4], "upload")]


def test_enqueue_once_uses_default_routing_without_a_queue():
    task = _Task()
    queue_guard.enqueue_once(task, 4, ttl=60, client=_Redis())
    assert task.calls == [("delay", 4)]
