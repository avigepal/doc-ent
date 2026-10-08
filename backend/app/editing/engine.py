"""Edit a document with the model: read the whole text, apply the user's
instruction, return the complete new text.

Not retrieval: an edit needs every part of the document in order, because
every part has to come out in the new file. Short documents go to the model
in one pass (so instructions that need the whole document -- "add a summary
at the top" -- work). Long ones are edited part by part, each part sized to
fit comfortably in a single reply, then joined.

`edit_document` is a generator so the caller can show progress while the
model works: it yields EditProgress updates and finishes with one
EditResult.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator, Protocol

from app.editing.literal import Replacement, apply_replacement, looks_like_name, parse_replacement

# Up to this many characters go to the model in one request (~6k tokens).
SINGLE_PASS_CHARS = 24_000
# Longer documents are cut into parts of about this size.
PART_CHARS = 12_000
# Structured formats (JSON, CSV, YAML...) can't be cut at arbitrary points.
STRUCTURED_MAX_CHARS = 60_000

# Edits don't need a reasoning phase, and it would only delay the first word.
_EDIT_BODY = {"chat_template_kwargs": {"enable_thinking": False}}

EDIT_SYSTEM_PROMPT = (
    "You are a careful document editor. You are given an instruction and a document. "
    "Apply the instruction and return the complete edited document.\n"
    "Rules:\n"
    "- Return ONLY the edited document: no introduction, no explanation, no closing remarks, "
    "and do not wrap it in a code fence.\n"
    "- Change only what the instruction asks for. Keep everything else exactly as it is: wording, "
    "order, numbers, names, headings and Markdown structure (tables, lists, code).\n"
    "- When the instruction changes a name, term or wording, apply it to EVERY occurrence, including "
    "the title, headings, tables and captions, even if nearby details then no longer match. Do not "
    "try to correct those details.\n"
    "- Do not invent facts. If the instruction needs information the document does not contain, "
    "leave that part unchanged.\n"
    "- Keep the document's language unless the instruction asks you to translate it."
)

_PART_NOTE = (
    "\nThis is part {i} of {n} of a longer document. Edit only this part and return only this part, "
    "in the same structure."
)


class EditLLM(Protocol):
    def chat_stream(self, system: str, user: str, temperature: float = 0.2, **kwargs) -> Iterator[str]: ...


@dataclass(frozen=True)
class EditProgress:
    text: str


@dataclass(frozen=True)
class EditResult:
    text: str
    parts: int  # 1 = edited in a single pass
    # set when the request was a plain find-and-replace done in code
    replaced: Replacement | None = None
    replacements: int = 0

    @property
    def single_pass(self) -> bool:
        return self.parts == 1


class DocumentTooLargeError(Exception):
    pass


# ---------- splitting ----------

def split_for_edit(text: str, max_chars: int = PART_CHARS) -> list[str]:
    """Cut text into parts of at most max_chars, at paragraph boundaries,
    keeping every character in order (joining the parts with "\\n\\n"
    gives back the text, modulo blank-line runs). A single paragraph longer
    than max_chars is cut at the last whitespace before the limit."""
    paragraphs = re.split(r"\n{2,}", text.strip("\n"))
    parts: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current:
            parts.append(current)
            current = ""

    for paragraph in paragraphs:
        while len(paragraph) > max_chars:
            cut = paragraph.rfind(" ", 0, max_chars)
            cut = cut if cut > max_chars // 2 else max_chars
            flush()
            parts.append(paragraph[:cut])
            paragraph = paragraph[cut:].lstrip(" ")
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) > max_chars:
            flush()
            current = paragraph
        else:
            current = candidate
    flush()
    return parts


# ---------- output cleanup ----------

_FENCE = re.compile(r"^\s*```[A-Za-z0-9_+-]*\n(.*?)\n```\s*$", re.DOTALL)
_DOC_TAGS = re.compile(r"</?document>", re.IGNORECASE)


def clean_model_output(text: str) -> str:
    """Models like to wrap a whole document in a code fence, or echo the
    <document> tags the prompt used. Strip both; leave inner fences alone."""
    text = _DOC_TAGS.sub("", text).strip("\n")
    match = _FENCE.match(text)
    if match and "```" not in match.group(1):
        text = match.group(1)
    return text.strip("\n")


# ---------- the model call ----------

def _build_user_prompt(instruction: str, part: str) -> str:
    return f"Instruction:\n{instruction}\n\n<document>\n{part}\n</document>"


def _run_model(llm: EditLLM, system: str, user: str, report) -> Iterator[EditProgress]:
    """Streams the reply (so a long document can't hit a whole-response
    timeout), yielding a progress line every ~2000 characters. Returns the
    full text through the generator's return value."""
    pieces: list[str] = []
    written = 0
    next_report = 2000
    for piece in llm.chat_stream(system, user, temperature=0.3, extra_body=_EDIT_BODY):
        pieces.append(piece)
        written += len(piece)
        if written >= next_report:
            next_report += 2000
            yield EditProgress(f"{report} — {written:,} characters written")
    return "".join(pieces)


def edit_document(
    text: str,
    instruction: str,
    llm: EditLLM,
    *,
    structured: bool = False,
) -> Iterator[EditProgress | EditResult]:
    """Apply `instruction` to `text`. `structured` marks formats that can't
    be edited in pieces (JSON, CSV, YAML...)."""
    if not text.strip():
        raise ValueError("the file has no text to edit")

    # "instead of X I want Y": an exact replacement everywhere, no model involved
    replacement = parse_replacement(instruction)
    if replacement is not None:
        new_text, count = apply_replacement(text, replacement)
        if count:
            yield EditResult(new_text, parts=1, replaced=replacement, replacements=count)
            return
        if looks_like_name(replacement.old):
            raise ValueError(f'"{replacement.old}" doesn\'t appear in the file, so there was nothing to replace')
        # otherwise it was a description ("change the tone to formal"), not a name to swap

    if len(text) <= SINGLE_PASS_CHARS:
        raw = yield from _run_model(llm, EDIT_SYSTEM_PROMPT, _build_user_prompt(instruction, text), "Rewriting")
        result = clean_model_output(raw)
        if not result.strip():
            raise ValueError("the model returned an empty document")
        yield EditResult(result, parts=1)
        return

    if structured and len(text) > STRUCTURED_MAX_CHARS:
        raise DocumentTooLargeError(
            f"this file is too large to edit in one go ({len(text):,} characters; "
            f"the limit for this format is {STRUCTURED_MAX_CHARS:,})"
        )
    if structured:
        raw = yield from _run_model(llm, EDIT_SYSTEM_PROMPT, _build_user_prompt(instruction, text), "Rewriting")
        result = clean_model_output(raw)
        if not result.strip():
            raise ValueError("the model returned an empty document")
        yield EditResult(result, parts=1)
        return

    parts = split_for_edit(text)
    edited: list[str] = []
    for i, part in enumerate(parts, start=1):
        label = f"Rewriting part {i} of {len(parts)}"
        yield EditProgress(label)
        system = EDIT_SYSTEM_PROMPT + _PART_NOTE.format(i=i, n=len(parts))
        raw = yield from _run_model(llm, system, _build_user_prompt(instruction, part), label)
        piece = clean_model_output(raw)
        # a part the model returned empty is kept as it was rather than lost
        edited.append(piece if piece.strip() else part)
    yield EditResult("\n\n".join(edited), parts=len(parts))
