import pytest

docx = pytest.importorskip("docx")

from app.editing.docx_ops import _header_footer_listing, apply_ops, parse_ops, read_blocks, summarize  # noqa: E402


def body_order(doc):
    """The top-level paragraphs and tables in order: their text, or "(table)"."""
    out = []
    for element in doc.element.body.iterchildren():
        if element.tag.endswith("}p"):
            text = "".join(t.text or "" for t in element.iter() if t.tag.endswith("}t"))
            if text.strip():
                out.append(text)
        elif element.tag.endswith("}tbl"):
            out.append("(table)")
    return out


def run_ops(doc, data):
    blocks = read_blocks(doc)
    ops, dropped = parse_ops(data, {b.id for b in blocks})
    changes, skipped = apply_ops(doc, blocks, ops)
    return changes, skipped, dropped


def sample():
    d = docx.Document()
    d.add_paragraph("Intro")
    d.add_paragraph("Middle")
    d.add_paragraph("Closing")
    return d


def test_a_paragraph_moves_after_another_block():
    d = sample()

    changes, skipped, dropped = run_ops(d, {"operations": [{"op": "move", "id": 3, "after": 1}]})

    assert body_order(d) == ["Intro", "Closing", "Middle"]
    assert [c.op for c in changes] == ["moved"] and not skipped and not dropped


def test_a_table_moves_too():
    d = sample()
    table = d.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "cell"

    run_ops(d, {"operations": [{"op": "move", "id": 4, "after": 1}]})

    assert body_order(d) == ["Intro", "(table)", "Middle", "Closing"]


def test_a_block_cannot_move_after_itself():
    d = sample()

    changes, _, dropped = run_ops(d, {"operations": [{"op": "move", "id": 2, "after": 2}]})

    assert changes == []
    assert body_order(d) == ["Intro", "Middle", "Closing"]
    assert dropped


def test_a_move_after_a_removed_block_is_skipped():
    d = sample()
    data = {"operations": [{"op": "delete", "id": 1}, {"op": "move", "id": 3, "after": 1}]}

    _, skipped, _ = run_ops(d, data)

    assert any("already removed" in s for s in skipped)
    assert body_order(d) == ["Middle", "Closing"]


def test_the_header_text_is_replaced_and_keeps_its_look():
    d = sample()
    header = d.sections[0].header
    run = header.paragraphs[0].add_run("Old header")
    run.bold = True

    changes, skipped, _ = run_ops(d, {"operations": [{"op": "set_header", "text": "Docent Ltd"}]})

    assert [p.text for p in d.sections[0].header.paragraphs] == ["Docent Ltd"]
    assert d.sections[0].header.paragraphs[0].runs[0].bold is True
    assert changes[0].before == "Old header" and changes[0].after == "Docent Ltd"
    assert not skipped


def test_a_missing_footer_is_created_with_the_text():
    d = sample()

    run_ops(d, {"operations": [{"op": "set_footer", "text": "Page one"}]})

    assert "Page one" in [p.text for p in d.sections[0].footer.paragraphs]


def test_a_header_operation_without_text_is_dropped():
    d = sample()

    _, _, dropped = run_ops(d, {"operations": [{"op": "set_header", "text": "  "}]})

    assert dropped


def test_the_model_sees_the_current_header_and_footer():
    d = sample()
    d.sections[0].header.paragraphs[0].text = "Confidential"

    listing = _header_footer_listing(d)

    assert "header: Confidential" in listing
    assert "footer" not in listing  # no footer of its own: nothing to show


def test_a_move_is_counted_in_the_summary():
    d = sample()
    changes, _, _ = run_ops(d, {"operations": [{"op": "move", "id": 3, "after": 1}]})

    assert summarize(changes) == "moved 1 part"


def test_rejecting_a_move_in_the_review_puts_the_block_back(tmp_path):
    from app.editing.changes import apply_accepted, describe_changes, save_changes

    base = tmp_path / "base.docx"
    sample().save(str(base))
    result = tmp_path / "result.docx"
    d = docx.Document(str(base))
    blocks = read_blocks(d)
    ops, _ = parse_ops({"operations": [{"op": "move", "id": 3, "after": 1}]}, {b.id for b in blocks})
    changes, _ = apply_ops(d, blocks, ops)
    d.save(str(result))
    save_changes(result, base, ops, changes)

    assert describe_changes(result)[0]["op"] == "moved"
    apply_accepted(result, [])

    assert body_order(docx.Document(str(result))) == ["Intro", "Middle", "Closing"]
