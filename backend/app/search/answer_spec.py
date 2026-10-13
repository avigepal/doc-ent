"""What the person asked the answer to look like: how much detail, and in what shape.

"Explain X in detail", "give me a table of Y with columns a, b, c", "one line: is it Z?" -- the wording
says what the reply should be, but the plain answer prompt asks for the shortest answer possible and
reads the same handful of passages for every question. This reads the wording with rules (free and
predictable, like the router), then does two things with the result:

  * a longer or shorter instruction is added to the answer prompt, in the shape asked for
  * a detailed answer retrieves more passages, so there is something to be detailed about

A message with no such wording gives the default spec, which changes nothing: same prompt, same k.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

BRIEF, NORMAL, THOROUGH = "brief", "normal", "thorough"
SHAPES = ("prose", "table", "steps", "list")

# A thorough answer reads this many times as many passages as an ordinary one, up to MAX_THOROUGH_K.
THOROUGH_MULTIPLIER = 3
MAX_THOROUGH_K = 40

_THOROUGH = re.compile(
    r"\bin[- ]depth\b|\bin (?:full )?detail\b|\bdetailed\b|\bthorough(?:ly)?\b|\bcomprehensive(?:ly)?\b|"
    r"\belaborate\b|\bexplain (?:it |this |that )?(?:fully|completely)\b|\bas much detail\b|\beverything (?:about|on|in)\b|"
    r"\bfull (?:explanation|breakdown|details?)\b|\bwalk me through\b",
    re.IGNORECASE,
)
_BRIEF = re.compile(
    r"\bbriefly\b|\bin brief\b|\bin short\b|\bshort (?:answer|summary|version)\b|\bone[- ](?:line|liner|sentence|word)\b|"
    r"\bin (?:a|one) (?:line|sentence|word)\b|\btl;?dr\b|\bquick(?:ly)?\b|\bjust (?:tell|give) me\b|\byes or no\b|"
    r"\bin (?:a )?(?:few|couple of) (?:words|lines|sentences)\b",
    re.IGNORECASE,
)
_TABLE = re.compile(r"\btables?\b|\btabular\b|\bin columns\b|\bcolumns?\b|\bside by side\b", re.IGNORECASE)
_COMPARE = re.compile(r"\bcompare\b|\bcomparison\b|\bdifferences? between\b|\bversus\b|\bvs\.?\b", re.IGNORECASE)
_STEPS = re.compile(r"\bstep[- ]by[- ]step\b|\bsteps\b|\bprocedure\b|\bwalk me through\b|\bhow (?:do|can|to) (?:i|we|you)\b", re.IGNORECASE)
_LIST = re.compile(r"\bbullet(?:s| points?)?\b|\blist (?:of|all|every|the)\b|\bas a list\b|\benumerate\b", re.IGNORECASE)

# "with columns name, date and total" / "columns: name, date, total" / "table of X (invoice no, date, vendor)"
_COLUMNS = re.compile(r"\bcolumns?\b\s*(?:are|is|:|-|named|called|for)?\s*:?\s*(?P<names>[^.?!\n]+)", re.IGNORECASE)
_MAX_COLUMNS = 12
_MAX_COLUMN_CHARS = 40


@dataclass(frozen=True)
class AnswerSpec:
    detail: str = NORMAL
    shape: str = "prose"
    columns: tuple[str, ...] = ()

    @property
    def is_default(self) -> bool:
        return self.detail == NORMAL and self.shape == "prose" and not self.columns

    def retrieval_k(self, k: int) -> int:
        """How many passages to read: more for a detailed answer, never fewer than asked for."""
        if self.detail != THOROUGH:
            return k
        return max(k, min(k * THOROUGH_MULTIPLIER, MAX_THOROUGH_K))

    def instructions(self) -> str:
        """Extra rules for the answer prompt. Empty for the default spec."""
        if self.is_default:
            return ""
        lines = ["\nThe user asked for a specific kind of answer. These instructions replace the style rules above about length and format:"]
        if self.detail == THOROUGH:
            lines.append(
                "- Give a detailed answer. Cover every relevant point in the sources, with the specifics they contain "
                "(names, numbers, dates, amounts, steps, conditions) instead of a summary. Group it under short ### headings "
                "when it has several parts, and put the most important points first."
            )
        elif self.detail == BRIEF:
            lines.append("- Keep it very short: one to three sentences, only the direct answer.")
        if self.shape == "table":
            if self.columns:
                names = " | ".join(self.columns)
                lines.append(f"- Answer as a Markdown table with exactly these columns, in this order: {names}.")
            else:
                lines.append("- Answer as a Markdown table, with columns that fit the question.")
            lines.append(
                '- One row per item. Write "not stated" in a cell when the sources do not say; never leave a cell empty or guess. '
                "Cite sources like [1] in the cell or a last Source column. Add at most one sentence before or after the table."
            )
        elif self.shape == "steps":
            lines.append("- Answer as a numbered list of steps in the order they are done, one action per step, with the exact commands, values or settings from the sources.")
        elif self.shape == "list":
            lines.append("- Answer as a bulleted list, one item per bullet, with a short detail after each item.")
        return "\n".join(lines)


def _columns(text: str) -> tuple[str, ...]:
    match = _COLUMNS.search(text)
    if not match:
        return ()
    names = re.split(r",|;|\band\b|&|/|\|", match.group("names"))
    cleaned = []
    for name in names:
        name = re.sub(r"^(?:the|a|an|its|their)\s+", "", name.strip().strip("\"'`()[]"), flags=re.IGNORECASE).strip()
        if not name or len(name) > _MAX_COLUMN_CHARS:
            return ()  # a sentence, not a list of column names: let the model read the request itself
        cleaned.append(name)
    unique = list({name.lower(): name for name in reversed(cleaned)}.values())[::-1]  # first spelling wins
    return tuple(unique[:_MAX_COLUMNS]) if len(unique) >= 2 else ()


def parse_answer_spec(message: str) -> AnswerSpec:
    text = message.strip()
    if not text:
        return AnswerSpec()

    detail = NORMAL
    if _THOROUGH.search(text):
        detail = THOROUGH
    elif _BRIEF.search(text):
        detail = BRIEF

    columns: tuple[str, ...] = ()
    if _TABLE.search(text):
        shape = "table"
        columns = _columns(text)
    elif _COMPARE.search(text):
        shape = "table"
    elif _STEPS.search(text):
        shape = "steps"
    elif _LIST.search(text):
        shape = "list"
    else:
        shape = "prose"

    # a one-line answer is not a table or a list, whatever else the wording mentions
    if detail == BRIEF and shape != "table":
        shape = "prose"
    return AnswerSpec(detail=detail, shape=shape, columns=columns)
