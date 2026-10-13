import pytest

from app.search.report_jobs import (
    CANCELLED,
    DONE,
    FAILED,
    QUEUED,
    RUNNING,
    STOPPED_TEXT,
    WAITING_TEXT,
    JobView,
    execute_job,
    follow_job,
)


class MemoryStore:
    """report_jobs in memory, with the same rules as DbJobStore."""

    def __init__(self, params=None):
        self.job = JobView("j1", QUEUED, params or {"question": "report on Asha"})
        self.on_get = None  # hook: runs at every get(), to play the other side

    def get(self, job_id):
        if self.on_get:
            self.on_get(self)
        return None if self.job is None else JobView(**self.job.__dict__)

    def claim(self, job_id):
        if self.job.state != QUEUED:
            return False
        self.job.state = RUNNING
        return True

    def cancel_if_queued(self, job_id):
        if self.job.state != QUEUED:
            return False
        self.job.state = CANCELLED
        return True

    def set_status(self, job_id, text):
        self.job.status_text = text

    def mark_cancelled(self, job_id):
        self.job.state = CANCELLED

    def finish(self, job_id, answer, sources, history_id):
        self.job.state, self.job.answer, self.job.sources, self.job.history_id = DONE, answer, sources, history_id

    def fail(self, job_id, error):
        self.job.state, self.job.error = FAILED, error


def _events(*texts, status="Reading…"):
    yield {"event": "status", "data": {"text": status}}
    yield {"event": "meta", "data": {"sources": ["/a.pdf"], "grounded": True}}
    for t in texts:
        yield {"event": "token", "data": {"text": t}}
    yield {"event": "extra", "data": {"cross_doc": None, "statistical": None}}


# ---------- the worker ----------

def test_a_job_runs_the_report_and_records_it_in_history():
    store = MemoryStore()
    recorded = {}

    def record(params, result):
        recorded.update(params=params, result=result)
        return 77

    execute_job(store, "j1", lambda params: _events("The ", "report."), record)

    assert store.job.state == DONE and store.job.answer == "The report." and store.job.history_id == 77
    assert recorded["result"] == {"answer": "The report.", "sources": ["/a.pdf"], "grounded": True}
    assert store.job.status_text == "Reading…"


def test_a_job_that_is_not_queued_is_left_alone():
    store = MemoryStore()
    store.job.state = CANCELLED

    execute_job(store, "j1", lambda params: pytest.fail("must not run"), lambda p, r: 1)

    assert store.job.state == CANCELLED


def test_a_failing_report_marks_the_job_failed():
    def boom(params):
        yield {"event": "status", "data": {"text": "x"}}
        raise RuntimeError("model down")

    store = MemoryStore()
    execute_job(store, "j1", boom, lambda p, r: 1)

    assert store.job.state == FAILED and store.job.error == "model down"


def test_a_stop_request_ends_the_job_without_recording_it():
    store = MemoryStore()
    closed = []

    def events(params):
        try:
            yield {"event": "status", "data": {"text": "one"}}
            store.job.cancel_requested = True
            yield {"event": "status", "data": {"text": "two"}}
            pytest.fail("kept going after the stop request")
        finally:
            closed.append(True)

    execute_job(store, "j1", events, lambda p, r: pytest.fail("a stopped report is not recorded"))

    assert store.job.state == CANCELLED and closed == [True]


# ---------- the request following it ----------

def _follow(store, **kw):
    gen = follow_job(store, "j1", sleep=lambda s: None, **kw)
    events = []
    try:
        while True:
            events.append(next(gen))
    except StopIteration as stop:
        return events, stop.value


def test_following_a_job_shows_its_progress_then_the_finished_report():
    store = MemoryStore()
    steps = iter([(RUNNING, "Reading… 1 of 3"), (RUNNING, "Reading… 2 of 3")])

    def play(s):
        step = next(steps, None)
        if step:
            s.job.state, s.job.status_text = step
        elif s.job.state == RUNNING:
            s.job.state, s.job.answer, s.job.sources, s.job.history_id = DONE, "The report.", ["/a.pdf"], 9

    store.on_get = play
    events, handled = _follow(store)

    kinds = [e["event"] for e in events]
    assert kinds[0] == "job" and events[0]["data"] == {"job_id": "j1"}
    assert [e["data"]["text"] for e in events if e["event"] == "status"] == ["Reading… 1 of 3", "Reading… 2 of 3"]
    assert kinds[-4:] == ["meta", "token", "extra", "recorded"]
    assert events[-1]["data"] == {"history_id": 9} and events[-3]["data"] == {"text": "The report."}
    assert handled is True


def test_a_job_nobody_picks_up_is_cancelled_so_the_request_can_run_it_itself():
    store = MemoryStore()
    now = [0.0]

    def tick(s):
        now[0] += 20

    store.on_get = tick
    events, handled = _follow(store, queue_wait_seconds=45, clock=lambda: now[0])

    assert handled is False and store.job.state == CANCELLED
    assert [e["data"]["text"] for e in events if e["event"] == "status"][0] == WAITING_TEXT


def test_a_job_that_failed_raises_its_error():
    store = MemoryStore()
    store.job.state, store.job.error = FAILED, "model down"

    with pytest.raises(RuntimeError, match="model down"):
        _follow(store)


def test_a_stopped_job_ends_with_a_stopped_message():
    store = MemoryStore()
    store.job.state = CANCELLED

    events, handled = _follow(store)

    assert handled is True
    assert [e["data"]["text"] for e in events if e["event"] == "token"] == [STOPPED_TEXT]


def test_a_long_silent_job_keeps_the_connection_alive():
    store = MemoryStore()
    store.job.state, store.job.status_text = RUNNING, "Reading…"
    now = [0.0]
    count = [0]

    def tick(s):
        now[0] += 6
        count[0] += 1
        if count[0] > 8:
            s.job.state, s.job.answer = DONE, "x"

    store.on_get = tick
    events, _ = _follow(store, keepalive_seconds=15, clock=lambda: now[0])

    assert sum(1 for e in events if e["event"] == "status") >= 3  # the text once, then a keepalive every ~15s
