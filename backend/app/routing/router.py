"""Decide what a message needs before doing anything with it.

The assistant can do three things with a message:
  search -- answer from the user's documents (the default)
  edit   -- change an attached file / make a new file from it
  chat   -- small talk that isn't about their documents

Obvious cases are decided by rules, with no model call (free and
predictable). Only the ambiguous ones -- a follow-up like "give me more
information about this", or a message with a file attached that isn't
clearly a question or an edit -- go to the model, which also rewrites a
follow-up into a self-contained search query. Anything that goes wrong
(timeout, malformed reply) falls back to a plain search of the original
message, so routing can never block an answer.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Protocol, Sequence

from app.editing.literal import parse_replacement
from app.editing.styling import looks_like_style

logger = logging.getLogger(__name__)

ACTIONS = ("search", "edit", "chat")

# Shown in the dashboard while the work runs.
ROUTE_LABELS = {
    "search": "Searching your documents…",
    "edit": "Editing your file…",
    "chat": "Thinking…",
    "need_file": "",
    "keyword": "Finding matches…",
    "report": "Reading all your documents…",
}


@dataclass(frozen=True)
class RouteDecision:
    action: str  # "search" | "edit" | "chat" | "need_file" | "keyword" | "report"
    query: str  # for search: a self-contained question; otherwise the message itself
    source: str  # "rules" | "model" | "fallback"


class LLMClient(Protocol):
    def chat(self, system: str, user: str, temperature: float = 0.2, **kwargs) -> str: ...


# ---------- rules ----------

_EDIT_START = re.compile(
    r"^\W*(?:(?:please|kindly|pls)[\s,]+)?"
    r"(?:(?:can|could|would|will) you[\s,]+(?:please[\s,]+)?)?"
    r"(?:i (?:want|need|would like|'d like) you to[\s,]+)?"
    r"(?:rewrite|rephrase|reword|paraphrase|edit|modify|revise|update|change|translate|correct|fix|proofread|"
    r"polish|improve|reformat|restructure|reorgani[sz]e|shorten|condense|expand|simplify|redact|anonymi[sz]e|"
    r"rename|replace|remove|delete|insert|append|prepend|add|convert|transform|clean(?:\s+up)?|"
    r"make\s+(?:it|this|that|them|the)\b|turn\s+(?:it|this|that)\b|"
    r"(?:generate|create|produce|write|prepare)\s+(?:me\s+)?(?:a\s+|an\s+|the\s+)?"
    r"(?:new|edited|updated|modified|revised|corrected|translated|rewritten)\b)",
    re.IGNORECASE,
)

_EDIT_ANYWHERE = re.compile(
    r"\b(?:new|edited|updated|modified|revised|translated)\s+(?:file|version|document|copy)\b|\bsave (?:it )?as\b",
    re.IGNORECASE,
)

_QUESTION_START = re.compile(
    r"^\W*(?:what|how|why|when|where|who|whom|whose|which|is|are|was|were|do|does|did|can|could|should|would|"
    r"will|tell me|explain|describe|show|list|give me|summari[sz]e|find|search)\b",
    re.IGNORECASE,
)


# "this document", "my file", "the attached report": the message points at a
# file the user is expected to have attached.
_MENTIONS_FILE = re.compile(
    r"\b(?:this|that|the|my|attached|uploaded)\s+(?:\w+\s+){0,2}"
    r"(?:file|document|doc|pdf|report|text|resume|cv|letter|spreadsheet|sheet|contract|article|essay|"
    r"presentation|slides?|paper|page|notes?)\b",
    re.IGNORECASE,
)


# "full report on this person", "write a detailed profile of Asha": a request
# for everything the documents say about a subject. The subject has to follow
# ("on/about/of/for"), so "summarize the complete report" -- a question about a
# file that happens to be a report -- is not one.
_REPORT_REQUEST = re.compile(
    r"\b(?:full|complete|detailed|comprehensive|in[- ]depth|thorough)\s+"
    r"(?:report|profile|dossier|biography|write[- ]?up|background(?:\s+check)?)\s+(?:on|about|of|for)\b"
    r"|\b(?:make|write|prepare|generate|create|produce|compile|build)\s+(?:me\s+)?(?:a\s+|an\s+|the\s+)?"
    r"(?:\w+\s+){0,2}(?:report|profile|dossier|biography)\s+(?:on|about|of|for)\b",
    re.IGNORECASE,
)


def looks_like_report(message: str) -> bool:
    return bool(_REPORT_REQUEST.search(message))


def looks_like_edit(message: str) -> bool:
    return bool(_EDIT_START.search(message) or _EDIT_ANYWHERE.search(message))


def looks_like_question(message: str) -> bool:
    return message.rstrip().endswith("?") or bool(_QUESTION_START.search(message))


def asks_for_change(message: str) -> bool:
    """The message itself asks for a change to a file: an edit verb, a
    replacement ("instead of X I want Y") or a styling request. A model that
    says "edit" about anything else ("mad", "ok", a bare word) is guessing."""
    return looks_like_edit(message) or looks_like_style(message) or parse_replacement(message) is not None


# ---------- model router ----------

ROUTER_SYSTEM_PROMPT = (
    "You route one message in a document assistant. The assistant can search the user's documents, "
    "edit an attached file into a new file, or just chat.\n"
    'Reply with JSON only: {"action": "search" | "edit" | "chat", "query": "..."}.\n'
    "- search: the user asks about the content of their documents. This is the default when unsure.\n"
    "- edit: the user wants an attached file changed, or a new file made from it (rewrite, translate, fix, "
    "reformat, add or remove content...). Only when a file is attached.\n"
    "- chat: greetings, thanks, or general talk that has nothing to do with their documents.\n"
    'For search, "query" must be the message rewritten as a complete, self-contained question. Keep what '
    'the user asked for (more detail, an example, a comparison, a summary...) and name the thing that '
    '"this", "it" or "that one" refers to, taken from the earlier conversation. Example: earlier question '
    '"How do I enable Wake-on-LAN?", new message "give me more information about this" -> query '
    '"Give me more detailed information about how to enable Wake-on-LAN". '
    'For edit and chat, repeat the message unchanged.'
)

_ROUTER_SCHEMA = {
    "type": "object",
    "properties": {"action": {"enum": list(ACTIONS)}, "query": {"type": "string"}},
    "required": ["action", "query"],
}

# Constrained JSON where the server supports it, and no thinking phase --
# routing has to cost about a second, not a reasoning session.
_ROUTER_BODY = {
    "response_format": {"type": "json_object", "schema": _ROUTER_SCHEMA},
    "chat_template_kwargs": {"enable_thinking": False},
}
_ROUTER_BODY_PLAIN = {"chat_template_kwargs": {"enable_thinking": False}}

_MAX_QUERY_CHARS = 500
_TURN_CHARS = 300


def _format_context(history: Sequence[tuple[str, str]], attachment_names: Sequence[str]) -> str:
    lines = []
    if attachment_names:
        lines.append("Attached files: " + ", ".join(attachment_names))
    else:
        lines.append("Attached files: none")
    if history:
        lines.append("Earlier in this conversation:")
        for question, answer in history:
            lines.append(f"User: {question[:_TURN_CHARS]}")
            lines.append(f"Assistant: {answer[:_TURN_CHARS]}")
    return "\n".join(lines)


def _extract_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in router reply")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("router reply is not an object")
    return data


def _ask_model(
    message: str,
    history: Sequence[tuple[str, str]],
    attachment_names: Sequence[str],
    llm: LLMClient,
) -> dict:
    user = f"{_format_context(history, attachment_names)}\n\nNew message: {message}"
    try:
        raw = llm.chat(ROUTER_SYSTEM_PROMPT, user, temperature=0.0, max_tokens=200, extra_body=_ROUTER_BODY)
    except Exception:
        # A server that rejects the response_format field: retry without it
        # (the reply is parsed defensively either way).
        raw = llm.chat(ROUTER_SYSTEM_PROMPT, user, temperature=0.0, max_tokens=200, extra_body=_ROUTER_BODY_PLAIN)
    return _extract_json(raw)


def _normalize(data: dict, message: str, has_attachments: bool) -> RouteDecision:
    action = str(data.get("action", "")).strip().lower()
    query = str(data.get("query", "")).strip()
    if action not in ACTIONS:
        return RouteDecision("search", message, "fallback")

    # "summarize this file" is a question about the file, whatever the model
    # thinks: an edit needs an edit verb or an explicit request for a new file.
    if action == "edit" and looks_like_question(message) and not looks_like_edit(message):
        action = "search"
    # ...and "mad" or "ok" is not an instruction to change anything
    if action == "edit" and not asks_for_change(message):
        action = "search"
    if action == "edit" and not has_attachments:
        return RouteDecision("need_file", message, "model")
    # With files attached the user is almost always asking about them.
    if action == "chat" and has_attachments:
        action = "search"

    if action != "search" or not query or len(query) > _MAX_QUERY_CHARS:
        query = message
    return RouteDecision(action, query, "model")


def decide_route(
    message: str,
    *,
    has_attachments: bool,
    history: Sequence[tuple[str, str]],
    llm: LLMClient,
    attachment_names: Sequence[str] = (),
) -> RouteDecision:
    text = message.strip()

    # 1. Rules: no model call.
    if has_attachments and (looks_like_edit(text) or looks_like_style(text)):
        return RouteDecision("edit", text, "rules")
    if not has_attachments and (looks_like_edit(text) or looks_like_style(text)) and _MENTIONS_FILE.search(text):
        return RouteDecision("need_file", text, "rules")
    # a request for everything about a subject: read all the documents, not just the best matches
    if looks_like_report(text):
        return RouteDecision("report", text, "rules")
    # "summarize this file", "what does the document say": a question about the
    # attached file, nothing to resolve or rewrite
    if has_attachments and looks_like_question(text) and _MENTIONS_FILE.search(text):
        return RouteDecision("search", text, "rules")
    ambiguous_with_files = has_attachments and not looks_like_question(text)
    if not history and not ambiguous_with_files:
        return RouteDecision("search", text, "rules")

    # 2. The model decides (follow-ups, unclear messages) -- and rewrites a
    # follow-up into a standalone query. Never allowed to stop an answer.
    try:
        return _normalize(_ask_model(text, history, attachment_names, llm), text, has_attachments)
    except Exception:
        logger.warning("routing model call failed; falling back to search", exc_info=True)
        return RouteDecision("search", text, "fallback")
