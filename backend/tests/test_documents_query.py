from app.ingestion.documents_query import relative_to_raw


def test_strips_posix_raw_prefix():
    assert (
        relative_to_raw("/data/pipeline/raw/contracts/lease.pdf", "/data/pipeline/raw")
        == "contracts/lease.pdf"
    )


def test_strips_windows_raw_prefix():
    assert (
        relative_to_raw(r"D:\data\raw\contracts\lease.pdf", r"D:\data\raw")
        == r"contracts\lease.pdf"
    )


def test_tolerates_a_trailing_separator_on_raw_dir():
    assert relative_to_raw("/data/raw/a.pdf", "/data/raw/") == "a.pdf"


def test_leaves_unrelated_paths_untouched():
    assert relative_to_raw("/somewhere/else/a.pdf", "/data/raw") == "/somewhere/else/a.pdf"
