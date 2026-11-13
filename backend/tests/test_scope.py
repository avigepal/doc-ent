import os
from pathlib import Path

import pytest

from app.ingestion.scope import BadFolder, clean_folder, folder_dir, in_scope, scope_prefix
from app.ingestion.walker import scan_directory

RAW = Path("data") / "raw"


def test_whole_library_is_the_empty_folder():
    assert clean_folder("") == ""
    assert clean_folder("  /  ") == ""
    assert folder_dir(RAW, "") == RAW


def test_nested_folder_is_cleaned_and_joined_under_raw():
    assert clean_folder("/sales/2024/") == "sales/2024"
    assert clean_folder("sales\\2024") == "sales/2024"
    assert folder_dir(RAW, "sales/2024") == RAW / "sales" / "2024"


@pytest.mark.parametrize("bad", ["..", "a/../b", "./a", ".incoming", "a/.hidden", "C:", "a//b"])
def test_folder_names_that_leave_raw_or_hide_files_are_refused(bad):
    with pytest.raises(BadFolder):
        clean_folder(bad)


def test_prefix_ends_with_a_separator_so_sibling_folders_do_not_match():
    prefix = scope_prefix(RAW, "sales")
    assert prefix.endswith(os.sep)
    assert in_scope({str(RAW / "sales" / "a.pdf"), str(RAW / "sales2" / "b.pdf")}, prefix) == {
        str(RAW / "sales" / "a.pdf")
    }


def test_folder_scan_only_walks_that_folder(tmp_path: Path):
    (tmp_path / "sales" / "2024").mkdir(parents=True)
    (tmp_path / "sales" / "2024" / "q1.txt").write_text("q1")
    (tmp_path / "hr").mkdir()
    (tmp_path / "hr" / "policy.txt").write_text("policy")

    walked = [Path(f.path).name for f in scan_directory(folder_dir(tmp_path, "sales"))]

    assert walked == ["q1.txt"]
