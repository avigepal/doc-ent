from app.search.folder_filter import build_folder_like_patterns


def test_builds_both_separator_variants_per_folder():
    patterns = build_folder_like_patterns("/data/pipeline/raw", ["contracts", "invoices"])
    assert patterns == [
        "/data/pipeline/raw/contracts/%",
        "/data/pipeline/raw\\\\contracts\\\\%",
        "/data/pipeline/raw/invoices/%",
        "/data/pipeline/raw\\\\invoices\\\\%",
    ]


def test_windows_style_raw_dir_doubles_every_backslash_for_postgres_like_escaping():
    # PostgreSQL's LIKE treats backslash as its default escape character —
    # confirmed live that a single-backslash pattern silently matched
    # nothing against real backslash-containing paths. Every backslash in
    # the pattern (from raw_dir *and* the separators this function adds)
    # must appear doubled so it survives as one literal backslash.
    patterns = build_folder_like_patterns("D:\\dev\\doc\\data\\raw", ["contracts"])
    backslash_pattern = patterns[1]
    assert backslash_pattern == "D:\\\\dev\\\\doc\\\\data\\\\raw\\\\contracts\\\\%"


def test_strips_trailing_slash_from_raw_dir():
    patterns = build_folder_like_patterns("/data/pipeline/raw/", ["contracts"])
    assert patterns == ["/data/pipeline/raw/contracts/%", "/data/pipeline/raw\\\\contracts\\\\%"]


def test_empty_folder_list_returns_empty_patterns():
    assert build_folder_like_patterns("/data/pipeline/raw", []) == []


def test_blank_folder_names_are_skipped():
    patterns = build_folder_like_patterns("/data/pipeline/raw", ["contracts", ""])
    assert patterns == ["/data/pipeline/raw/contracts/%", "/data/pipeline/raw\\\\contracts\\\\%"]
