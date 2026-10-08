import hashlib
from pathlib import Path

import pytest

from app import queue_guard
from app.ingestion import walker
from app.ingestion.walker import KnownFile, ScannedFile, scan_directory, scan_file
from app.pipeline_runner import changed_paths


# ---------- queue guard ----------

class FakeRedis:
    def __init__(self):
        self.store = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)


class FakeTask:
    name = "app.tasks.convert.convert_fast"

    def __init__(self):
        self.queued = []

    def delay(self, file_id):
        self.queued.append(file_id)


def test_enqueue_once_queues_a_file_only_once_per_window():
    redis, task = FakeRedis(), FakeTask()

    assert queue_guard.enqueue_once(task, 7, ttl=60, client=redis) is True
    assert queue_guard.enqueue_once(task, 7, ttl=60, client=redis) is False
    assert queue_guard.enqueue_once(task, 8, ttl=60, client=redis) is True

    assert task.queued == [7, 8]


def test_enqueue_once_is_per_task():
    redis = FakeRedis()
    convert, other = FakeTask(), FakeTask()
    other.name = "app.tasks.index.index_file"

    queue_guard.enqueue_once(convert, 1, ttl=60, client=redis)
    assert queue_guard.enqueue_once(other, 1, ttl=60, client=redis) is True


def test_enqueue_once_fails_open_when_redis_is_down():
    class DownRedis:
        def set(self, *a, **k):
            raise ConnectionError("redis unreachable")

    task = FakeTask()

    assert queue_guard.enqueue_once(task, 3, ttl=60, client=DownRedis()) is True
    assert task.queued == [3]


def test_clear_enqueued_lets_a_file_be_queued_again():
    redis, task = FakeRedis(), FakeTask()
    queue_guard.enqueue_once(task, 5, ttl=60, client=redis)

    queue_guard.clear_enqueued([5], client=redis)

    assert queue_guard.enqueue_once(task, 5, ttl=60, client=redis) is True


# ---------- scan skips unchanged files ----------

def _known_for(path: Path, sha256: str, mime: str = "text/plain") -> KnownFile:
    stat = path.stat()
    return KnownFile(size_bytes=stat.st_size, mtime_ns=stat.st_mtime_ns, mime_type=mime, sha256=sha256)


def test_unchanged_file_is_not_read_again(tmp_path, monkeypatch):
    f = tmp_path / "a.txt"
    f.write_text("same content")
    known = _known_for(f, "stored-hash")

    def must_not_hash(*_a, **_k):
        raise AssertionError("an unchanged file must not be re-hashed")

    monkeypatch.setattr(walker, "_hash_file", must_not_hash)
    monkeypatch.setattr(walker, "_detect_mime", must_not_hash)

    result = scan_file(f, known)

    assert result.sha256 == "stored-hash"
    assert result.queue == "convert_fast"
    assert result.mtime_ns == f.stat().st_mtime_ns


def test_file_with_changed_size_is_hashed_again(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("new, longer content than before")
    stale = KnownFile(size_bytes=3, mtime_ns=f.stat().st_mtime_ns, mime_type="text/plain", sha256="old-hash")

    result = scan_file(f, stale)

    assert result.sha256 == hashlib.sha256(b"new, longer content than before").hexdigest()


def test_known_file_without_a_stored_hash_is_hashed(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("content")
    # e.g. previously "unsupported" (no hash) but now routable
    result = scan_file(f, _known_for(f, ""))

    assert result.sha256 == hashlib.sha256(b"content").hexdigest()


def test_scan_directory_passes_known_records_through(tmp_path, monkeypatch):
    f = tmp_path / "a.txt"
    f.write_text("x")
    monkeypatch.setattr(walker, "_hash_file", lambda *_a, **_k: pytest.fail("re-hashed"))

    [result] = list(scan_directory(tmp_path, {str(f): _known_for(f, "h")}))

    assert result.sha256 == "h"


# ---------- change detection ----------

def _scanned(path, sha):
    return ScannedFile(path=path, sha256=sha, mime_type="text/plain", size_bytes=1, queue="convert_fast")


def _known(sha):
    return KnownFile(size_bytes=1, mtime_ns=1, mime_type="text/plain", sha256=sha)


def test_changed_content_of_processed_file_is_detected():
    known = {"a": _known("old"), "b": _known("same"), "c": _known("old")}
    statuses = {"a": "summarized", "b": "converted", "c": "converted"}
    scanned = [_scanned("a", "new"), _scanned("b", "same"), _scanned("c", "new")]

    assert changed_paths(known, statuses, scanned) == ["a", "c"]


def test_unprocessed_new_and_unsupported_files_are_not_changes():
    known = {"queued": _known("old"), "exe": _known("")}
    statuses = {"queued": "discovered", "exe": "unsupported"}
    scanned = [_scanned("queued", "new"), _scanned("exe", ""), _scanned("brand-new", "h")]

    assert changed_paths(known, statuses, scanned) == []


def test_failed_file_with_new_content_counts_as_changed():
    assert changed_paths({"a": _known("old")}, {"a": "failed"}, [_scanned("a", "new")]) == ["a"]
