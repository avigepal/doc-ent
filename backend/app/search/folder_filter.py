"""Builds SQL LIKE patterns scoping retrieval to specific top-level
folders under raw/. Kept separate from pgvector_retrieval.py (which
needs a live DB) so the pattern-building itself is unit-testable.
"""

from __future__ import annotations


def build_folder_like_patterns(raw_dir: str, folders: list[str]) -> list[str]:
    """Two SQL LIKE patterns per folder (forward-slash and backslash
    variants) — stored file paths use whatever separator the OS that ran
    the walker used (forward slashes in Docker/Linux, backslashes on
    Windows local dev), and a single-separator pattern silently matches
    nothing on the other OS.

    The backslash variant's backslashes are doubled because PostgreSQL's
    LIKE treats backslash as its *default escape character* — a single
    literal backslash in the pattern gets silently consumed instead of
    matching a literal backslash in the data. Confirmed live: without
    doubling, folder-scoped lookups matched nothing at all on Windows."""
    base = raw_dir.rstrip("/\\")
    base_escaped_for_like = base.replace("\\", "\\\\")

    patterns = []
    for folder in folders:
        if not folder:
            continue
        patterns.append(f"{base}/{folder}/%")
        patterns.append(f"{base_escaped_for_like}\\\\{folder}\\\\%")
    return patterns
