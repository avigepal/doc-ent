"""Related questions offered under an answer: five things the user could ask
next, based on what the answer says and which documents it came from.

One small model call with thinking switched off, made after the answer is
finished (the dashboard asks for it separately, so it never delays the
answer). The reply is parsed defensively -- a model may return a JSON array,
a numbered list or bullets -- and anything unusable is dropped.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Protocol

SUGGEST_COUNT = 5
MIN_QUESTION_CHARS = 8
MAX_QUESTION_CHARS = 140
ANSWER_CHARS = 6000
MAX_SOURCE_NAMES = 8
SUGGEST_MAX_TOKENS = 400

_NO_THINKING = {"chat_template_kwargs": {"enable_thinking": False}}

SUGGEST_SYSTEM_PROMPT = (
    "You suggest follow-up questions for someone reading an answer about their own documents.\n"
    f"Reply with a JSON array of exactly {SUGGEST_COUNT} short questions and nothing else, "
    'for example ["Question one?", "Question two?"].\n'
    "Rules:\n"
    "- Each question must be something the same documents could answer: build on the specific people, "
    "dates, amounts, terms, comparisons or next steps the answer mentions.\n"
    "- Do not repeat the original question, and do not ask what the answer already states.\n"
    "- Give five different angles (for example detail, comparison, cause, timeline, what is missing).\n"
    f"- Each question is under {MAX_QUESTION_CHARS} characters, written as the user would type it.\n"
    "- Write in the same language as the original question."
)


class LLMClient(Protocol):
    def chat(self, system: str, user: str, temperature: float = 0.2, **kwargs) -> str: ...


def build_prompt(question: str, answer: str, sources: list[str]) -> str:
    names = [Path(s.replace("\\", "/")).name for s in sources[:MAX_SOURCE_NAMES]]
    parts = [f"Original question: {question}", "", f"Answer:\n{answer[:ANSWER_CHARS]}"]
    if names:
        parts += ["", "The answer came from: " + ", ".join(names)]
    return "\n".join(parts)


_JSON_ARRAY = re.compile(r"\[.*\]", re.DOTALL)
_LIST_MARKER = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")


def _normalised(text: str) -> str:
    return re.sub(r"[\W_]+", " ", text.lower()).strip()


def parse_suggestions(raw: str, original_question: str = "") -> list[str]:
    """Up to SUGGEST_COUNT distinct, reasonably short questions from the model's reply."""
    items: list[str] = []
    match = _JSON_ARRAY.search(raw)
    if match:
        try:
            data = json.loads(match.group(0))
            items = [x for x in data if isinstance(x, str)] if isinstance(data, list) else []
        except ValueError:
            items = []
    if not items:
        # Not JSON: one question per line, list markers removed. Only lines that end in a
        # question mark count, so a refusal or a sentence of chatter is never offered as a question.
        lines = (_LIST_MARKER.sub("", line).strip().strip("\"'") for line in raw.splitlines())
        items = [line for line in lines if line.endswith("?")]

    seen = {_normalised(original_question)}
    suggestions: list[str] = []
    for item in items:
        question = " ".join(item.strip().strip("\"'").split())
        key = _normalised(question)
        if not (MIN_QUESTION_CHARS <= len(question) <= MAX_QUESTION_CHARS) or key in seen:
            continue
        seen.add(key)
        suggestions.append(question)
        if len(suggestions) == SUGGEST_COUNT:
            break
    return suggestions


def suggest_followups(question: str, answer: str, sources: list[str], llm: LLMClient) -> list[str]:
    reply = llm.chat(
        SUGGEST_SYSTEM_PROMPT,
        build_prompt(question, answer, sources),
        temperature=0.6,
        max_tokens=SUGGEST_MAX_TOKENS,
        extra_body=_NO_THINKING,
    )
    return parse_suggestions(reply, question)
