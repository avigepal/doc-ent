"""Phase 4 — correlation modes, per your answer ("Both"):

  cross_document_correlate  - retrieved chunks span multiple files/groups;
                               LLM finds relationships/contradictions/trends,
                               still cites each source
  statistical_correlate     - pandas-aggregated numeric tables; REAL
                               pandas.DataFrame.corr() computes the numbers,
                               the LLM only narrates them (never estimates
                               math itself)

`correlate()` is the router: a single question can trigger either or both,
depending on whether the retrieved chunks span multiple files and whether
any tabular data was retrieved alongside them.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Protocol

import pandas as pd

from app.search.retrieval import RetrievedChunk

CROSS_DOC_SYSTEM_PROMPT = (
    "You are comparing excerpts from multiple different source documents. "
    "Identify relationships, contradictions, or trends across them. Cite "
    "the source number(s) in brackets, e.g. [1], for every claim. Write in "
    "Markdown, lead with the finding, and keep it short. Use only "
    "what the sources say -- no outside knowledge, no guessing. Report only "
    "relationships that are explicitly supported; if the sources have no "
    "real relationship to each other, reply exactly: No notable "
    "relationships found."
)

STATISTICAL_SYSTEM_PROMPT = (
    "You are given correlation coefficients that were already computed "
    "from the underlying data. Narrate what they mean in plain language. "
    "Use ONLY the numbers given to you — do not estimate or invent "
    "correlation values yourself."
)


class LLMClient(Protocol):
    def chat(self, system: str, user: str, temperature: float = 0.2) -> str: ...


@dataclass(frozen=True)
class CrossDocResult:
    answer: str
    sources: list[str]


@dataclass(frozen=True)
class StatisticalResult:
    answer: str
    correlation_summary: str


@dataclass(frozen=True)
class CorrelationReport:
    question: str
    cross_doc: CrossDocResult | None
    statistical: StatisticalResult | None


# ---------- cross-document mode ----------

def group_by_file(chunks: list[RetrievedChunk]) -> "OrderedDict[str, list[RetrievedChunk]]":
    grouped: "OrderedDict[str, list[RetrievedChunk]]" = OrderedDict()
    for chunk in chunks:
        grouped.setdefault(chunk.file_path, []).append(chunk)
    return grouped


def cross_document_correlate(question: str, chunks: list[RetrievedChunk], llm: LLMClient) -> CrossDocResult:
    grouped = group_by_file(chunks)
    sources = list(grouped.keys())

    sections = [
        f"[{i}] Source: {path}\n" + "\n".join(c.text for c in file_chunks)
        for i, (path, file_chunks) in enumerate(grouped.items(), start=1)
    ]
    prompt = f"Question: {question}\n\n" + "\n\n".join(sections)
    answer = llm.chat(system=CROSS_DOC_SYSTEM_PROMPT, user=prompt)
    return CrossDocResult(answer=answer, sources=sources)


# ---------- statistical mode ----------

def compute_correlations(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    numeric_frames = [df.select_dtypes(include="number") for df in tables.values()]
    combined = pd.concat(numeric_frames, axis=1)
    return combined.corr()


def format_correlation_matrix(corr: pd.DataFrame, min_abs: float = 0.5) -> str:
    cols = list(corr.columns)
    pairs: list[tuple[str, str, float]] = []
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            r = corr.iloc[i, j]
            if pd.notna(r) and abs(r) >= min_abs:
                pairs.append((cols[i], cols[j], float(r)))

    if not pairs:
        return f"No strong correlations found (|r| >= {min_abs:.2f})."

    pairs.sort(key=lambda t: -abs(t[2]))
    return "\n".join(f"{a} vs {b}: r={r:.2f}" for a, b, r in pairs)


def statistical_correlate(
    question: str,
    tables: dict[str, pd.DataFrame],
    llm: LLMClient,
    min_abs: float = 0.5,
) -> StatisticalResult:
    corr = compute_correlations(tables)
    summary = format_correlation_matrix(corr, min_abs=min_abs)
    prompt = f"Question: {question}\n\nComputed correlations:\n{summary}"
    answer = llm.chat(system=STATISTICAL_SYSTEM_PROMPT, user=prompt)
    return StatisticalResult(answer=answer, correlation_summary=summary)


# ---------- router ----------

# Cross-document findings are a separate LLM call that compares sources
# with each other. That only helps when the question is about relationships
# between documents; on an ordinary question it just restates the answer
# (and doubles the latency), so it's opt-in by wording.
_COMPARISON_CUES = re.compile(
    r"\b(?:compar\w*|versus|vs\.?|differ\w*|contradict\w*|conflict\w*|disagree\w*|inconsisten\w*|"
    r"consisten\w*|relationship\w*|relat(?:e|es|ed|ing)\b|correlat\w*|trends?|similar\w*|in common|"
    r"across\s+(?:the\s+|all\s+|these\s+|my\s+)?(?:documents?|files?|sources?)|"
    r"between\s+(?:the\s+|these\s+)?(?:documents?|files?|sources?)|"
    r"(?:both|each|every)\s+(?:of\s+the\s+)?(?:documents?|files?|sources?))",
    re.IGNORECASE,
)


def wants_cross_document(question: str) -> bool:
    return bool(_COMPARISON_CUES.search(question))


def correlate(
    question: str,
    chunks: list[RetrievedChunk],
    tables: dict[str, pd.DataFrame],
    llm: LLMClient,
    cross_doc_enabled: bool = True,
) -> CorrelationReport:
    distinct_files = {c.file_path for c in chunks}

    cross_doc = (
        cross_document_correlate(question, chunks, llm)
        if cross_doc_enabled and len(distinct_files) >= 2
        else None
    )
    statistical = statistical_correlate(question, tables, llm) if tables else None

    return CorrelationReport(question=question, cross_doc=cross_doc, statistical=statistical)
