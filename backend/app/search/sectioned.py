"""A detailed answer written section by section.

One model call asked for "everything about X in detail" tends to compress: it spends its effort on the
first points and wraps up early. Here one short call plans the sections, then each section is written
by its own call that sees the same sources and is told which other sections exist (so it does not
repeat them). The sections stream one after another, so the first words still appear quickly.

Only used for a detailed prose answer over a fair amount of material (see should_write_in_sections).
Any problem planning the sections falls back to the ordinary single call.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Iterator, Protocol

from app.search.answer_spec import THOROUGH, AnswerSpec
from app.search.ask import GROUNDED_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

MIN_SECTIONS = 2
MAX_SECTIONS = 6
_MAX_TITLE_CHARS = 80
_MAX_FOCUS_CHARS = 240

OUTLINE_SYSTEM_PROMPT = (
    "You plan a detailed answer to a question about the user's documents. You see the question and the "
    "numbered sources. Reply with JSON only: "
    '{"sections": [{"title": "...", "focus": "..."}]}.\n'
    f"- {MIN_SECTIONS} to {MAX_SECTIONS} sections, in the order a reader needs them. The first section is a short overview "
    "that answers the question directly.\n"
    '- "title": a short heading (2 to 6 words). "focus": one sentence naming what that section covers and which '
    "facts from the sources belong in it.\n"
    "- Sections must not overlap, and together they must cover everything in the sources that is relevant to the question.\n"
    "- Plan only from what the sources contain. Do not add a section for something they do not mention."
)

SECTION_SYSTEM_RULES = (
    "\nYou are writing ONE section of a longer answer. Write only that section: no heading (it is added for you), "
    "no introduction to the whole answer, no closing summary. Do not cover what the other sections are for. "
    "Keep citing source numbers like [1] exactly as above."
)

_OUTLINE_SCHEMA = {
    "type": "object",
    "properties": {
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "focus": {"type": "string"}},
                "required": ["title", "focus"],
            },
        }
    },
    "required": ["sections"],
}
_OUTLINE_BODY = {
    "response_format": {"type": "json_object", "schema": _OUTLINE_SCHEMA},
    "chat_template_kwargs": {"enable_thinking": False},
}
_OUTLINE_BODY_PLAIN = {"chat_template_kwargs": {"enable_thinking": False}}


class LLMClient(Protocol):
    def chat(self, system: str, user: str, temperature: float = 0.2, **kwargs) -> str: ...
    def chat_stream(self, system: str, user: str, temperature: float = 0.2) -> Iterator[str]: ...


@dataclass(frozen=True)
class Section:
    title: str
    focus: str


def should_write_in_sections(spec: AnswerSpec | None, context: str, *, enabled: bool, min_chars: int) -> bool:
    """A detailed prose answer over enough material to be worth planning. A table, a list or a numbered
    list is one piece, and a few paragraphs of sources fit one call."""
    return bool(enabled and spec and spec.detail == THOROUGH and spec.shape == "prose" and len(context) >= min_chars)


def _json_object(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in the outline reply")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("the outline reply is not an object")
    return data


def _clean_title(title: str) -> str:
    return re.sub(r"^[#\s*]+|[#\s*:]+$", "", title.strip())[:_MAX_TITLE_CHARS].strip()


def parse_outline(text: str) -> list[Section]:
    """The sections in a model reply, or [] when it is unusable (the caller then answers in one piece)."""
    try:
        raw = _json_object(text).get("sections")
    except (ValueError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    sections: list[Section] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = _clean_title(str(item.get("title", "")))
        focus = str(item.get("focus", "")).strip()[:_MAX_FOCUS_CHARS]
        if not title or title.lower() in seen:
            continue
        seen.add(title.lower())
        sections.append(Section(title, focus))
    if len(sections) < MIN_SECTIONS:
        return []
    return sections[:MAX_SECTIONS]


def plan_sections(question: str, context: str, llm: LLMClient) -> list[Section]:
    prompt = f"Question: {question}\n\nSources:\n{context}"
    try:
        try:
            raw = llm.chat(OUTLINE_SYSTEM_PROMPT, prompt, temperature=0.0, max_tokens=700, extra_body=_OUTLINE_BODY)
        except Exception:
            # a server that rejects response_format: ask again without it (the reply is parsed defensively)
            raw = llm.chat(OUTLINE_SYSTEM_PROMPT, prompt, temperature=0.0, max_tokens=700, extra_body=_OUTLINE_BODY_PLAIN)
    except Exception:
        logger.warning("planning the answer's sections failed; answering in one piece", exc_info=True)
        return []
    return parse_outline(raw or "")


def _outline_text(sections: list[Section], current: int) -> str:
    lines = []
    for i, section in enumerate(sections):
        marker = "  <- YOU WRITE THIS ONE" if i == current else ""
        lines.append(f"{i + 1}. {section.title}: {section.focus}{marker}")
    return "\n".join(lines)


def section_events(question: str, context: str, spec: AnswerSpec, sections: list[Section], llm: LLMClient) -> Iterator[dict]:
    """Status and token events for the whole answer, one section at a time."""
    system = GROUNDED_SYSTEM_PROMPT + spec.instructions() + SECTION_SYSTEM_RULES
    produced_any = False
    for i, section in enumerate(sections):
        yield {"event": "status", "data": {"text": f"Writing part {i + 1} of {len(sections)}: {section.title}…"}}
        prompt = (
            f"Question: {question}\n\nSources:\n{context}\n\n"
            f"The full answer has these sections:\n{_outline_text(sections, i)}\n\n"
            f'Write only section {i + 1}, "{section.title}".'
        )
        started = False
        for piece in llm.chat_stream(system=system, user=prompt):
            if not started:
                started = True
                lead = "\n\n" if produced_any else ""
                yield {"event": "token", "data": {"text": f"{lead}### {section.title}\n\n"}}
            yield {"event": "token", "data": {"text": piece}}
        produced_any = produced_any or started
