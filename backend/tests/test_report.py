import pytest

from app.routing.router import RouteDecision, decide_route, looks_like_report
from app.search.report import (
    CONDENSE_SYSTEM_PROMPT,
    MAP_SYSTEM_PROMPT,
    NO_DOCUMENTS_MESSAGE,
    REPORT_SYSTEM_PROMPT,
    ReportDocument,
    ReportLimits,
    batch_sections,
    is_nothing,
    report_events,
    scope_problem,
)

LIMITS = ReportLimits(batch_chars=200, max_files=5, max_chars=10_000, notes_chars=1_000)


class FakeLLM:
    """Replies to note-taking by file: notes_by_doc maps a document name found in the
    prompt to the notes to return; anything else is "NONE"."""

    def __init__(self, notes_by_doc: dict[str, str] | None = None, report_pieces=("The ", "report.")):
        self.notes_by_doc = notes_by_doc or {}
        self.report_pieces = list(report_pieces)
        self.calls: list[tuple[str, str]] = []

    def chat(self, system: str, user: str, temperature: float = 0.2, **kwargs) -> str:
        self.calls.append((system, user))
        if system == MAP_SYSTEM_PROMPT:
            for name, notes in self.notes_by_doc.items():
                if f"Document: {name}" in user:
                    return notes
            return "NONE"
        if system == CONDENSE_SYSTEM_PROMPT:
            return "short notes"
        raise AssertionError("unexpected chat call")

    def chat_stream(self, system: str, user: str, temperature: float = 0.2):
        self.calls.append((system, user))
        yield from self.report_pieces


def _doc(name: str, *texts: str) -> ReportDocument:
    return ReportDocument(f"/data/raw/people/{name}", [("", t) for t in texts])


def _kinds(events):
    return [e["event"] for e in events]


# ---------- batching ----------

def test_batch_sections_groups_consecutive_sections_up_to_the_limit():
    sections = [("A", "x" * 60), ("B", "y" * 60), ("C", "z" * 60)]

    batches = batch_sections(sections, 150)

    assert len(batches) == 2
    assert batches[0].startswith("## A") and "## B" in batches[0]
    assert batches[1].startswith("## C")


def test_batch_sections_cuts_a_section_longer_than_the_limit_and_loses_nothing():
    words = " ".join(f"w{i}" for i in range(200))

    batches = batch_sections([("", words)], 100)

    assert all(len(b) <= 100 for b in batches)
    assert " ".join(batches).split() == words.split()


def test_batch_sections_of_nothing_is_no_batches():
    assert batch_sections([], 100) == []


@pytest.mark.parametrize("reply", ["NONE", "none.", "  None  ", "", "   "])
def test_replies_that_mean_nothing_relevant(reply):
    assert is_nothing(reply)


def test_notes_are_not_nothing():
    assert not is_nothing("- Born in 1980 in Pune")


# ---------- scope limits ----------

def test_scope_problem_for_no_documents():
    assert scope_problem([], LIMITS) == NO_DOCUMENTS_MESSAGE


def test_scope_problem_for_too_many_files_or_too_much_text():
    many = [_doc(f"{i}.pdf", "x") for i in range(6)]
    big = [_doc("big.pdf", "x" * 20_000)]

    assert "6 documents" in scope_problem(many, LIMITS)
    assert "20k characters" in scope_problem(big, LIMITS)
    assert scope_problem([_doc("a.pdf", "x")], LIMITS) is None


# ---------- the report ----------

def test_every_document_is_read_and_the_report_is_written_from_the_notes():
    docs = [_doc("cv.pdf", "Asha Rao, born 1990."), _doc("letter.pdf", "Asha worked at Acme."), _doc("misc.pdf", "lunch menu")]
    llm = FakeLLM({"cv.pdf": "- Born 1990", "letter.pdf": "- Worked at Acme"})

    events = list(report_events("make a full report on this person", docs, llm, LIMITS))

    # reads each document, then writes -- and says what it is doing
    statuses = [e["data"]["text"] for e in events if e["event"] == "status"]
    assert statuses[:3] == [
        "Reading document 1 of 3: cv.pdf…",
        "Reading document 2 of 3: letter.pdf…",
        "Reading document 3 of 3: misc.pdf…",
    ]
    assert statuses[-1] == "Writing the report…"

    # only the documents that had something are sources, in order, numbered for citations
    meta = next(e for e in events if e["event"] == "meta")
    assert meta["data"] == {
        "sources": ["/data/raw/people/cv.pdf", "/data/raw/people/letter.pdf"],
        "grounded": True,
    }
    assert "".join(e["data"]["text"] for e in events if e["event"] == "token") == "The report."
    assert _kinds(events)[-1] == "extra"

    system, prompt = llm.calls[-1]
    assert system == REPORT_SYSTEM_PROMPT
    assert "[1] cv.pdf\n- Born 1990" in prompt
    assert "[2] letter.pdf\n- Worked at Acme" in prompt
    assert "had nothing relevant: misc.pdf" in prompt
    assert "make a full report on this person" in prompt


def test_a_long_document_is_read_in_parts_and_its_notes_are_joined():
    doc = _doc("long.pdf", *("para " * 30 for _ in range(4)))  # 4 x 150 chars with a 200 char batch
    llm = FakeLLM({"long.pdf": "- a fact"})

    events = list(report_events("report on x", [doc], llm, LIMITS))

    statuses = [e["data"]["text"] for e in events if e["event"] == "status"]
    assert any("(part 1 of" in s for s in statuses)
    map_calls = [c for c in llm.calls if c[0] == MAP_SYSTEM_PROMPT]
    assert len(map_calls) >= 2
    assert "- a fact\n- a fact" in llm.calls[-1][1]


def test_when_nothing_is_relevant_it_says_so_instead_of_inventing_a_report():
    llm = FakeLLM({})

    events = list(report_events("report on x", [_doc("a.pdf", "x"), _doc("b.pdf", "y")], llm, LIMITS))

    meta = next(e for e in events if e["event"] == "meta")
    assert meta["data"] == {"sources": [], "grounded": False}
    text = "".join(e["data"]["text"] for e in events if e["event"] == "token")
    assert "all 2 of your documents" in text
    assert all(c[0] != REPORT_SYSTEM_PROMPT for c in llm.calls)  # the report model was never asked


def test_an_empty_or_oversized_scope_never_calls_the_model():
    llm = FakeLLM()

    empty = list(report_events("report on x", [], llm, LIMITS))
    huge = list(report_events("report on x", [_doc(f"{i}.pdf", "x") for i in range(9)], llm, LIMITS))

    assert llm.calls == []
    assert "".join(e["data"]["text"] for e in empty if e["event"] == "token") == NO_DOCUMENTS_MESSAGE
    assert "9 documents" in "".join(e["data"]["text"] for e in huge if e["event"] == "token")


def test_notes_that_are_too_long_for_one_prompt_are_condensed_first():
    docs = [_doc("a.pdf", "x"), _doc("b.pdf", "y")]
    llm = FakeLLM({"a.pdf": "- fact " * 200, "b.pdf": "- short"})  # a.pdf's notes exceed the 1000 char budget

    events = list(report_events("report on x", docs, llm, LIMITS))

    condense_calls = [c for c in llm.calls if c[0] == CONDENSE_SYSTEM_PROMPT]
    assert len(condense_calls) == 1  # only the long one
    assert "[1] a.pdf\nshort notes" in llm.calls[-1][1]
    assert any(e["event"] == "status" and "Condensing notes from a.pdf" in e["data"]["text"] for e in events)


# ---------- routing ----------

@pytest.mark.parametrize(
    "message",
    [
        "make a full report on this person",
        "Write a detailed profile of Asha Rao",
        "give me a complete report about the candidate",
        "prepare a comprehensive dossier on Mr Shah",
        "I need an in-depth report on this applicant",
    ],
)
def test_report_requests_are_recognised(message):
    assert looks_like_report(message)


@pytest.mark.parametrize(
    "message",
    [
        "summarize the complete report",
        "what does the report say about revenue?",
        "make it shorter",
        "who is Asha Rao?",
        "list the files",
    ],
)
def test_other_messages_are_not_report_requests(message):
    assert not looks_like_report(message)


def test_a_report_request_is_routed_without_a_model_call_even_with_files_attached():
    class NoModel:
        def chat(self, *a, **k):
            raise AssertionError("routing should not call the model for this")

    decision = decide_route(
        "make a full report on this person", has_attachments=True, history=[], llm=NoModel(), attachment_names=["cv.pdf"]
    )

    assert decision == RouteDecision("report", "make a full report on this person", "rules")
