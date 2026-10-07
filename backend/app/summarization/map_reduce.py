"""Phase 3: map-summarize each chunk, then hierarchically reduce the
resulting summaries into one. Used for both the file-level reduce
(chunks -> file summary) and, once grouping strategy is decided, the
group/final reduce (file summaries -> group -> final) — same function,
different input list.
"""

from __future__ import annotations

from typing import Protocol

from app.summarization.chunker import Chunk

MAP_SYSTEM_PROMPT = (
    "You are summarizing one section of a larger document. Produce a "
    "tight, factual summary of the given text. Do not invent information "
    "not present in the text."
)

REDUCE_SYSTEM_PROMPT = (
    "You are combining several summaries of parts of the same document "
    "into one coherent summary. Preserve all distinct facts; do not "
    "repeat points that appear in multiple inputs."
)


class LLMClient(Protocol):
    def chat(self, system: str, user: str, temperature: float = 0.2) -> str: ...


def map_summarize(chunks: list[Chunk], llm: LLMClient) -> list[str]:
    return [llm.chat(system=MAP_SYSTEM_PROMPT, user=chunk.text) for chunk in chunks]


def reduce_summaries(summaries: list[str], llm: LLMClient, batch_size: int = 20) -> str:
    """Combine a list of summaries into one, batching so no single LLM
    call receives more than batch_size summaries at once. Runs multiple
    rounds until exactly one summary remains. A list of 0 or 1 items
    short-circuits without calling the LLM."""
    if not summaries:
        return ""
    if len(summaries) == 1:
        return summaries[0]

    current = summaries
    while len(current) > 1:
        batches = [current[i : i + batch_size] for i in range(0, len(current), batch_size)]
        current = [llm.chat(system=REDUCE_SYSTEM_PROMPT, user="\n\n".join(batch)) for batch in batches]

    return current[0]
