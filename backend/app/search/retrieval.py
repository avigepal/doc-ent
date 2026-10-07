"""Shared retrieval types for phase 4. The actual pgvector similarity
query (app/search/pgvector_retrieval.py) needs a live Postgres+pgvector
database and isn't unit-testable here — this module holds the plain data
type everything else (ask, correlate) is built and tested against.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievedChunk:
    file_path: str
    heading: str
    text: str
    score: float  # cosine similarity, higher is more relevant
