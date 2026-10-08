"""Phase 4 — grounded Ask: embed query -> retrieve top-k -> answer strictly
from retrieved sources, citing them, or say 'not found' if nothing clears
the similarity threshold. (Your plan's open decision — "grounded,
cites sources, says not found" — resolved that way.)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.search.retrieval import RetrievedChunk

NOT_FOUND_MESSAGE = "I couldn't find anything about this in your documents."

GROUNDED_SYSTEM_PROMPT = (
    "You answer questions about the user's documents, using ONLY "
    "the numbered sources provided below.\n"
    "\n"
    "Accuracy rules:\n"
    "1. Use no outside knowledge, even if you know the answer. If a fact is "
    "not in the sources, it does not exist for you.\n"
    "2. Cite source numbers like [1] or [1][2] at the end of the sentence or "
    "bullet a fact comes from -- not after every clause, and never in headings.\n"
    "3. If the sources only partly answer the question, answer that part and "
    "end with one sentence saying what the documents do not cover. If they do "
    "not answer it at all, say you couldn't find that in the user's documents.\n"
    "4. Never guess, never fill gaps from general knowledge, and never "
    "describe documents or files you were not shown.\n"
    "\n"
    "Style rules (write like a clear, helpful assistant):\n"
    "- Start with the direct answer in one or two sentences. No preamble such "
    "as \"Based on the provided documents\" and no closing summary or \"Note:\" "
    "that repeats what you said.\n"
    "- Use Markdown. Steps or procedures: a numbered list. Several facts: "
    "bullets. Comparing two or more things on several points: a table. "
    "Commands, file contents and code: fenced code blocks. Use short ### "
    "headings only for answers with distinct sections.\n"
    "- Call the material \"your documents\" or name the file. Never say "
    "\"ingested\", \"corpus\", \"the provided sources\" or \"the excerpts\".\n"
    "- Bold only the few terms that matter most. Keep it as short as the "
    "question allows; leave out detail the question did not ask for."
)


class LLMClient(Protocol):
    def chat(self, system: str, user: str, temperature: float = 0.2) -> str: ...


@dataclass(frozen=True)
class AskResult:
    answer: str
    sources: list[str]
    grounded: bool


def build_context(chunks: list[RetrievedChunk]) -> tuple[str, list[str]]:
    """One numbered source per FILE, with that file's retrieved passages
    together. Numbering per chunk made the same PDF show up as [1]..[8] in
    the citations; per file, [n] and the sources list line up and each
    document appears once."""
    grouped: dict[str, list[RetrievedChunk]] = {}
    for c in chunks:
        grouped.setdefault(c.file_path, []).append(c)

    sources = list(grouped)
    blocks = []
    for i, (path, file_chunks) in enumerate(grouped.items(), start=1):
        headings = list(dict.fromkeys(c.heading for c in file_chunks if c.heading))
        label = f" — {' / '.join(headings)}" if headings else ""
        body = "\n\n".join(c.text for c in file_chunks)
        blocks.append(f"[{i}] (from {path}{label})\n{body}")
    return "\n\n".join(blocks), sources


def answer_grounded(
    question: str,
    chunks: list[RetrievedChunk],
    llm: LLMClient,
    similarity_threshold: float = 0.3,
) -> AskResult:
    if not chunks or max(c.score for c in chunks) < similarity_threshold:
        return AskResult(answer=NOT_FOUND_MESSAGE, sources=[], grounded=False)

    context, sources = build_context(chunks)
    prompt = f"Question: {question}\n\nSources:\n{context}"
    answer = llm.chat(system=GROUNDED_SYSTEM_PROMPT, user=prompt)
    return AskResult(answer=answer, sources=sources, grounded=True)
