"""One request that changes the words AND the look of a Word file."""

import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

docx = pytest.importorskip("docx")

from app.editing.docx_ops import OPS_SYSTEM_PROMPT  # noqa: E402
from app.editing.layout import PARAGRAPH_SYSTEM_PROMPT  # noqa: E402
from app.editing.request import SPLIT_SYSTEM_PROMPT, split_request  # noqa: E402
from app.editing.styling import STYLE_SYSTEM_PROMPT, mentions_style  # noqa: E402


class FakeModel:
    """Answers each kind of prompt the edit makes; `calls` records which were asked."""

    def __init__(self, split=None, split_error=False):
        self.split = split
        self.split_error = split_error
        self.calls = []

    def chat(self, system, user, temperature=0.2, **kwargs):
        self.calls.append(system)
        if system == SPLIT_SYSTEM_PROMPT:
            if self.split_error:
                return "not json at all"
            return json.dumps(self.split)
        if system == PARAGRAPH_SYSTEM_PROMPT:
            sent = json.loads(user.split("Paragraphs:\n", 1)[1])
            changed = [
                {"id": p["id"], "text": p["text"].replace("is quiet", "is very quiet")}
                for p in sent
                if "is quiet" in p["text"]
            ]
            return json.dumps({"paragraphs": changed})
        if system == OPS_SYSTEM_PROMPT:
            return json.dumps(
                {"operations": [{"op": "insert_after", "id": 2, "style": "paragraph", "text": "A short summary."}]}
            )
        if system == STYLE_SYSTEM_PROMPT:
            return json.dumps({"operations": [{"target": "headings", "set": {"color": "#1F3864", "bold": True}}]})
        raise AssertionError(f"unexpected prompt: {system[:40]}")


# ---------- telling the two parts apart ----------

@pytest.mark.parametrize(
    "message,expected",
    [
        ("shorten the intro and make the headings dark blue", True),
        ("use Arial 11 for the body", True),
        ("translate this to French", False),
        ("replace 2023 with 2024", False),
        ("what font is this document in?", False),
    ],
)
def test_a_request_that_mentions_the_look_is_noticed_even_next_to_a_wording_change(message, expected):
    assert mentions_style(message) is expected


def test_a_request_with_no_style_in_it_is_not_split_and_costs_no_model_call():
    llm = FakeModel()

    split = split_request(llm, "translate this to French")

    assert (split.text, split.style) == ("translate this to French", "") and llm.calls == []


def test_a_mixed_request_is_split_into_its_wording_and_its_look():
    llm = FakeModel({"text": "shorten the intro", "style": "make the headings dark blue"})

    split = split_request(llm, "shorten the intro and make the headings dark blue")

    assert (split.text, split.style) == ("shorten the intro", "make the headings dark blue")


def test_a_model_that_cannot_be_read_falls_back_to_one_kind_of_change():
    both = "shorten the intro and make the headings dark blue"
    assert split_request(FakeModel(split_error=True), "make the headings dark blue").style == "make the headings dark blue"
    # "shorten" is about the words, so the old either-or rule reads the mixed request as wording
    fallback = split_request(FakeModel(split_error=True), both)
    assert (fallback.text, fallback.style) == (both, "")


def test_a_tone_request_is_not_mistaken_for_a_look_request():
    llm = FakeModel({"text": "", "style": "in a formal style"})

    split = split_request(llm, "rewrite this in a formal style")

    assert (split.text, split.style) == ("rewrite this in a formal style", "")


# ---------- the whole edit ----------

@pytest.fixture
def run_edit(tmp_path, monkeypatch):
    # the editing module imports PyMuPDF (in the Docker image, not in a bare local venv)
    try:
        import pymupdf  # noqa: F401
    except ImportError:
        monkeypatch.setitem(sys.modules, "pymupdf", MagicMock())

    import app.editing.service as service

    raw = tmp_path / "raw"
    raw.mkdir()
    src = raw / "quote.docx"
    d = docx.Document()
    d.add_heading("Quotation", 1)
    d.add_paragraph("The Mac Studio is quiet.")
    d.save(str(src))

    exports = []
    monkeypatch.setattr(service, "edits_in_conversation", lambda *a, **k: [])
    monkeypatch.setattr(service, "record_export", lambda session, **kw: exports.append(kw) or len(exports))
    row = SimpleNamespace(id=1, path=str(src), mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    session = MagicMock()
    session.execute.return_value.scalars.return_value = [row]

    def run(instruction, llm):
        events = list(
            service.edit_events(session, instruction=instruction, file_ids=[1], llm=llm, data_dir=str(tmp_path))
        )
        files = [e["data"] for e in events if e["event"] == "file"]
        message = "".join(e["data"]["text"] for e in events if e["event"] == "token")
        return files, message, exports

    return run


def _open(export):
    return docx.Document(export["stored_path"])


def test_one_request_changes_the_words_and_the_look_in_a_single_file(run_edit):
    llm = FakeModel({"text": "reword the quiet sentence", "style": "make the headings dark blue"})

    files, message, exports = run_edit("reword the quiet sentence and make the headings dark blue", llm)

    assert len(files) == 1 and len(exports) == 1  # one result, not a file per step
    result = _open(exports[0])
    assert result.paragraphs[1].text == "The Mac Studio is very quiet."  # the words changed
    heading = result.paragraphs[0].runs[0]
    assert heading.bold is True and str(heading.font.color.rgb) == "1F3864"  # and the look
    assert "Applied your changes" in message and "Changed the style" in message
    assert "Quotation" in result.paragraphs[0].text  # nothing else was touched


def test_a_look_only_request_still_leaves_the_words_alone(run_edit):
    llm = FakeModel({"text": "", "style": "make the headings dark blue"})

    files, message, exports = run_edit("make the headings dark blue", llm)

    result = _open(exports[0])
    assert result.paragraphs[1].text == "The Mac Studio is quiet."
    assert PARAGRAPH_SYSTEM_PROMPT not in llm.calls
    assert "Changed the style" in message and "text is unchanged" in message


def test_a_wording_only_request_never_asks_for_style_changes(run_edit):
    llm = FakeModel()

    files, message, exports = run_edit("reword the quiet sentence", llm)

    assert _open(exports[0]).paragraphs[1].text == "The Mac Studio is very quiet."
    assert SPLIT_SYSTEM_PROMPT not in llm.calls and STYLE_SYSTEM_PROMPT not in llm.calls


def test_a_style_that_cannot_be_applied_keeps_the_wording_change(run_edit):
    class NoStyle(FakeModel):
        def chat(self, system, user, temperature=0.2, **kwargs):
            if system == STYLE_SYSTEM_PROMPT:
                return json.dumps({"operations": []})
            return super().chat(system, user, temperature, **kwargs)

    llm = NoStyle({"text": "reword the quiet sentence", "style": "make the headings a nicer colour"})

    files, message, exports = run_edit("reword the quiet sentence and make it pretty colour", llm)

    assert len(files) == 1
    assert _open(exports[0]).paragraphs[1].text == "The Mac Studio is very quiet."
    assert "couldn't apply the style changes" in message


def test_adding_content_and_restyling_in_one_request_keeps_the_original_file(run_edit):
    llm = FakeModel({"text": "add a summary after the first paragraph", "style": "make the headings dark blue"})

    files, message, exports = run_edit("add a summary after the first paragraph and make the headings dark blue", llm)

    assert len(files) == 1
    result = _open(exports[0])
    assert [p.text for p in result.paragraphs] == ["Quotation", "The Mac Studio is quiet.", "A short summary."]
    assert str(result.paragraphs[0].runs[0].font.color.rgb) == "1F3864"
    assert "added 1 new part" in message and "keeping its layout" in message and "Changed the style" in message
    assert PARAGRAPH_SYSTEM_PROMPT not in llm.calls  # not rewritten paragraph by paragraph
