import pytest

from app.search.answer_spec import BRIEF, NORMAL, THOROUGH, AnswerSpec, parse_answer_spec
from app.search.ask import GROUNDED_SYSTEM_PROMPT
from app.search.query import stream_query
from app.search.retrieval import RetrievedChunk


@pytest.mark.parametrize(
    "message",
    ["what is the refund policy?", "who signed the contract", "mac", "", "   ", "is the warranty transferable?"],
)
def test_an_ordinary_question_gets_the_default_spec_and_changes_nothing(message):
    spec = parse_answer_spec(message)

    assert spec.is_default
    assert spec.instructions() == ""
    assert spec.retrieval_k(12) == 12


@pytest.mark.parametrize(
    "message",
    [
        "explain the refund policy in detail",
        "give me a detailed breakdown of the contract",
        "tell me everything about the Acme agreement",
        "a thorough explanation of the onboarding process",
        "Explain it in depth",
    ],
)
def test_wording_that_asks_for_detail_reads_more_passages_and_asks_for_specifics(message):
    spec = parse_answer_spec(message)

    assert spec.detail == THOROUGH
    assert spec.retrieval_k(12) == 36
    assert "detailed answer" in spec.instructions()


def test_a_detailed_answer_never_reads_more_than_the_cap_or_fewer_than_asked():
    spec = AnswerSpec(detail=THOROUGH)

    assert spec.retrieval_k(30) == 40
    assert spec.retrieval_k(50) == 50  # the person asked for more than the cap: keep that
    assert AnswerSpec(detail=BRIEF).retrieval_k(12) == 12


@pytest.mark.parametrize(
    "message", ["briefly, what is the notice period?", "one line: who is the landlord", "tl;dr of the lease", "yes or no: is it signed?"]
)
def test_wording_that_asks_for_a_short_answer_gets_one(message):
    spec = parse_answer_spec(message)

    assert spec.detail == BRIEF
    assert "one to three sentences" in spec.instructions()


def test_a_table_request_names_the_columns_in_order():
    spec = parse_answer_spec("make a table of every invoice with columns invoice number, date, vendor and total")

    assert spec.shape == "table"
    assert spec.columns == ("invoice number", "date", "vendor", "total")
    text = spec.instructions()
    assert "invoice number | date | vendor | total" in text
    assert "not stated" in text


def test_a_table_request_without_columns_leaves_the_choice_to_the_model():
    spec = parse_answer_spec("show the employees as a table")

    assert spec.shape == "table" and spec.columns == ()
    assert "columns that fit the question" in spec.instructions()


def test_a_sentence_after_the_word_columns_is_not_taken_for_column_names():
    spec = parse_answer_spec("put it in a table, the columns should be easy to read when printed on one page of paper")

    assert spec.shape == "table" and spec.columns == ()


def test_columns_are_not_repeated_and_the_extras_are_dropped():
    spec = parse_answer_spec("table with columns name, Name, date, " + ", ".join(f"c{i}" for i in range(20)))

    assert spec.columns[:2] == ("name", "date")
    assert len(spec.columns) == 12


def test_a_comparison_defaults_to_a_table():
    assert parse_answer_spec("compare plan A and plan B").shape == "table"
    assert parse_answer_spec("what is the difference between the two leases?").shape == "table"


def test_steps_and_lists_are_recognised():
    steps = parse_answer_spec("walk me through enabling Wake-on-LAN step by step")
    assert steps.shape == "steps" and steps.detail == THOROUGH
    assert "numbered list" in steps.instructions()

    assert parse_answer_spec("list all the vendors").shape == "list"
    assert parse_answer_spec("give me the key dates as bullet points").shape == "list"


def test_a_short_answer_is_not_turned_into_a_list():
    spec = parse_answer_spec("briefly list the vendors")

    assert spec.detail == BRIEF and spec.shape == "prose"


# ---------- in the answer prompt ----------


class FakeLLM:
    def __init__(self):
        self.systems: list[str] = []

    def chat(self, system, user, temperature=0.2):
        return "{}"

    def chat_stream(self, system, user, temperature=0.2):
        self.systems.append(system)
        yield "ok"


def _chunks():
    return [RetrievedChunk(file_path="a.pdf", heading="H", text="text", score=0.9)]


def test_the_requested_shape_is_added_to_the_answer_prompt():
    llm = FakeLLM()
    spec = parse_answer_spec("explain the policy in detail as a table with columns clause, effect")

    list(stream_query("explain the policy", _chunks(), {}, llm, spec=spec))

    [system] = llm.systems
    assert system.startswith(GROUNDED_SYSTEM_PROMPT)
    assert "detailed answer" in system and "clause | effect" in system


def test_no_spec_or_the_default_spec_leaves_the_prompt_exactly_as_it_was():
    for spec in (None, AnswerSpec(), parse_answer_spec("what is x?")):
        llm = FakeLLM()
        list(stream_query("what is x?", _chunks(), {}, llm, spec=spec))
        assert llm.systems == [GROUNDED_SYSTEM_PROMPT]


def test_normal_detail_is_the_default():
    assert AnswerSpec().detail == NORMAL
