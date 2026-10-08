"""Edit a Word or PDF file in place, so the result looks like the original.

The default edit reads a file's text, changes it and builds a new, clean
document -- which loses the original's design. For two kinds of change the
original can be kept instead:

  * a plain replacement ("instead of Mac Studio I want Mac Mini"), and
  * a change to the wording of existing text (shorten, formal tone, fix
    grammar, translate...), made paragraph by paragraph.

Changes that need new structure (add a section, make a table, remove a page)
can't be written into an existing layout and still go the clean-document way.
`needs_rebuild` tells them apart.

Shared by the Word and PDF editors (docx_layout.py, pdf_layout.py).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

from app.editing.literal import Replacement


class LayoutUnsupported(Exception):
    """This file/change can't be made while keeping the layout; the caller
    falls back to building a clean document. The message says why, in words
    a user can read."""


@dataclass(frozen=True)
class LayoutResult:
    changed: int  # paragraphs / lines that were rewritten
    replaced: Replacement | None = None
    replacements: int = 0


# Instructions that add, remove or rearrange things, not reword them.
_STRUCTURAL = re.compile(
    r"\b(?:add|insert|append|prepend|include|remove|delete|drop|summary|summari[sz]e|section|chapter|"
    r"heading|table|bullet|bulleted|list|convert|transform|restructure|reorgani[sz]e|reorder|merge|split|"
    r"move|combine|new\s+(?:page|paragraph|line)|title\s+page|table\s+of\s+contents)\b",
    re.IGNORECASE,
)


def needs_rebuild(instruction: str) -> bool:
    """True when the instruction changes the structure of the document, so it
    can't be applied to the original layout."""
    return bool(_STRUCTURAL.search(instruction))


# ---------- asking the model to reword paragraphs ----------

PARAGRAPH_SYSTEM_PROMPT = (
    "You edit a document paragraph by paragraph. You get an instruction and a JSON list of "
    'paragraphs, each {"id": number, "text": string}.\n'
    "Apply the instruction to the paragraphs and reply with JSON only: "
    '{"paragraphs": [{"id": number, "text": string}]}\n'
    "Rules:\n"
    "- Include ONLY paragraphs you changed, with the same id and the complete new text of that paragraph.\n"
    "- Do not merge, split, add or remove paragraphs. One id in, at most one text out.\n"
    "- Change only what the instruction asks for. Keep numbers, names and formatting characters as they are.\n"
    "- When the instruction changes a name, term or wording, apply it to EVERY paragraph where it appears.\n"
    "- Do not invent facts. Keep the document's language unless the instruction asks you to translate it."
)

_JSON_BODY = {
    "response_format": {"type": "json_object"},
    "chat_template_kwargs": {"enable_thinking": False},
}
_JSON_BODY_PLAIN = {"chat_template_kwargs": {"enable_thinking": False}}

# A batch of paragraphs per model call: big enough to keep the context of
# neighbouring paragraphs, small enough to answer in one reply.
BATCH_CHARS = 5_000
BATCH_PARAGRAPHS = 40
MAX_PARAGRAPHS = 1_500


class ParagraphLLM(Protocol):
    def chat(self, system: str, user: str, temperature: float = 0.2, **kwargs) -> str: ...


def batches(paragraphs: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    """Group (id, text) pairs, in order, into batches for the model."""
    out: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    size = 0
    for item in paragraphs:
        if current and (size + len(item[1]) > BATCH_CHARS or len(current) >= BATCH_PARAGRAPHS):
            out.append(current)
            current, size = [], 0
        current.append(item)
        size += len(item[1])
    if current:
        out.append(current)
    return out


def _extract_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in the reply")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("the reply is not a JSON object")
    return data


def chat_json(llm: ParagraphLLM, system: str, user: str, temperature: float = 0.2) -> dict:
    """Ask the model for a JSON object (no thinking phase; JSON mode where the
    server supports it) and parse it."""
    try:
        raw = llm.chat(system, user, temperature=temperature, extra_body=_JSON_BODY)
    except Exception:
        # a server that rejects response_format: retry without it
        raw = llm.chat(system, user, temperature=temperature, extra_body=_JSON_BODY_PLAIN)
    try:
        return _extract_json(raw)
    except ValueError as exc:
        raise LayoutUnsupported(f"the model's reply wasn't readable ({exc})") from exc


def reword_batch(llm: ParagraphLLM, instruction: str, batch: list[tuple[int, str]]) -> dict[int, str]:
    """The model's new text for the paragraphs it changed, by id. Ids that
    weren't sent, and texts that aren't strings, are ignored."""
    payload = json.dumps([{"id": i, "text": t} for i, t in batch], ensure_ascii=False)
    user = f"Instruction:\n{instruction}\n\nParagraphs:\n{payload}"
    data = chat_json(llm, PARAGRAPH_SYSTEM_PROMPT, user)

    sent = {i for i, _ in batch}
    changes: dict[int, str] = {}
    for item in data.get("paragraphs") or []:
        if not isinstance(item, dict):
            continue
        ident, text = item.get("id"), item.get("text")
        if isinstance(ident, int) and ident in sent and isinstance(text, str) and text.strip():
            changes[ident] = text
    return changes
