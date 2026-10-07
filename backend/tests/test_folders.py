from pathlib import Path

from app.ingestion.folders import list_top_level_folders


def test_lists_top_level_directories_only(tmp_path: Path):
    (tmp_path / "contracts").mkdir()
    (tmp_path / "invoices").mkdir()
    (tmp_path / "contracts" / "nested").mkdir()  # not listed, top-level only
    (tmp_path / "loose_file.pdf").write_text("x")  # not a directory

    assert list_top_level_folders(tmp_path) == ["contracts", "invoices"]


def test_sorted_alphabetically(tmp_path: Path):
    (tmp_path / "zebra").mkdir()
    (tmp_path / "alpha").mkdir()

    assert list_top_level_folders(tmp_path) == ["alpha", "zebra"]


def test_skips_hidden_directories(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "contracts").mkdir()

    assert list_top_level_folders(tmp_path) == ["contracts"]


def test_missing_raw_dir_returns_empty_list(tmp_path: Path):
    assert list_top_level_folders(tmp_path / "does-not-exist") == []
