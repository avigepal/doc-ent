"""Unified query: one question in, everything the corpus can say about it
out — grounded answer, cross-document findings, and statistical findings,
whichever apply. This is what the dashboard's single Ask box calls,
instead of making the user pick "Ask" vs "Correlate" up front.

Pure orchestration over already-tested pieces (answer_grounded, correlate)
— chunks/tables are retrieved once by the caller (see app/tasks/
correlate.py:query) and reused for both, rather than retrieving twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Protocol, TypedDict

import pandas as pd

from app.search.ask import GROUNDED_SYSTEM_PROMPT, NOT_FOUND_MESSAGE, answer_grounded, build_context
from app.search.correlate import CrossDocResult, StatisticalResult, correlate, wants_cross_document
from app.search.retrieval import RetrievedChunk


class LLMClient(Protocol):
    def chat(self, system: str, user: str, temperature: float = 0.2) -> str: ...
    def chat_stream(self, system: str, user: str, temperature: float = 0.2) -> Iterator[str]: ...


# Sent when the model streams back no text at all (it spent its output on
# hidden reasoning, or the request was cut off). Without it the answer card
# stays blank and the empty answer is saved to history.
EMPTY_ANSWER_MESSAGE = "The model didn't return an answer. Please try again."


def _stream_answer(llm: LLMClient, system: str, user: str) -> "Iterator[StreamEvent]":
    produced = False
    for piece in llm.chat_stream(system=system, user=user):
        produced = True
        yield {"event": "token", "data": {"text": piece}}
    if not produced:
        yield {"event": "token", "data": {"text": EMPTY_ANSWER_MESSAGE}}


CHAT_SYSTEM_PROMPT = (
    "You are a helpful assistant. Answer directly and conversationally — "
    "there is no document corpus attached to this question."
)


@dataclass(frozen=True)
class QueryResult:
    question: str
    answer: str
    sources: list[str]
    grounded: bool
    cross_doc: CrossDocResult | None
    statistical: StatisticalResult | None


def run_query(
    question: str,
    chunks: list[RetrievedChunk],
    tables: dict[str, pd.DataFrame],
    llm: LLMClient,
    similarity_threshold: float = 0.3,
) -> QueryResult:
    ask_result = answer_grounded(question, chunks, llm, similarity_threshold=similarity_threshold)
    report = correlate(question, chunks, tables, llm, cross_doc_enabled=wants_cross_document(question))

    return QueryResult(
        question=question,
        answer=ask_result.answer,
        sources=ask_result.sources,
        grounded=ask_result.grounded,
        cross_doc=report.cross_doc,
        statistical=report.statistical,
    )


class StreamEvent(TypedDict):
    event: str  # "meta" | "token" | "extra"
    data: dict


def stream_query(
    question: str,
    chunks: list[RetrievedChunk],
    tables: dict[str, pd.DataFrame],
    llm: LLMClient,
    chat_only: bool = False,
    similarity_threshold: float = 0.3,
) -> Iterator[StreamEvent]:
    """Streaming counterpart to run_query/run_chat — yields events instead
    of returning one QueryResult, so a caller (see app/main.py's
    /query/stream) can forward each piece to the client as it's produced.

    Only the main answer actually streams. Statistical findings (when they
    apply) come from correlate(), which makes its own separate LLM call
    with a different prompt shape, so they arrive as one blocking "extra"
    event after the main answer finishes. Cross-document findings are NOT
    produced here: on an ordinary question they only restate the answer, so
    the dashboard asks for them on demand (see compare_documents and
    POST /query/compare). "cross_doc" is therefore always None here.

    Event order is always: one "meta" (sources + grounded, known before
    any generation starts), then zero or more "token" (answer text
    pieces, concatenate in order for the full answer), then exactly one
    "extra" (cross_doc/statistical, both None for chat_only or an
    ungrounded answer)."""
    if chat_only:
        yield {"event": "meta", "data": {"sources": [], "grounded": False}}
        yield from _stream_answer(llm, CHAT_SYSTEM_PROMPT, question)
        yield {"event": "extra", "data": {"cross_doc": None, "statistical": None}}
        return

    if not chunks or max(c.score for c in chunks) < similarity_threshold:
        yield {"event": "meta", "data": {"sources": [], "grounded": False}}
        yield {"event": "token", "data": {"text": NOT_FOUND_MESSAGE}}
        yield {"event": "extra", "data": {"cross_doc": None, "statistical": None}}
        return

    context, sources = build_context(chunks)
    yield {"event": "meta", "data": {"sources": sources, "grounded": True}}
    prompt = f"Question: {question}\n\nSources:\n{context}"
    yield from _stream_answer(llm, GROUNDED_SYSTEM_PROMPT, prompt)

    report = correlate(question, chunks, tables, llm, cross_doc_enabled=False)
    yield {
        "event": "extra",
        "data": {
            "cross_doc": None if report.cross_doc is None else {
                "answer": report.cross_doc.answer,
                "sources": report.cross_doc.sources,
            },
            "statistical": None if report.statistical is None else {
                "answer": report.statistical.answer,
                "correlation_summary": report.statistical.correlation_summary,
            },
        },
    }


def run_chat(question: str, llm: LLMClient) -> QueryResult:
    """No retrieval at all — used when the dashboard's Scope has nothing
    selected (neither "All" nor a folder), so the user is talking to the
    model directly rather than asking about the corpus. Same QueryResult
    shape as run_query so the dashboard's result card doesn't need a
    special case: grounded is always False here and sources/cross_doc/
    statistical are always empty, since there's nothing to cite."""
    answer = llm.chat(system=CHAT_SYSTEM_PROMPT, user=question)
    return QueryResult(
        question=question,
        answer=answer,
        sources=[],
        grounded=False,
        cross_doc=None,
        statistical=None,
    )
