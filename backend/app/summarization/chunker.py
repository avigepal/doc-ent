"""Phase 3: chunk converted Markdown by section.

Docling's output and the email backend's output both use `## ` as the
section/message boundary (file section headings, or one email per
message) — so a single heading-based splitter covers "section" and
"thread" chunking from the plan. Spreadsheet ("sheet") chunking is a
separate concern (pandas-aggregated tables, not raw Markdown) and isn't
handled here yet.

Oversized sections (bigger than max_chars, e.g. a very long thread
message) are greedily split on paragraph boundaries so no chunk exceeds
max_chars — keeps each chunk within a sane prompt budget for the LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_HEADING_RE = re.compile(r"^## (.+)$", re.MULTILINE)


@dataclass(frozen=True)
class Chunk:
    index: int
    heading: str
    text: str


def _split_oversized(heading: str, text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    paragraphs = text.split("\n\n")
    pieces: list[str] = []
    current = ""
    for para in paragraphs:
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) > max_chars and current:
            pieces.append(current)
            current = para
        else:
            current = candidate
    if current:
        pieces.append(current)

    # a single paragraph longer than max_chars on its own: hard-split it
    final: list[str] = []
    for piece in pieces:
        if len(piece) <= max_chars:
            final.append(piece)
        else:
            for i in range(0, len(piece), max_chars):
                final.append(piece[i : i + max_chars])
    return final


def chunk_markdown(markdown: str, max_chars: int = 4000) -> list[Chunk]:
    if not markdown or not markdown.strip():
        return []

    headings = list(_HEADING_RE.finditer(markdown))

    sections: list[tuple[str, str]] = []
    if not headings:
        sections.append(("(untitled)", markdown.strip()))
    else:
        for i, match in enumerate(headings):
            heading = match.group(1).strip()
            start = match.end()
            end = headings[i + 1].start() if i + 1 < len(headings) else len(markdown)
            body = markdown[start:end].strip()
            sections.append((heading, body))

    chunks: list[Chunk] = []
    for heading, body in sections:
        for piece in _split_oversized(heading, body, max_chars):
            if piece.strip():
                chunks.append(Chunk(index=len(chunks), heading=heading, text=piece))

    return chunks
