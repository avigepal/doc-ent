"""Phase 4 indexing: turn a converted Markdown file into embedded chunk
rows. The DB writes live in app/tasks/index.py; this module is the pure
part (what text gets embedded) so it's testable without a database.
"""

from __future__ import annotations

from pathlib import PurePath


def build_embedding_input(
    file_path: str, title: str | None, author: str | None, heading: str, text: str
) -> str:
    """Text sent to the embedding model for one chunk.

    File metadata and the section heading are prepended so the vector
    carries them: a question like "Alice's budget report" can then match
    a chunk whose body never repeats the author or filename. The stored
    chunk text stays the raw section body -- this prefix is only for the
    embedding, not shown to the LLM or the user.
    """
    lines = [f"File: {PurePath(file_path).name}"]
    if title:
        lines.append(f"Title: {title}")
    if author:
        lines.append(f"Author: {author}")
    if heading and heading != "(untitled)":
        lines.append(f"Section: {heading}")
    return "\n".join(lines) + "\n\n" + text
