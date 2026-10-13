import json

import pytest

from app.config import settings
from app.search.answer_spec import AnswerSpec, parse_answer_spec
from app.search.query import EMPTY_ANSWER_MESSAGE, stream_query
from app.search.retrieval import RetrievedChunk
from app.search.sectioned import (
    MAX_SECTIONS,
    Section,
    parse_outline,
    plan_sections,
    section_events,
    should_write_in_sections,
)

DETAILED = parse_answer_spec("explain the supply terms in detail")
BIG = "x" * 7000


def _outline(*titles):
    return json.dumps({"sections": [{"title": t, "focus": f"about {t}"} for t in titles]})


# ---------- when ----------


def test_only_a_detailed_prose_answer_over_enough_material_is_written_in_sections():
    assert should_write_in_sections(DETAILED, BIG, enabled=True, min_chars=6000)
    assert not should_write_in_sections(DETAILED, "short", enabled=True, min_chars=6000)  # fits one call
    assert not should_write_in_sections(DETAILED, BIG, enabled=False, min_chars=6000)
    assert not should_write_in_sections(None, BIG, enabled=True, min_chars=6000)
    assert not should_write_in_sections(AnswerSpec(), BIG, enabled=True, min_chars=6000)  # not detailed
    table = parse_answer_spec("explain in detail as a table")
    assert not should_write_in_sections(table, BIG, enabled=True, min_chars=6000)
    steps = parse_answer_spec("walk me through it step by step")
    assert not should_write_in_sections(steps, BIG, enabled=True, min_chars=6000)


# ---------- reading the outline ----------


def test_a_good_outline_is_read_in_order():
    sections = parse_outline(_outline("Overview", "Payment", "Delivery"))

    assert [s.title for s in sections] == ["Overview", "Payment", "Delivery"]
    assert sections[1].focus == "about Payment"


def test_an_outline_inside_other_text_or_with_decorated_titles_is_cleaned():
    text = 'Sure! ```json\n{"sections": [{"title": "## Overview:", "focus": "a"}, {"title": "**Payment**", "focus": "b"}]}\n```'

    assert [s.title for s in parse_outline(text)] == ["Overview", "Payment"]


def test_repeated_blank_or_extra_sections_are_dropped():
    titles = ["A", "a", "", "B", "C", "D", "E", "F", "G", "H"]
    sections = parse_outline(_outline(*titles))

    assert [s.title for s in sections] == ["A", "B", "C", "D", "E", "F"]
    assert len(sections) == MAX_SECTIONS


@pytest.mark.parametrize(
    "junk",
    ["", "no json here", "{}", '{"sections": "oops"}', '{"sections": [1, 2]}', _outline("Only one"), '{"sections": [', "[1, 2]"],
)
def test_an_unusable_outline_gives_no_sections(junk):
    assert parse_outline(junk) == []


# ---------- planning ----------


class PlanLLM:
    def __init__(self, reply=None, fail_first=False, fail_always=False):
        self.reply, self.fail_first, self.fail_always = reply, fail_first, fail_always
        self.calls = []

    def chat(self, system, user, temperature=0.2, **kwargs):
        self.calls.append(kwargs)
        if self.fail_always or (self.fail_first and len(self.calls) == 1):
            raise RuntimeError("server said no")
        return self.reply


def test_planning_asks_for_json_without_thinking():
    llm = PlanLLM(_outline("A", "B"))

    assert [s.title for s in plan_sections("q", "ctx", llm)] == ["A", "B"]
    [call] = llm.calls
    assert call["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert "response_format" in call["extra_body"]


def test_a_server_that_rejects_json_mode_is_asked_again_without_it():
    llm = PlanLLM(_outline("A", "B"), fail_first=True)

    assert len(plan_sections("q", "ctx", llm)) == 2
    assert "response_format" not in llm.calls[1]["extra_body"]


def test_a_planning_failure_gives_no_sections_instead_of_an_error():
    assert plan_sections("q", "ctx", PlanLLM(fail_always=True)) == []


# ---------- writing ----------


class WriterLLM(PlanLLM):
    def __init__(self, outline, pieces=None):
        super().__init__(outline)
        self.pieces = pieces if pieces is not None else ["text"]
        self.prompts = []

    def chat_stream(self, system, user, temperature=0.2):
        self.prompts.append((system, user))
        yield from self.pieces


def _chunks():
    return [RetrievedChunk(file_path="a.pdf", heading="H", text=BIG, score=0.9)]


@pytest.fixture
def sections_on(monkeypatch):
    monkeypatch.setattr(settings, "sectioned_answers", True)
    monkeypatch.setattr(settings, "sectioned_min_chars", 6000)


def test_each_section_is_written_by_its_own_call_that_knows_the_others(sections_on):
    llm = WriterLLM(_outline("Overview", "Payment"), pieces=["abc"])
    events = list(section_events("q", "ctx", DETAILED, parse_outline(_outline("Overview", "Payment")), llm))

    text = "".join(e["data"]["text"] for e in events if e["event"] == "token")
    assert text == "### Overview\n\nabc\n\n### Payment\n\nabc"
    assert [e["data"]["text"] for e in events if e["event"] == "status"] == [
        "Writing part 1 of 2: Overview…",
        "Writing part 2 of 2: Payment…",
    ]
    assert len(llm.prompts) == 2
    system, user = llm.prompts[1]
    assert "ONE section" in system and "detailed answer" in system
    assert "2. Payment: about Payment  <- YOU WRITE THIS ONE" in user and "1. Overview: about Overview\n" in user


def test_a_section_that_writes_nothing_gets_no_heading():
    class Quiet(WriterLLM):
        def chat_stream(self, system, user, temperature=0.2):
            self.prompts.append((system, user))
            yield from (["body"] if len(self.prompts) == 2 else [])

    events = list(section_events("q", "ctx", DETAILED, [Section("A", ""), Section("B", "")], Quiet(None)))

    assert "".join(e["data"]["text"] for e in events if e["event"] == "token") == "### B\n\nbody"


def test_a_detailed_question_over_enough_text_streams_as_sections(sections_on):
    llm = WriterLLM(_outline("Overview", "Terms"), pieces=["p"])

    events = list(stream_query("explain the supply terms in detail", _chunks(), {}, llm, spec=DETAILED))

    assert events[0]["event"] == "meta"
    text = "".join(e["data"]["text"] for e in events if e["event"] == "token")
    assert text.startswith("### Overview") and "### Terms" in text
    assert events[-1]["event"] == "extra"
    assert len(llm.prompts) == 2


def test_if_planning_fails_the_ordinary_single_answer_is_written(sections_on):
    llm = WriterLLM("not json", pieces=["one piece"])

    events = list(stream_query("explain in detail", _chunks(), {}, llm, spec=DETAILED))

    assert "".join(e["data"]["text"] for e in events if e["event"] == "token") == "one piece"
    assert len(llm.prompts) == 1


def test_the_switch_turns_it_off(monkeypatch):
    monkeypatch.setattr(settings, "sectioned_answers", False)
    llm = WriterLLM(_outline("A", "B"), pieces=["one piece"])

    events = list(stream_query("explain in detail", _chunks(), {}, llm, spec=DETAILED))

    assert len(llm.prompts) == 1 and llm.calls == []  # no planning call either
    assert "".join(e["data"]["text"] for e in events if e["event"] == "token") == "one piece"


def test_an_ordinary_question_never_plans(sections_on):
    llm = WriterLLM(_outline("A", "B"), pieces=["answer"])

    list(stream_query("what is x?", _chunks(), {}, llm, spec=parse_answer_spec("what is x?")))

    assert llm.calls == [] and len(llm.prompts) == 1


def test_no_text_from_any_section_gives_the_usual_empty_message(sections_on):
    llm = WriterLLM(_outline("A", "B"), pieces=[])

    events = list(stream_query("explain in detail", _chunks(), {}, llm, spec=DETAILED))

    assert [e["data"]["text"] for e in events if e["event"] == "token"] == [EMPTY_ANSWER_MESSAGE]
