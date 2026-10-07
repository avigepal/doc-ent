from pathlib import Path

from app.conversion.backends import PlainTextBackend


def test_plaintext_backend_reads_file_content_as_is(tmp_path: Path):
    f = tmp_path / "note.txt"
    f.write_text("Revenue grew 15 percent.", encoding="utf-8")

    result = PlainTextBackend().convert(f)

    assert result.markdown == "Revenue grew 15 percent."
    assert result.engine == "plaintext"
    assert result.engine_metadata["title"] == "note"


def test_plaintext_backend_replaces_undecodable_bytes_instead_of_crashing(tmp_path: Path):
    f = tmp_path / "bad.txt"
    f.write_bytes(b"valid text \xff\xfe invalid bytes")

    result = PlainTextBackend().convert(f)

    assert "valid text" in result.markdown
    assert "invalid bytes" in result.markdown
