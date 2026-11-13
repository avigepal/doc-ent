from pathlib import Path

from app.search.original import INLINE_TYPES, original_path


def test_a_file_inside_raw_is_served(tmp_path: Path):
    raw = tmp_path / "raw"
    (raw / "sales").mkdir(parents=True)
    doc = raw / "sales" / "q1.pdf"
    doc.write_bytes(b"%PDF-1.4")

    assert original_path(str(doc), raw) == doc.resolve()


def test_a_stored_path_outside_raw_is_refused(tmp_path: Path):
    raw = tmp_path / "raw"
    raw.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("not for the viewer")

    assert original_path(str(outside), raw) is None
    assert original_path(str(raw / ".." / "secret.txt"), raw) is None


def test_a_missing_file_is_refused(tmp_path: Path):
    raw = tmp_path / "raw"
    raw.mkdir()

    assert original_path(str(raw / "gone.pdf"), raw) is None


def test_only_pdfs_and_plain_images_open_in_the_browser():
    assert "application/pdf" in INLINE_TYPES
    assert "text/html" not in INLINE_TYPES
    assert "image/svg+xml" not in INLINE_TYPES
