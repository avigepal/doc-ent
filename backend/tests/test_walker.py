import hashlib
from pathlib import Path

from app.ingestion.walker import scan_directory, scan_file


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


def test_unsupported_files_are_routed_away_and_not_hashed(tmp_path: Path):
    exe = tmp_path / "setup.exe"
    exe.write_bytes(bytes([0x4D, 0x5A, 0x90, 0x00]) + bytes(200))  # "MZ" DOS/PE header

    [result] = list(scan_directory(tmp_path))

    assert result.queue == "unsupported"
    assert result.sha256 == ""
    assert result.size_bytes == exe.stat().st_size


def test_scan_file_classifies_one_file_without_walking_siblings(tmp_path: Path):
    target = tmp_path / "upload.txt"
    target.write_text("just this one")
    (tmp_path / "other.txt").write_text("not scanned")

    result = scan_file(target)

    assert result.path == str(target)
    assert result.sha256 == hashlib.sha256(b"just this one").hexdigest()
    assert result.queue == "convert_fast"
