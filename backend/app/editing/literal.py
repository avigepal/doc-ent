"""Plain "replace X with Y" requests, done exactly in code.

"edit this file, instead of Mac Studio I want Mac Mini" is a find-and-replace.
Handing it to the model is both slower and unreliable: it changed the title
and left the two mentions in the body, because it judged that the specs next
to them still belonged to the old name. A requested replacement should be
applied everywhere, every time, and not depend on the model's judgment, so
requests that are clearly just a replacement never reach the model.

Anything that isn't clearly only a replacement (a second instruction, a
different kind of change) is left to the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Replacement:
    old: str
    new: str


_QUOTE = r"[\"'“‘`]?"
_UNQUOTE = r"[\"'”’`]?"
# a name or phrase: no quotes, commas, semicolons or sentence punctuation inside
_VALUE = r"[^\"'“”‘’`,;!?]+?"

# fillers that may surround the replacement without being another instruction
_FILLER = {
    "please", "kindly", "pls", "can", "could", "would", "will", "you", "i", "we", "just", "now", "also",
    "edit", "update", "change", "modify", "fix", "redo", "rewrite", "make", "it", "this", "that", "the",
    "file", "document", "doc", "pdf", "report", "text", "and", "then", "so", "a", "new", "one", "version",
    "in", "of", "to", "for", "with", "use", "put", "write", "say", "need", "want", "would", "like",
    "d", "ll", "should", "be", "replace", "swap", "all", "every", "everywhere", "throughout", "whole",
    "entire", "mention", "mentions", "occurrence", "occurrences", "instance", "instances", "reference",
    "references", "name", "term", "word", "words",
}

_TRAILING = re.compile(
    r"\s*\b(?:everywhere|throughout(?:\s+the\s+(?:file|document|doc|pdf|text|report))?|"
    r"in\s+(?:the|this|that)\s+(?:whole\s+|entire\s+)?(?:file|document|doc|pdf|text|report)|"
    r"in\s+all\s+(?:places|mentions|occurrences)|all\s+(?:occurrences|mentions))\s*$",
    re.IGNORECASE,
)

_PATTERNS = [
    # replace / change / swap X with / to / by Y
    re.compile(
        r"^(?P<pre>.*?)\b(?:replace|change|swap|substitute|rename|switch)\s+"
        r"(?:all\s+|every\s+)?(?:(?:the\s+)?(?:occurrences?|mentions?|instances?|references?)\s+of\s+)?"
        rf"{_QUOTE}(?P<old>{_VALUE}){_UNQUOTE}\s+(?:with|to|by|into|for)\s+{_QUOTE}(?P<new>{_VALUE}){_UNQUOTE}$",
        re.IGNORECASE,
    ),
    # ... instead of X, I want / use / put Y
    re.compile(
        r"^(?P<pre>.*?)\binstead\s+of\s+"
        rf"{_QUOTE}(?P<old>{_VALUE}){_UNQUOTE}[,;]?\s+"
        r"(?:(?:i|we)\s+(?:want|need|would\s+like|prefer|'d\s+like)|use|put|write|say|make\s+it|"
        r"it\s+should\s+(?:be|say)|with|replace(?:\s+it)?\s+with)\s+"
        rf"{_QUOTE}(?P<new>{_VALUE}){_UNQUOTE}$",
        re.IGNORECASE,
    ),
    # use / put Y instead of X
    re.compile(
        r"^(?P<pre>.*?)\b(?:use|put|write|say|show|want|need|prefer)\s+"
        rf"{_QUOTE}(?P<new>{_VALUE}){_UNQUOTE}\s+instead\s+of\s+{_QUOTE}(?P<old>{_VALUE}){_UNQUOTE}$",
        re.IGNORECASE,
    ),
    # Y instead of X
    re.compile(
        rf"^(?P<pre>){_QUOTE}(?P<new>{_VALUE}){_UNQUOTE}\s+instead\s+of\s+{_QUOTE}(?P<old>{_VALUE}){_UNQUOTE}$",
        re.IGNORECASE,
    ),
    # X -> Y
    re.compile(
        rf"^(?P<pre>){_QUOTE}(?P<old>{_VALUE}){_UNQUOTE}\s*(?:->|=>|→)\s*{_QUOTE}(?P<new>{_VALUE}){_UNQUOTE}$",
        re.IGNORECASE,
    ),
]

_MAX_VALUE_CHARS = 80

# "replace the table with a bullet list", "change this heading to something shorter":
# a determiner means a description of a change, not a name to swap
_DETERMINER = re.compile(r"^(?:the|a|an|this|that|these|those|my|our|your|some|any|each|every|all)\b", re.IGNORECASE)


_SECOND_INSTRUCTION = re.compile(
    r"\b(?:and\s+(?:then\s+|also\s+)?(?:translate|add|remove|delete|make|convert|fix|rewrite|summari[sz]e|"
    r"shorten|expand|format|change|replace|update|put|use|save|give|output|write|insert|append|please)"
    r"|then\b|also\b|,\s*and\b)",
    re.IGNORECASE,
)


def looks_like_name(value: str) -> bool:
    """A name or code as typed (capital letters or digits), as opposed to a
    plain description like "the tone"."""
    return any(c.isupper() or c.isdigit() for c in value)


def _only_fillers(text: str) -> bool:
    words = re.findall(r"[a-z']+", text.lower())
    return all(w in _FILLER for w in words)


def parse_replacement(instruction: str) -> Replacement | None:
    """The replacement the instruction asks for, or None when it isn't
    clearly nothing but a replacement."""
    text = instruction.strip()
    text = _TRAILING.sub("", text.rstrip(" .!")).strip()

    for pattern in _PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        old = match.group("old").strip(" .")
        new = match.group("new").strip(" .")
        if not old or not new or old.lower() == new.lower():
            continue
        if len(old) > _MAX_VALUE_CHARS or len(new) > _MAX_VALUE_CHARS:
            continue
        if _DETERMINER.match(old) or _DETERMINER.match(new):
            continue
        # "use a table instead of bullet points": the verb belongs to the request, not the new text
        if re.match(r"(?:use|put|write|say|show|want|need|prefer|make)\b", new, re.IGNORECASE):
            continue
        # "...with Mac Mini and also add a summary": the tail is another instruction
        if _SECOND_INSTRUCTION.search(old) or _SECOND_INSTRUCTION.search(new):
            continue
        # anything else in the sentence is another instruction: leave it to the model
        if not _only_fillers(match.group("pre")):
            continue
        return Replacement(old, new)
    return None


def _match_case(matched: str, new: str) -> str:
    """Give the replacement the same capitalization style as the text it
    replaces: "MAC STUDIO" -> "MAC MINI", "Mac Studio" -> "Mac Mini",
    "mac studio" -> "mac mini"."""
    letters = [c for c in matched if c.isalpha()]
    if not letters:
        return new
    if all(c.isupper() for c in letters) and len(letters) > 1:
        return new.upper()
    if all(c.islower() for c in letters):
        return new.lower()
    words = matched.split()
    if words and all(w[:1].isupper() for w in words if w[:1].isalpha()):
        return " ".join(w[:1].upper() + w[1:] for w in new.split())
    return new


def find_spans(text: str, replacement: Replacement) -> list[tuple[int, int, str]]:
    """Every place the old text occurs, as (start, end, new text): ignoring
    case, whole words only, and treating any run of whitespace (including a
    line break from wrapped text) as one space. Files that keep their layout
    apply these in place, run by run."""
    words = [re.escape(w) for w in replacement.old.split()]
    pattern = re.compile(r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)", re.IGNORECASE)
    return [(m.start(), m.end(), _match_case(m.group(0), replacement.new)) for m in pattern.finditer(text)]


def apply_replacement(text: str, replacement: Replacement) -> tuple[str, int]:
    """Replace every occurrence (see find_spans). Returns the new text and
    how many places changed."""
    spans = find_spans(text, replacement)
    pieces: list[str] = []
    position = 0
    for start, end, new in spans:
        pieces.append(text[position:start])
        pieces.append(new)
        position = end
    pieces.append(text[position:])
    return "".join(pieces), len(spans)
