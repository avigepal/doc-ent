from app.ingestion.folder_status import build_folder_statuses, top_level_folder


# ---------- top_level_folder ----------

def test_top_level_folder_forward_slash():
    assert top_level_folder("/data/pipeline/raw/contracts/q2.txt", "/data/pipeline/raw") == "contracts"


def test_top_level_folder_backslash_windows():
    assert top_level_folder("D:\\dev\\doc\\data\\raw\\contracts\\q2.txt", "D:\\dev\\doc\\data\\raw") == "contracts"


def test_top_level_folder_nested_subfolder_still_returns_top_level():
    assert top_level_folder("/data/pipeline/raw/contracts/2024/q2.txt", "/data/pipeline/raw") == "contracts"


def test_top_level_folder_file_directly_in_raw_returns_none():
    assert top_level_folder("/data/pipeline/raw/loose.txt", "/data/pipeline/raw") is None


def test_top_level_folder_outside_raw_returns_none():
    assert top_level_folder("/data/pipeline/converted/contracts/q2.md", "/data/pipeline/raw") is None


# ---------- build_folder_statuses ----------

def test_build_folder_statuses_counts_by_status():
    files = [
        (1, "/raw/contracts/a.txt", "summarized"),
        (2, "/raw/contracts/b.txt", "converted"),
        (3, "/raw/invoices/c.txt", "discovered"),
    ]

    result = build_folder_statuses("/raw", files, running_file_ids=set(), failed_file_ids=set())

    by_name = {r.name: r for r in result}
    assert by_name["contracts"].total_files == 2
    assert by_name["contracts"].summarized == 1
    assert by_name["contracts"].converted == 1
    assert by_name["invoices"].total_files == 1
    assert by_name["invoices"].discovered == 1


def test_build_folder_statuses_marks_processing_and_failures():
    files = [
        (1, "/raw/contracts/a.txt", "discovered"),
        (2, "/raw/contracts/b.txt", "discovered"),
    ]

    result = build_folder_statuses("/raw", files, running_file_ids={1}, failed_file_ids={2})

    status = result[0]
    assert status.processing is True
    assert status.has_failures is True


def test_build_folder_statuses_sorted_alphabetically():
    files = [
        (1, "/raw/zebra/a.txt", "discovered"),
        (2, "/raw/alpha/b.txt", "discovered"),
    ]

    result = build_folder_statuses("/raw", files, set(), set())

    assert [r.name for r in result] == ["alpha", "zebra"]


def test_build_folder_statuses_ignores_files_outside_any_folder():
    files = [(1, "/raw/loose.txt", "discovered")]

    result = build_folder_statuses("/raw", files, set(), set())

    assert result == []


def test_build_folder_statuses_empty_input_returns_empty_list():
    assert build_folder_statuses("/raw", [], set(), set()) == []
