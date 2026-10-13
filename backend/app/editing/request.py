"""One request, two kinds of change: "shorten the introduction and make the
headings dark blue" is a change of wording AND a change of look.

The wording is handled by the text editors (literal.py, layout.py, engine.py)
and the look by the Word styler (styling.py, docx_style.py). The model splits
the request into the two parts, each in the user's own words; the parts are then
applied one after the other to the same file, so the user gets a single result.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.editing.layout import ParagraphLLM, chat_json
from app.editing.styling import looks_like_style, mentions_style

logger = logging.getLogger(__name__)

SPLIT_SYSTEM_PROMPT = (
    "A user asks for changes to a document. Split the request into two instructions and reply with JSON "
    'only: {"text": "...", "style": "..."}.\n'
    "- text: everything about what the document SAYS: wording, replacing or translating words, shortening, "
    "adding or removing content.\n"
    "- style: everything about how the document LOOKS: fonts, sizes, colours, bold, italic, alignment, "
    "spacing, margins, borders, shading, page orientation.\n"
    "Rules:\n"
    "- Use the user's own words for each part, keeping every detail (names, colours, numbers). "
    "Do not add, drop or reinterpret anything.\n"
    "- A part the request does not contain is an empty string.\n"
    'Example: "shorten the intro and make the headings dark blue" -> '
    '{"text": "shorten the intro", "style": "make the headings dark blue"}'
)


@dataclass(frozen=True)
class RequestSplit:
    text: str  # the wording change ("" when there is none)
    style: str  # the look change ("" when there is none)


def split_request(llm: ParagraphLLM, instruction: str) -> RequestSplit:
    """The wording part and the look part of a request. Only called when the
    request mentions style (see styling.mentions_style); if the model can't be
    read, falls back to the old either-or decision."""
    if not mentions_style(instruction):
        return RequestSplit(instruction, "")
    try:
        data = chat_json(llm, SPLIT_SYSTEM_PROMPT, instruction, temperature=0.0)
        text, style = data.get("text"), data.get("style")
        text = text.strip() if isinstance(text, str) else ""
        style = style.strip() if isinstance(style, str) else ""
    except Exception:
        logger.warning("could not split the edit request; treating it as one kind of change", exc_info=True)
        return RequestSplit("", instruction) if looks_like_style(instruction) else RequestSplit(instruction, "")
    # a "style" the model made up out of a tone or wording request ("a formal style") is no look change
    if style and not mentions_style(style):
        style = ""
    if not text and not style:
        return RequestSplit(instruction, "")
    if not text and not looks_like_style(instruction):
        return RequestSplit(instruction, "")
    return RequestSplit(text, style)
