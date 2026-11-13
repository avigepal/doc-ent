import json

import pytest

pymupdf = pytest.importorskip("pymupdf")

from app.editing.changes import apply_accepted, describe_changes, save_changes  # noqa: E402
from app.editing.layout import LayoutResult, LayoutUnsupported  # noqa: E402
from app.editing.pdf_layout import edit_pdf  # noqa: E402
from app.editing.pdf_pages import apply_page_ops, parse_page_ops, rebuild_pages  # noqa: E402


def make_pdf(path, count=3):
    doc = pymupdf.open()
    for n in range(1, count + 1):
        page = doc.new_page()
        page.insert_text((72, 100), f"Page {n} heading")
    doc.save(str(path))
    doc.close()


def first_lines(path):
    with pymupdf.open(str(path)) as doc:
        return [page.get_text("text").strip().splitlines()[0] for page in doc]


def ops(*raw, count=3):
    parsed, dropped = parse_page_ops({"operations": list(raw)}, count)
    return parsed, dropped


class FakeModel:
    def __init__(self, plan):
        self.plan = plan

    def chat(self, system, user, temperature=0.2, **kwargs):
        return json.dumps(self.plan)


def test_a_page_is_deleted_and_the_others_keep_their_text(tmp_path):
    src, dst = tmp_path / "a.pdf", tmp_path / "b.pdf"
    make_pdf(src)
    parsed, _ = ops({"op": "delete_page", "page": 2})

    rebuild_pages(src, dst, parsed)

    assert first_lines(dst) == ["Page 1 heading", "Page 3 heading"]


def test_a_page_moves_after_another_page_or_to_the_front(tmp_path):
    src, dst = tmp_path / "a.pdf", tmp_path / "b.pdf"
    make_pdf(src)
    parsed, _ = ops({"op": "move_page", "page": 3, "after": 0})

    rebuild_pages(src, dst, parsed)

    assert first_lines(dst) == ["Page 3 heading", "Page 1 heading", "Page 2 heading"]


def test_a_page_turns_and_the_turn_is_kept(tmp_path):
    src, dst = tmp_path / "a.pdf", tmp_path / "b.pdf"
    make_pdf(src)
    parsed, _ = ops({"op": "rotate_page", "page": 1, "degrees": 90})

    rebuild_pages(src, dst, parsed)

    with pymupdf.open(str(dst)) as doc:
        assert doc[0].rotation == 90 and doc[1].rotation == 0


def test_a_line_of_text_is_added_to_the_page_bottom(tmp_path):
    src, dst = tmp_path / "a.pdf", tmp_path / "b.pdf"
    make_pdf(src, count=1)
    parsed, _ = ops({"op": "add_text", "page": 1, "text": "Confidential", "where": "bottom"}, count=1)

    rebuild_pages(src, dst, parsed)

    with pymupdf.open(str(dst)) as doc:
        assert "Confidential" in doc[0].get_text()


def test_pages_that_do_not_exist_or_bad_turns_are_dropped():
    parsed, dropped = ops(
        {"op": "delete_page", "page": 9},
        {"op": "rotate_page", "page": 1, "degrees": 45},
        {"op": "move_page", "page": 2, "after": 2},
        {"op": "add_text", "page": 1, "text": "   "},
        {"op": "explode", "page": 1},
    )
    assert parsed == []
    assert len(dropped) == 5


def test_the_last_page_is_never_removed(tmp_path):
    src = tmp_path / "a.pdf"
    make_pdf(src, count=1)
    with pymupdf.open(str(src)) as doc:
        changes, skipped = apply_page_ops(doc, ops({"op": "delete_page", "page": 1}, count=1)[0])
    assert skipped and "last page" in skipped[0]


def test_a_rejected_page_change_puts_the_page_back(tmp_path):
    base, result = tmp_path / "base.pdf", tmp_path / "result.pdf"
    make_pdf(base)
    parsed, _ = ops({"op": "delete_page", "page": 2}, {"op": "rotate_page", "page": 1, "degrees": 90})
    rebuild_pages(base, result, parsed)
    with pymupdf.open(str(base)) as doc:
        changes, _ = apply_page_ops(doc, parsed)
    save_changes(result, base, parsed, changes, kind="pdf")

    assert [c["op"] for c in describe_changes(result)] == ["delete_page", "rotate_page"]
    apply_accepted(result, [1])  # keep the turn, reject the deletion

    assert first_lines(result) == ["Page 1 heading", "Page 2 heading", "Page 3 heading"]


def test_the_editor_applies_page_operations_when_the_request_asks_for_them(tmp_path):
    src, dst = tmp_path / "a.pdf", tmp_path / "b.pdf"
    make_pdf(src)
    model = FakeModel({"operations": [{"op": "delete_page", "page": 2}]})

    results = [u for u in edit_pdf(src, dst, "delete page 2", model) if isinstance(u, LayoutResult)]

    assert results[-1].summary == "page 2 deleted"
    assert first_lines(dst) == ["Page 1 heading", "Page 3 heading"]


def test_a_request_the_pages_cannot_do_falls_back_to_a_clean_rebuild(tmp_path):
    src, dst = tmp_path / "a.pdf", tmp_path / "b.pdf"
    make_pdf(src)
    model = FakeModel({"operations": [], "problem": "a summary section needs new text"})

    with pytest.raises(LayoutUnsupported, match="new text"):
        list(edit_pdf(src, dst, "add a summary section", model))
