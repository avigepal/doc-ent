from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.ingestion import uploads
from app.ingestion.uploads import delete_uploads


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(uploads, "clear_enqueued", MagicMock())

    def touch(*parts: str) -> Path:
        path = tmp_path.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
        return path

    # an uploaded file with all its derived outputs...
    touch("raw", "uploads", "a.pdf")
    touch("converted", "uploads", "a.md")
    touch("converted", "uploads", "a.md.json")
    touch("summaries", "uploads", "a.md")
    # ...a leftover the database never knew about...
    touch("converted", "uploads", "orphan.md")
    # ...and a corpus folder that must never be touched
    touch("raw", "test", "keep.pdf")
    touch("converted", "test", "keep.md")
    return tmp_path


def _session_returning(rows):
    session = MagicMock()
    session.execute.return_value.all.return_value = rows
    return session


def _exists(base: Path, *parts: str) -> bool:
    return base.joinpath(*parts).exists()


def test_clearing_everything_removes_uploads_and_leaves_corpus_folders(data_dir: Path):
    session = _session_returning([(1, str(data_dir / "raw" / "uploads" / "a.pdf"))])

    removed = delete_uploads(session, str(data_dir))

    assert removed == 1
    assert not _exists(data_dir, "raw", "uploads", "a.pdf")
    assert not _exists(data_dir, "converted", "uploads", "a.md")
    assert not _exists(data_dir, "converted", "uploads", "a.md.json")
    assert not _exists(data_dir, "summaries", "uploads", "a.md")
    assert not _exists(data_dir, "converted", "uploads", "orphan.md")  # swept too
    assert _exists(data_dir, "raw", "test", "keep.pdf")
    assert _exists(data_dir, "converted", "test", "keep.md")
    uploads.clear_enqueued.assert_called_once_with([1])


def test_deleting_one_file_keeps_everything_else(data_dir: Path):
    session = _session_returning([(1, str(data_dir / "raw" / "uploads" / "a.pdf"))])

    removed = delete_uploads(session, str(data_dir), [1])

    assert removed == 1
    assert not _exists(data_dir, "raw", "uploads", "a.pdf")
    assert not _exists(data_dir, "converted", "uploads", "a.md")
    # no whole-folder sweep when deleting a single file
    assert _exists(data_dir, "converted", "uploads", "orphan.md")
    assert _exists(data_dir, "raw", "test", "keep.pdf")


def test_nothing_matched_deletes_no_rows_and_no_corpus_files(data_dir: Path):
    session = _session_returning([])

    assert delete_uploads(session, str(data_dir), [999]) == 0

    assert _exists(data_dir, "raw", "uploads", "a.pdf")  # not matched, so not touched
    assert _exists(data_dir, "raw", "test", "keep.pdf")
    uploads.clear_enqueued.assert_not_called()
