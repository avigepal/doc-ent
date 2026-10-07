from app.models import FileRecord
from app.tasks.convert import _apply_document_metadata


def _bare_file_record() -> FileRecord:
    return FileRecord(path="x", sha256="x", mime_type="x", size_bytes=1, queue="convert_fast")


def test_applies_title_author_page_count():
    f = _bare_file_record()
    _apply_document_metadata(f, {"title": "Q2 Report", "author": "alice@example.com", "page_count": 12})

    assert f.title == "Q2 Report"
    assert f.author == "alice@example.com"
    assert f.page_count == 12


def test_applies_valid_iso_doc_created_at():
    f = _bare_file_record()
    _apply_document_metadata(f, {"doc_created_at": "2024-01-01T10:00:00+00:00"})

    assert f.doc_created_at.isoformat() == "2024-01-01T10:00:00+00:00"


def test_missing_keys_leave_fields_unset():
    f = _bare_file_record()
    _apply_document_metadata(f, {})

    assert f.title is None
    assert f.author is None
    assert f.page_count is None
    assert f.doc_created_at is None


def test_unparseable_doc_created_at_is_silently_skipped_not_raised():
    f = _bare_file_record()
    _apply_document_metadata(f, {"doc_created_at": "not-a-real-date"})

    assert f.doc_created_at is None


def test_falsy_values_do_not_overwrite_with_empty_string():
    f = _bare_file_record()
    _apply_document_metadata(f, {"title": "", "author": None})

    assert f.title is None
    assert f.author is None
