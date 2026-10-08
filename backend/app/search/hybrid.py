"""Pure helpers for hybrid retrieval (vector + keyword + file metadata),
split out of pgvector_retrieval.py so they're unit-testable without a
database. See pgvector_retrieval.py for the SQL that feeds them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Reciprocal Rank Fusion constant: 60 is the value from the original RRF
# paper and is rarely worth tuning.
RRF_K = 60

# A chunk that matched the question's keywords (or its file's metadata) is
# relevant even when its embedding similarity is low -- exact IDs, names
# and codes embed poorly. The caller rejects answers whose best score is
# under a similarity threshold (0.3 by default), so a keyword hit gets at
# least this score to avoid being thrown away by that gate.
KEYWORD_MATCH_FLOOR = 0.5

_WORD_RE = re.compile(r"\w+", re.UNICODE)

# Question filler that would otherwise match nearly every chunk or every
# file path. Postgres's 'english' config drops these for chunk text, but
# the 'simple' config used for file metadata does not.
_STOPWORDS = frozenset(
    "a an and are as at be by for from how i in is it of on or that the this to was what when where which who why with"
    " do does did can could should would about any all me my show find tell give list".split()
)


def build_tsquery_terms(question: str, max_terms: int = 12) -> list[str]:
    """Distinct, lowercased content words from the question. \\w+ only, so
    the result is safe to splice into a to_tsquery() string."""
    seen: list[str] = []
    for word in _WORD_RE.findall(question.lower()):
        if len(word) < 2 or word in _STOPWORDS or word in seen:
            continue
        seen.append(word)
        if len(seen) >= max_terms:
            break
    return seen


def build_or_tsquery(question: str) -> str | None:
    """'a | b | c' for to_tsquery, or None when the question has no content
    words. OR (not AND) because natural-language questions rarely have
    every word in one chunk; ts_rank sorts the partial matches."""
    terms = build_tsquery_terms(question)
    return " | ".join(terms) if terms else None


@dataclass(frozen=True)
class Candidate:
    chunk_id: int
    file_path: str
    heading: str
    text: str
    similarity: float | None  # 1 - cosine distance; None when the chunk has no embedding


def fuse(
    vector_hits: list[Candidate],
    keyword_hits: list[Candidate],
    k: int,
) -> list[tuple[Candidate, float]]:
    """Merge two ranked lists with Reciprocal Rank Fusion and return the
    top k as (candidate, score). Score is the embedding similarity, raised
    to KEYWORD_MATCH_FLOOR for keyword hits, so downstream thresholding
    keeps its existing meaning."""
    rrf: dict[int, float] = {}
    by_id: dict[int, Candidate] = {}
    keyword_ids = {c.chunk_id for c in keyword_hits}

    for hits in (vector_hits, keyword_hits):
        for rank, cand in enumerate(hits):
            rrf[cand.chunk_id] = rrf.get(cand.chunk_id, 0.0) + 1.0 / (RRF_K + rank + 1)
            by_id.setdefault(cand.chunk_id, cand)

    ordered = sorted(rrf, key=lambda cid: rrf[cid], reverse=True)[:k]
    results: list[tuple[Candidate, float]] = []
    for cid in ordered:
        cand = by_id[cid]
        score = cand.similarity if cand.similarity is not None else 0.0
        if cid in keyword_ids:
            score = max(score, KEYWORD_MATCH_FLOOR)
        results.append((cand, score))
    return results
