"""Phase 4 — grounded Ask: embed query -> retrieve top-k -> answer strictly
from retrieved sources, citing them, or say 'not found' if nothing clears
the similarity threshold. (Your plan's open decision — "grounded,
cites sources, says not found" — resolved that way.)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.search.retrieval import RetrievedChunk

NOT_FOUND_MESSAGE = "I couldn't find information about this in the corpus."

GROUNDED_SYSTEM_PROMPT = (
    "Answer the question using ONLY the numbered sources provided. Cite "
    "the source number(s) for every claim, e.g. [1]. If the sources do "
    "not contain the answer, say so plainly — never invent information."
)


class LLMClient(Protocol):
    def chat(self, system: str, user: str, temperature: float = 0.2) -> str: ...


@dataclass(frozen=True)
class AskResult:
    answer: str
    sources: list[str]
    grounded: bool


def build_context(chunks: list[RetrievedChunk]) -> tuple[str, list[str]]:
    sources = [c.file_path for c in chunks]
    lines = [
        f"[{i}] (from {c.file_path} — {c.heading})\n{c.text}"
        for i, c in enumerate(chunks, start=1)
    ]
    return "\n\n".join(lines), sources


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
