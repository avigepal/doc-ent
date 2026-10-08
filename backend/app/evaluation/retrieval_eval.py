"""Measure how well search finds the chunk that holds the answer.

Run inside the stack (read-only: it queries the library and calls the embedding
server, and writes nothing):

    docker compose exec api python -m app.evaluation.retrieval_eval

For each question in cases.py it runs the same retrieval the app uses and
reports how often the answer's chunk reaches the model, for several numbers of
chunks (k), plus how high it ranks. Run it before and after a change to
search (the number of chunks, the keyword weighting, a reranker...) to see
whether the change helped.
"""

from __future__ import annotations

import re
import statistics

from sqlalchemy import text as sql

from app.config import settings
from app.db import SessionLocal
from app.evaluation.cases import CASES
from app.search.pgvector_retrieval import retrieve_top_k
from app.tasks.correlate import _embedder, _raw_dir

K_VALUES = (5, 8, 10, 12, 15)


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def first_hit(chunks, pattern: str) -> int | None:
    """Position (1 = best) of the first chunk containing the answer."""
    for position, chunk in enumerate(chunks, start=1):
        if re.search(pattern, _flat(chunk.text), re.IGNORECASE):
            return position
    return None


def main() -> None:
    session = SessionLocal()
    library = [_flat(t) for (t,) in session.execute(sql("select text from chunks"))]

    cases = []
    for question, pattern in CASES:
        if any(re.search(pattern, t, re.IGNORECASE) for t in library):
            cases.append((question, pattern))
        else:
            print(f"skipped (the answer is not in the library): {question}")
    print(f"{len(cases)} questions, {len(library)} chunks in the library\n")

    ranks: dict[int, list[int | None]] = {k: [] for k in K_VALUES}
    for question, pattern in cases:
        [embedding] = _embedder.embed([question])
        for k in K_VALUES:
            chunks = retrieve_top_k(session, embedding, k=k, raw_dir=_raw_dir, folders=[], query_text=question)
            ranks[k].append(first_hit(chunks, pattern))
    session.close()

    n = len(cases)
    for k in K_VALUES:
        found = [r for r in ranks[k] if r]
        marker = "   <- the app's setting" if k == settings.search_top_k else ""
        print(f"send {k:2d} chunks: the answer is included for {len(found):2d}/{n}{marker}")

    k = settings.search_top_k
    at = lambda limit: sum(1 for r in ranks[k] if r and r <= limit)  # noqa: E731
    mrr = statistics.mean((1 / r if r else 0) for r in ranks[k])
    print(f"\nat k={k}: ranked first for {at(1)}/{n}, top 3 for {at(3)}/{n}, average rank score (MRR) {mrr:.2f}")
    for (question, _), rank in zip(cases, ranks[k]):
        if rank is None:
            print(f"  missed: {question}")


if __name__ == "__main__":
    main()
