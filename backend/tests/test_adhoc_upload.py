from app.search.adhoc_upload import _matches, convert_upload_to_chunks, detect_mime


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


# ---- _matches (pure helper) ----


def test_matches_passes_through_when_no_filter_given():
    assert _matches(None, None) is True
    assert _matches(None, "anything") is True


def test_matches_is_case_insensitive_substring():
    assert _matches("alice", "Alice Smith <alice@example.com>") is True
    assert _matches("ALICE", "alice@example.com") is True


def test_matches_fails_when_metadata_missing_or_not_matching():
    assert _matches("alice", None) is False
    assert _matches("bob", "alice@example.com") is False


# ---- convert_upload_to_chunks author/title filtering (same semantics as
# the corpus path's author/title filter — see app/search/pgvector_retrieval.py) ----

_EML_FROM_ALICE = (
    b"Subject: Q2 contract renewal\r\n"
    b"From: Alice Smith <alice@example.com>\r\n"
    b"To: bob@example.com\r\n"
    b"Date: Mon, 1 Jan 2026 10:00:00 +0000\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"The Q2 contract renewal terms are attached.\r\n"
)


def test_convert_upload_to_chunks_includes_file_matching_author_filter():
    chunks = convert_upload_to_chunks("email.eml", _EML_FROM_ALICE, author_filter="alice")

    assert len(chunks) == 1
    assert "Q2 contract renewal" in chunks[0].text


def test_convert_upload_to_chunks_excludes_file_not_matching_author_filter():
    chunks = convert_upload_to_chunks("email.eml", _EML_FROM_ALICE, author_filter="bob")

    assert chunks == []


def test_convert_upload_to_chunks_includes_file_matching_title_filter():
    chunks = convert_upload_to_chunks("email.eml", _EML_FROM_ALICE, title_filter="renewal")

    assert len(chunks) == 1


def test_convert_upload_to_chunks_excludes_file_not_matching_title_filter():
    chunks = convert_upload_to_chunks("email.eml", _EML_FROM_ALICE, title_filter="invoice")

    assert chunks == []


def test_convert_upload_to_chunks_requires_both_filters_to_match():
    chunks = convert_upload_to_chunks(
        "email.eml", _EML_FROM_ALICE, author_filter="alice", title_filter="invoice"
    )

    assert chunks == []  # author matches, title doesn't — must exclude
