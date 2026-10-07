from app.search.adhoc_upload import convert_upload_to_chunks, detect_mime


def test_detect_mime_identifies_plain_text():
    assert detect_mime(b"just some plain ascii text") == "text/plain"


def test_detect_mime_falls_back_on_unreadable_content():
    # an empty/ambiguous buffer still returns *something* usable, never raises
    result = detect_mime(b"", fallback="application/octet-stream")
    assert isinstance(result, str) and result


def test_convert_upload_to_chunks_plain_text_file():
    content = b"Revenue grew 15 percent this quarter, driven by new contracts."

    chunks = convert_upload_to_chunks("note.txt", content, content_type="text/plain")

    assert len(chunks) == 1
    assert chunks[0].file_path == "note.txt"
    assert chunks[0].score == 1.0
    assert "Revenue grew 15 percent" in chunks[0].text


def test_convert_upload_to_chunks_splits_sectioned_content():
    content = b"## Intro\n\nFirst part.\n\n## Details\n\nSecond part."

    chunks = convert_upload_to_chunks("report.txt", content)

    assert len(chunks) == 2
    assert all(c.file_path == "report.txt" for c in chunks)
    assert all(c.score == 1.0 for c in chunks)
    assert chunks[0].heading == "Intro"
    assert chunks[1].heading == "Details"
