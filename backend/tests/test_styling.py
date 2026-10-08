import json

import pytest

from app.editing.layout import LayoutUnsupported
from app.editing.styling import StyleOp, describe, looks_like_style, normalize_color, plan_style_changes, sanitize


@pytest.mark.parametrize(
    "message",
    [
        "make the headings dark blue",
        "use Arial 11 for the body",
        "change the font to Calibri",
        "centre the title",
        "set the margins to 1 inch",
        "increase the font size",
        "make the text bigger",
        "bold all the headings",
        "make the headings blue",
        "change the colour of the table header",
        "put the document in landscape",
        "can you justify the paragraphs",
        "add borders to the table",
        "change the line spacing to 1.5",
    ],
)
def test_styling_requests_are_recognized(message):
    assert looks_like_style(message)


@pytest.mark.parametrize(
    "message",
    [
        "what colour is the logo?",
        "which font does it use",
        "tell me about the red team report",
        "show me the blue headings",
        "translate it to French and use bold",
        "make it shorter",
        "instead of mac studio i want mac mini",
        "fix the grammar",
        "mac",
        "mad",
        "summarize this file",
        "blue",
    ],
)
def test_other_messages_are_not_styling_requests(message):
    assert not looks_like_style(message)


def test_colours_come_from_hex_or_common_names():
    assert normalize_color("#1f3864") == "1F3864"
    assert normalize_color("1F3864") == "1F3864"
    assert normalize_color("Dark  Blue") == "1F3864"
    assert normalize_color("blue") == "1F4E9E"
    assert normalize_color("not-a-colour") is None
    assert normalize_color(12) is None


def test_a_valid_plan_is_kept():
    ops, ignored = sanitize(
        {
            "operations": [
                {"target": "headings", "set": {"color": "#1F3864", "bold": True, "font": "Arial"}},
                {"target": "body", "set": {"size": 11, "line_spacing": 1.15, "align": "justify"}},
                {"target": "page", "set": {"margins": {"top": 1, "left": 1.25}, "orientation": "landscape"}},
                {"target": "tables", "set": {"table_borders": True, "table_header_fill": "dddddd"}},
            ]
        }
    )

    assert ignored == []
    assert [op.target for op in ops] == ["headings", "body", "page", "tables"]
    assert ops[0].props == {"color": "1F3864", "bold": True, "font": "Arial"}
    assert ops[2].props == {"margins": {"top": 1.0, "left": 1.25}, "orientation": "landscape"}
    assert ops[3].props["table_header_fill"] == "DDDDDD"


def test_nonsense_from_the_model_is_dropped_not_applied():
    ops, ignored = sanitize(
        {
            "operations": [
                {"target": "body", "set": {"size": 900, "color": "greenish", "font": "<script>", "bold": "yes", "align": "diagonal"}},
                {"target": "footnotes", "set": {"bold": True}},
                {"target": "body", "set": "bold"},
                "junk",
                {"target": "body", "set": {"size": 12}},
            ]
        }
    )

    assert [(op.target, op.props) for op in ops] == [("body", {"size": 12.0})]
    assert {"size", "color", "font", "bold", "align", "footnotes"} <= set(ignored)


def test_settings_that_do_not_belong_to_the_target_are_ignored():
    ops, ignored = sanitize(
        {
            "operations": [
                {"target": "body", "set": {"margins": {"top": 1}, "bold": True}},
                {"target": "page", "set": {"bold": True, "orientation": "portrait"}},
            ]
        }
    )

    assert [(op.target, op.props) for op in ops] == [("body", {"bold": True}), ("page", {"orientation": "portrait"})]
    assert "margins" in ignored


def test_aliases_and_levels():
    ops, _ = sanitize(
        {"operations": [{"target": "heading", "level": 2, "set": {"align": "centred"}}, {"target": "Table", "set": {"table_borders": False}}]}
    )
    assert (ops[0].target, ops[0].level, ops[0].props) == ("headings", 2, {"align": "center"})
    assert ops[1].target == "tables" and ops[1].props == {"table_borders": False}


def test_the_changes_are_described_in_words():
    text = describe([StyleOp("headings", None, {"color": "1F3864", "bold": True}), StyleOp("page", None, {"margins": {"top": 1.0}})])
    assert "headings: colour #1F3864, bold" in text and "the page: margins top 1 in" in text


class FakeModel:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def chat(self, system, user, temperature=0.2, **kwargs):
        self.calls.append((system, user))
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


def test_the_model_is_given_the_request_and_the_document_facts():
    model = FakeModel({"operations": [{"target": "body", "set": {"size": 12}}]})
    ops, _ = plan_style_changes(model, "make the text 12pt", "body: 5 paragraph(s)")

    assert ops[0].props == {"size": 12.0}
    assert "make the text 12pt" in model.calls[0][1] and "body: 5 paragraph(s)" in model.calls[0][1]


def test_an_unreadable_plan_is_reported():
    with pytest.raises(LayoutUnsupported):
        plan_style_changes(FakeModel("sorry, no"), "make it blue", "")
