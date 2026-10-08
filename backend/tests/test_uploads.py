from pathlib import Path

from app.ingestion.uploads import safe_upload_filename, unique_destination


def test_safe_upload_filename_keeps_extension():
    assert safe_upload_filename("report.pdf") == "report.pdf"


def test_safe_upload_filename_strips_path_components():
    assert safe_upload_filename("../../etc/passwd") == "passwd"
    assert safe_upload_filename("a/b/c.pdf") == "c.pdf"


def test_safe_upload_filename_replaces_disallowed_characters():
    assert safe_upload_filename("my report (final)!.pdf") == "my-report-final-.pdf"


def test_safe_upload_filename_falls_back_when_empty():
    assert safe_upload_filename("...") == "upload"
    assert safe_upload_filename("") == "upload"


def test_unique_destination_returns_name_unchanged_when_free(tmp_path: Path):
    assert unique_destination(tmp_path, "report.pdf") == tmp_path / "report.pdf"


def test_unique_destination_avoids_clobbering_existing_file(tmp_path: Path):
    (tmp_path / "report.pdf").write_text("existing")

    dest = unique_destination(tmp_path, "report.pdf")

    assert dest == tmp_path / "report-1.pdf"


def test_unique_destination_increments_past_multiple_collisions(tmp_path: Path):
    (tmp_path / "report.pdf").write_text("x")
    (tmp_path / "report-1.pdf").write_text("x")
    (tmp_path / "report-2.pdf").write_text("x")

    dest = unique_destination(tmp_path, "report.pdf")

    assert dest == tmp_path / "report-3.pdf"
