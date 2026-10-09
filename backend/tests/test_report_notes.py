"""Saved notes, parallel reading and follow-ups of the full report (see app/search/report.py)."""

import threading
import time

import pytest

from app.history.store import pick_report_context
from app.routing.router import looks_like_report_refinement
from app.search.report import (
    CONDENSE_SYSTEM_PROMPT,
    MAP_SYSTEM_PROMPT,
    REFINE_SYSTEM_PROMPT,
    ReportDocument,
    ReportLimits,
    refine_events,
    report_events,
    request_key,
)

LIMITS = ReportLimits(batch_chars=200, max_files=10, max_chars=10_000, notes_chars=1_000)


class FakeLLM:
    """Note-taking replies come from notes_by_doc (matched on the document name in the
    prompt); anything else is "NONE"."""

    def __init__(self, notes_by_doc=None, report_pieces=("The ", "report.")):
        self.notes_by_doc = notes_by_doc or {}
        self.report_pieces = list(report_pieces)
        self.calls = []

    def chat(self, system, user, temperature=0.2, **kwargs):
        self.calls.append((system, user))
        if system == MAP_SYSTEM_PROMPT:
            for name, notes in self.notes_by_doc.items():
                if f"Document: {name}" in user:
                    return notes
            return "NONE"
        if system == CONDENSE_SYSTEM_PROMPT:
            return "short notes"
        raise AssertionError("unexpected chat call")

    def chat_stream(self, system, user, temperature=0.2):
        self.calls.append((system, user))
        yield from self.report_pieces


class MemoryStore:
    def __init__(self):
        self.saved = {}
        self.puts = 0

    def get(self, file_id, key, content_hash):
        entry = self.saved.get((file_id, key))
        return entry[1] if entry and entry[0] == content_hash else None

    def put(self, file_id, key, content_hash, notes):
        self.puts += 1
        self.saved[(file_id, key)] = (content_hash, notes)


def _doc(file_id, name, text):
    return ReportDocument(f"/data/raw/people/{name}", [("", text)], file_id)


def _map_calls(llm):
    return [c for c in llm.calls if c[0] == MAP_SYSTEM_PROMPT]


def _texts(events, kind):
    return [e["data"]["text"] for e in events if e["event"] == kind]


# ---------- saved notes ----------

def test_request_key_ignores_case_and_spacing_but_not_the_words():
    assert request_key("Full  report on THIS person") == request_key("full report on this person")
    assert request_key("full report on this person") != request_key("full report on that person")


def test_a_second_report_on_the_same_request_reads_nothing_again():
    docs = [_doc(1, "cv.pdf", "Asha Rao"), _doc(2, "letter.pdf", "worked at Acme")]
    llm = FakeLLM({"cv.pdf": "- Born 1990", "letter.pdf": "- Acme"})
    store = MemoryStore()

    list(report_events("report on Asha", docs, llm, LIMITS, store))
    assert len(_map_calls(llm)) == 2 and store.puts == 2

    llm2 = FakeLLM({"cv.pdf": "- SHOULD NOT BE USED"})
    events = list(report_events("Report  on asha", docs, llm2, LIMITS, store))

    assert _map_calls(llm2) == []  # nothing was read
    assert any("Using saved notes for 2 of 2 documents" in s for s in _texts(events, "status"))
    assert "- Born 1990" in llm2.calls[-1][1]  # the report is written from the saved notes


def test_a_changed_document_is_read_again_and_the_others_are_not():
    store = MemoryStore()
    docs = [_doc(1, "cv.pdf", "Asha Rao"), _doc(2, "letter.pdf", "worked at Acme")]
    list(report_events("report on Asha", docs, FakeLLM({"cv.pdf": "- old", "letter.pdf": "- Acme"}), LIMITS, store))

    edited = [_doc(1, "cv.pdf", "Asha Rao, now a director"), docs[1]]
    llm = FakeLLM({"cv.pdf": "- new", "letter.pdf": "- Acme"})
    list(report_events("report on Asha", edited, llm, LIMITS, store))

    reads = _map_calls(llm)
    assert len(reads) == 1 and "Document: cv.pdf" in reads[0][1]
    assert "- new" in llm.calls[-1][1] and "- old" not in llm.calls[-1][1]


def test_a_different_request_does_not_reuse_notes_written_for_another():
    store = MemoryStore()
    docs = [_doc(1, "cv.pdf", "Asha Rao")]
    list(report_events("everything about Asha", docs, FakeLLM({"cv.pdf": "- a"}), LIMITS, store))

    llm = FakeLLM({"cv.pdf": "- b"})
    list(report_events("only her education", docs, llm, LIMITS, store))

    assert len(_map_calls(llm)) == 1


def test_nothing_relevant_is_remembered_too():
    store = MemoryStore()
    docs = [_doc(1, "misc.pdf", "lunch menu"), _doc(2, "cv.pdf", "Asha")]
    list(report_events("report on Asha", docs, FakeLLM({"cv.pdf": "- a"}), LIMITS, store))

    llm = FakeLLM({})
    list(report_events("report on Asha", docs, llm, LIMITS, store))

    assert _map_calls(llm) == []  # misc.pdf's "nothing here" was saved as well


def test_documents_without_a_file_id_are_never_saved():
    store = MemoryStore()
    doc = ReportDocument("/data/raw/people/a.pdf", [("", "x")])

    list(report_events("report on x", [doc], FakeLLM({"a.pdf": "- a"}), LIMITS, store))

    assert store.puts == 0


# ---------- reading side by side ----------

class SlowLLM(FakeLLM):
    """Takes a moment per note-taking call and records how many ran at once."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lock = threading.Lock()
        self.running = 0
        self.peak = 0

    def chat(self, system, user, temperature=0.2, **kwargs):
        with self.lock:
            self.running += 1
            self.peak = max(self.peak, self.running)
        time.sleep(0.05)
        try:
            return super().chat(system, user, temperature, **kwargs)
        finally:
            with self.lock:
                self.running -= 1


def _limits(parallel):
    return ReportLimits(batch_chars=200, max_files=10, max_chars=10_000, notes_chars=1_000, parallel=parallel)


def test_documents_are_read_side_by_side_up_to_the_limit():
    docs = [_doc(i, f"d{i}.pdf", "text") for i in range(1, 7)]
    llm = SlowLLM({f"d{i}.pdf": "- fact" for i in range(1, 7)})

    events = list(report_events("report on x", docs, llm, _limits(3)))

    assert llm.peak == 3
    meta = next(e for e in events if e["event"] == "meta")
    assert meta["data"]["sources"] == [d.path for d in docs]  # numbering stays in document order


def test_one_at_a_time_when_parallel_is_one():
    docs = [_doc(i, f"d{i}.pdf", "text") for i in range(1, 5)]
    llm = SlowLLM({f"d{i}.pdf": "- fact" for i in range(1, 5)})

    list(report_events("report on x", docs, llm, _limits(1)))

    assert llm.peak == 1


def test_a_failed_read_stops_the_report_with_the_error():
    class Failing(FakeLLM):
        def chat(self, system, user, temperature=0.2, **kwargs):
            if system == MAP_SYSTEM_PROMPT:
                raise RuntimeError("llama-server unreachable")
            return super().chat(system, user, temperature, **kwargs)

    with pytest.raises(RuntimeError, match="unreachable"):
        list(report_events("report on x", [_doc(1, "a.pdf", "x")], Failing(), _limits(3)))


# ---------- follow-ups to a report ----------

def test_a_follow_up_rewrites_the_report_from_the_saved_notes_without_reading_again():
    store = MemoryStore()
    docs = [_doc(1, "cv.pdf", "Asha Rao"), _doc(2, "letter.pdf", "Acme")]
    list(report_events("report on Asha", docs, FakeLLM({"cv.pdf": "- Born 1990", "letter.pdf": "- Acme"}), LIMITS, store))

    llm = FakeLLM({})
    events = list(
        refine_events("report on Asha", "make it shorter", "### Overview\nAsha was born in 1990 [1].", docs, llm, LIMITS, store)
    )

    assert _map_calls(llm) == []
    system, prompt = llm.calls[-1]
    assert system == REFINE_SYSTEM_PROMPT
    assert "Instruction: make it shorter" in prompt
    assert "Asha was born in 1990 [1]." in prompt  # the previous report
    assert "[1] cv.pdf\n- Born 1990" in prompt  # same numbering as before
    assert "".join(_texts(events, "token")) == "The report."
    meta = next(e for e in events if e["event"] == "meta")
    assert meta["data"]["sources"] == [d.path for d in docs]


@pytest.mark.parametrize(
    "message",
    [
        "make it shorter",
        "add a section on education",
        "please translate it to Hindi",
        "can you remove the contact details from the report",
        "expand the overview",
        "rewrite this in a formal tone",
        "include a table of dates",
    ],
)
def test_changes_to_the_report_are_recognised(message):
    assert looks_like_report_refinement(message)


@pytest.mark.parametrize(
    "message",
    ["who is the author?", "what is her date of birth", "tell me about the company", "thanks", "list the files", "", "make it shorter " * 30],
)
def test_other_messages_are_not_changes_to_the_report(message):
    assert not looks_like_report_refinement(message)


class Turn:
    def __init__(self, id, route, question, answer):
        self.id, self.route, self.question, self.answer = id, route, question, answer


def test_the_original_request_and_latest_text_come_from_the_chat_history():
    newest_first = [
        Turn(4, "report_refine", "make it shorter", "short v2"),
        Turn(3, "report_refine", "add education", "longer v1"),
        Turn(2, "report", "full report on Asha", "first report"),
        Turn(1, "search", "who is Asha?", "an engineer"),
    ]

    assert pick_report_context(newest_first) == ("full report on Asha", "short v2")


def test_a_regenerate_only_sees_what_came_before_the_turn_it_replaces():
    newest_first = [
        Turn(4, "report_refine", "make it shorter", "short v2"),
        Turn(3, "report_refine", "add education", "longer v1"),
        Turn(2, "report", "full report on Asha", "first report"),
    ]

    assert pick_report_context(newest_first, before_id=4) == ("full report on Asha", "longer v1")
    assert pick_report_context(newest_first, before_id=3) == ("full report on Asha", "first report")
    assert pick_report_context(newest_first, before_id=2) is None


def test_no_follow_up_context_when_the_last_turn_was_not_a_report():
    newest_first = [Turn(3, "search", "who is she?", "an engineer"), Turn(2, "report", "full report on Asha", "r")]

    assert pick_report_context(newest_first) is None
    assert pick_report_context([]) is None
