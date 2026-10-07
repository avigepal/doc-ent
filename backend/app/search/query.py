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
from typing import Protocol

import pandas as pd

from app.search.ask import answer_grounded
from app.search.correlate import CrossDocResult, StatisticalResult, correlate
from app.search.retrieval import RetrievedChunk


class LLMClient(Protocol):
    def chat(self, system: str, user: str, temperature: float = 0.2) -> str: ...


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
    report = correlate(question, chunks, tables, llm)

    return QueryResult(
        question=question,
        answer=ask_result.answer,
        sources=ask_result.sources,
        grounded=ask_result.grounded,
        cross_doc=report.cross_doc,
        statistical=report.statistical,
    )
