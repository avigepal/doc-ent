from app.summarization.chunker import chunk_markdown


def test_chunk_by_sections_splits_on_h2_headings():
    markdown = (
        "## Intro\n\nThis is the intro.\n\n"
        "## Details\n\nThese are the details.\n\n"
        "## Conclusion\n\nWrapping up.\n"
    )

    chunks = chunk_markdown(markdown, max_chars=1000)

    assert [c.heading for c in chunks] == ["Intro", "Details", "Conclusion"]
    assert "This is the intro." in chunks[0].text
    assert "These are the details." in chunks[1].text
    assert "Wrapping up." in chunks[2].text
    assert [c.index for c in chunks] == [0, 1, 2]


def test_chunk_without_headings_falls_back_to_paragraph_grouping(tmp_path=None):
    markdown = "Paragraph one.\n\nParagraph two.\n\nParagraph three."

    chunks = chunk_markdown(markdown, max_chars=1000)

    # no headings -> everything fits under max_chars -> single chunk
    assert len(chunks) == 1
    assert chunks[0].heading == "(untitled)"
    assert "Paragraph one." in chunks[0].text
    assert "Paragraph three." in chunks[0].text


def test_oversized_section_is_split_into_multiple_chunks_under_max_chars():
    long_para_a = "A" * 30
    long_para_b = "B" * 30
    long_para_c = "C" * 30
    markdown = f"## Section\n\n{long_para_a}\n\n{long_para_b}\n\n{long_para_c}\n"

    chunks = chunk_markdown(markdown, max_chars=40)

    assert len(chunks) > 1
    assert all(len(c.text) <= 40 for c in chunks)
    assert all(c.heading == "Section" for c in chunks)
    # content preserved across the split, nothing dropped
    combined = "".join(c.text for c in chunks)
    assert long_para_a in combined
    assert long_para_b in combined
    assert long_para_c in combined


def test_empty_markdown_returns_no_chunks():
    assert chunk_markdown("", max_chars=1000) == []
    assert chunk_markdown("   \n\n  ", max_chars=1000) == []


def test_chunk_index_is_sequential_across_split_sections():
    markdown = "## A\n\n" + ("x" * 30) + "\n\n" + ("y" * 30) + "\n\n## B\n\nshort"

    chunks = chunk_markdown(markdown, max_chars=35)

    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert chunks[-1].heading == "B"
