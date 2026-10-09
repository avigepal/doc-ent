from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.ingestion import documents_actions
from app.ingestion.documents_actions import delete_documents, reprocess_documents


@pytest.fixture(autouse=True)
def no_redis(monkeypatch):
    monkeypatch.setattr(documents_actions, "clear_enqueued", MagicMock())


def _touch(base: Path, *parts: str) -> Path:
    path = base.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    return path


def test_delete_removes_the_file_its_converted_text_and_summary(tmp_path: Path):
    original = _touch(tmp_path, "raw", "contracts", "a.pdf")
    _touch(tmp_path, "converted", "contracts", "a.md")
    _touch(tmp_path, "converted", "contracts", "a.md.json")
    _touch(tmp_path, "summaries", "contracts", "a.md")
    keep = _touch(tmp_path, "raw", "contracts", "b.pdf")
    session = MagicMock()
    session.execute.return_value.all.return_value = [(1, str(original))]

    assert delete_documents(session, str(tmp_path), [1]) == 1

    assert not original.exists()
    assert not (tmp_path / "converted" / "contracts" / "a.md").exists()
    assert not (tmp_path / "converted" / "contracts" / "a.md.json").exists()
    assert not (tmp_path / "summaries" / "contracts" / "a.md").exists()
    assert keep.exists()
    documents_actions.clear_enqueued.assert_called_once_with([1])


def test_delete_never_touches_a_file_outside_raw(tmp_path: Path):
    outside = _touch(tmp_path, "elsewhere", "a.pdf")
    session = MagicMock()
    session.execute.return_value.all.return_value = [(1, str(outside))]

    delete_documents(session, str(tmp_path), [1])

    assert outside.exists()


def test_delete_with_no_ids_does_nothing(tmp_path: Path):
    session = MagicMock()

    assert delete_documents(session, str(tmp_path), []) == 0
    session.execute.assert_not_called()


def test_reprocess_resets_the_files_and_returns_their_ids():
    session = MagicMock()
    session.execute.return_value.scalars.return_value = [4, 5]

    assert reprocess_documents(session, [4, 5, 99]) == [4, 5]

    session.commit.assert_called_once()
    documents_actions.clear_enqueued.assert_called_once_with([4, 5])


def test_reprocess_with_nothing_found_is_a_no_op():
    session = MagicMock()
    session.execute.return_value.scalars.return_value = []

    assert reprocess_documents(session, [99]) == []
    session.commit.assert_not_called()


# ---------- clearing a whole folder ----------

from app.ingestion.documents_actions import FolderNotFound, InvalidFolderName, clear_folder  # noqa: E402


def _folder_session(ids, rows):
    session = MagicMock()
    session.execute.return_value.scalars.return_value = ids
    session.execute.return_value.all.return_value = rows
    return session


def test_clear_folder_empties_it_but_keeps_the_folder_and_others(tmp_path: Path):
    a = _touch(tmp_path, "raw", "contracts", "a.pdf")
    _touch(tmp_path, "raw", "contracts", "sub", "deep.pdf")
    _touch(tmp_path, "raw", "contracts", "never_scanned.txt")
    _touch(tmp_path, "converted", "contracts", "a.md")
    _touch(tmp_path, "summaries", "contracts", "a.md")
    other = _touch(tmp_path, "raw", "invoices", "keep.pdf")
    other_md = _touch(tmp_path, "converted", "invoices", "keep.md")
    session = _folder_session([1], [(1, str(a))])

    assert clear_folder(session, str(tmp_path), "contracts") == 1

    assert (tmp_path / "raw" / "contracts").is_dir()
    assert list((tmp_path / "raw" / "contracts").iterdir()) == []
    assert list((tmp_path / "converted" / "contracts").iterdir()) == []
    assert list((tmp_path / "summaries" / "contracts").iterdir()) == []
    assert other.exists() and other_md.exists()


@pytest.mark.parametrize("name", ["", ".", "..", "../raw", "a/b", r"a\b", ".hidden", "uploads"])
def test_clear_folder_refuses_unsafe_or_reserved_names(tmp_path: Path, name: str):
    (tmp_path / "raw" / "uploads").mkdir(parents=True)
    victim = _touch(tmp_path, "raw", "keep.pdf")

    with pytest.raises(InvalidFolderName):
        clear_folder(MagicMock(), str(tmp_path), name)

    assert victim.exists()


def test_clear_folder_reports_a_missing_folder(tmp_path: Path):
    (tmp_path / "raw").mkdir()

    with pytest.raises(FolderNotFound):
        clear_folder(MagicMock(), str(tmp_path), "nope")
