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
    "what the sources say -- no outside knowledge, no guessing. A "
    "relationship can be a shared person, company or topic, a decision that "
    "follows from another document, or a sequence in time that links them; "
    "name the link and cite both documents. Report only relationships the "
    "sources explicitly support; if the sources have no real link to each "
    "other, reply exactly: No notable relationships found."
)

STATISTICAL_SYSTEM_PROMPT = (
    "You are given correlation coefficients that were already computed "
    "from the underlying data. Narrate what they mean in plain language. "
    "Use ONLY the numbers given to you — do not estimate or invent "
    "correlation values yourself. Mention every pair listed, with its r "
    "value as given. Describe strength from |r|: 0.9 and above is very "
    "strong, 0.7 to 0.9 strong, 0.5 to 0.7 moderate. Never say 'perfect' or "
    "'exact' unless r is exactly 1.000; a positive r means the two columns "
    "rise together, a negative r means one falls as the other rises. "
    "Correlation does not show that one causes the other."
)


class LLMClient(Protocol):
    def chat(self, system: str, user: str, temperature: float = 0.2) -> str: ...


@dataclass(frozen=True)
class CrossDocResult:
    answer: str
    sources: list[str]  # only the documents the answer cites
    retrieved: list[str] = ()  # every document that was compared


@dataclass(frozen=True)
class CorrelationPair:
    table: str
    first: str
    second: str
    r: float
    rows: int


@dataclass(frozen=True)
class StatisticalResult:
    answer: str
    correlation_summary: str
    pairs: list[CorrelationPair] = ()


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
    return CrossDocResult(answer=answer, sources=cited_sources(answer, sources), retrieved=sources)


_CITATION = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def cited_sources(answer: str, sources: list[str]) -> list[str]:
    """The documents an answer actually cites ([1], [2, 3]...), in the order they were numbered."""
    cited: set[int] = set()
    for group in _CITATION.findall(answer):
        cited.update(int(n) for n in re.findall(r"\d+", group))
    return [path for i, path in enumerate(sources, start=1) if i in cited]


def best_passages_per_file(chunks: list[RetrievedChunk], max_files: int, per_file: int = 2) -> list[RetrievedChunk]:
    """Up to per_file of the best-scoring passages from each of up to max_files documents. Widening retrieval
    and keeping a few passages per document lets a correlation see many documents, not only the few whose
    passages happened to rank highest; a short email keeps enough of its text to relate to another."""
    kept_ids: set[int] = set()
    counts: dict[str, int] = {}
    for chunk in sorted(chunks, key=lambda c: -c.score):
        if chunk.file_path not in counts and len(counts) >= max_files:
            continue
        if counts.get(chunk.file_path, 0) < per_file:
            counts[chunk.file_path] = counts.get(chunk.file_path, 0) + 1
            kept_ids.add(id(chunk))
    return [c for c in chunks if id(c) in kept_ids]


# ---------- statistical mode ----------

MIN_PAIR_ROWS = 3


def compute_correlations(tables: dict[str, pd.DataFrame]) -> list[CorrelationPair]:
    """Pearson r for every pair of numeric columns, computed inside each table only. Rows from
    different tables are not the same records, so correlating across tables would be meaningless."""
    found: list[CorrelationPair] = []
    for name, df in tables.items():
        numeric = df.select_dtypes(include="number")
        cols = list(numeric.columns)
        for i in range(len(cols)):
            for j in range(i + 1, len(cols)):
                both = numeric[[cols[i], cols[j]]].dropna()
                if len(both) < MIN_PAIR_ROWS:
                    continue
                r = both[cols[i]].corr(both[cols[j]])
                if pd.notna(r):
                    found.append(CorrelationPair(name, str(cols[i]), str(cols[j]), float(r), len(both)))
    return found


def format_correlation_matrix(pairs: list[CorrelationPair], min_abs: float = 0.5) -> str:
    strong = sorted((p for p in pairs if abs(p.r) >= min_abs), key=lambda p: -abs(p.r))
    if not strong:
        return f"No strong correlations found (|r| >= {min_abs:.2f})."
    return "\n".join(f"{p.first} vs {p.second} ({p.table}, {p.rows} rows): r={p.r:.4f}" for p in strong)


def statistical_correlate(
    question: str,
    tables: dict[str, pd.DataFrame],
    llm: LLMClient,
    min_abs: float = 0.5,
) -> StatisticalResult:
    pairs = compute_correlations(tables)
    summary = format_correlation_matrix(pairs, min_abs=min_abs)
    prompt = f"Question: {question}\n\nComputed correlations:\n{summary}"
    answer = llm.chat(system=STATISTICAL_SYSTEM_PROMPT, user=prompt)
    shown = tuple(sorted((p for p in pairs if abs(p.r) >= min_abs), key=lambda p: -abs(p.r)))
    return StatisticalResult(answer=answer, correlation_summary=summary, pairs=shown)


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


# A passage this weakly related to the question isn't worth comparing: it is
# what lets an unrelated file's content into the findings.
COMPARE_MIN_SCORE = 0.3


def compare_documents(
    question: str,
    chunks: list[RetrievedChunk],
    llm: LLMClient,
    min_score: float = COMPARE_MIN_SCORE,
) -> CrossDocResult | None:
    """Cross-document findings on demand (the dashboard's "Compare across
    documents" button). Only passages that actually match the question are
    compared; None when fewer than two different files are left, since there
    is nothing to compare then."""
    relevant = [c for c in chunks if c.score >= min_score]
    if len({c.file_path for c in relevant}) < 2:
        return None
    return cross_document_correlate(question, relevant, llm)


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


# ---------- JSON shape shared by the API, the chat stream and saved views ----------

def cross_doc_to_dict(result: CrossDocResult | None) -> dict | None:
    if result is None:
        return None
    return {"answer": result.answer, "sources": list(result.sources), "retrieved": list(result.retrieved)}


def statistical_to_dict(result: StatisticalResult | None) -> dict | None:
    if result is None:
        return None
    return {
        "answer": result.answer,
        "correlation_summary": result.correlation_summary,
        "pairs": [
            {"table": p.table, "first": p.first, "second": p.second, "r": round(p.r, 4), "rows": p.rows}
            for p in result.pairs
        ],
    }
