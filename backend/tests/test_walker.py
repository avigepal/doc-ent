import hashlib
from pathlib import Path

from app.ingestion.walker import scan_directory


def test_scan_directory_hashes_and_classifies_files(tmp_path: Path):
    f1 = tmp_path / "a.txt"
    f1.write_text("hello world")
    f2 = tmp_path / "sub" / "b.txt"
    f2.parent.mkdir()
    f2.write_text("another file")

    results = list(scan_directory(tmp_path))

    assert len(results) == 2
    paths = {r.path for r in results}
    assert str(f1) in paths
    assert str(f2) in paths

    r1 = next(r for r in results if r.path == str(f1))
    assert r1.sha256 == hashlib.sha256(b"hello world").hexdigest()
    assert r1.size_bytes == len(b"hello world")
    assert r1.queue == "convert_fast"  # text/plain


def test_scan_directory_is_empty_for_empty_folder(tmp_path: Path):
    assert list(scan_directory(tmp_path)) == []


def test_scan_directory_skips_directories_and_only_yields_files(tmp_path: Path):
    (tmp_path / "empty_dir").mkdir()
    f1 = tmp_path / "a.txt"
    f1.write_text("x")

    results = list(scan_directory(tmp_path))
    assert len(results) == 1
    assert results[0].path == str(f1)
