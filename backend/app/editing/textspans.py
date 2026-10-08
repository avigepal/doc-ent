"""Changing text that is stored in pieces, without disturbing the pieces.

A Word paragraph is a list of runs (each with its own bold, font, size...)
and a PDF line is a list of spans. To edit such text and keep how it looks,
the change has to be written back into the pieces it came from: the new
words take the style of the old words they replace.

A change is a span: (start, end, new_text) in the characters of all the
pieces joined together. `apply_spans` rewrites each piece; `diff_spans`
turns "old text -> new text" (a model's rewrite) into the smallest spans, so
words the model left alone keep their own formatting.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

Span = tuple[int, int, str]

_TOKEN = re.compile(r"\s+|\w+|[^\w\s]", re.UNICODE)


def diff_spans(old: str, new: str) -> list[Span]:
    """The word-level differences between two texts, as spans over `old`."""
    old_tokens = _TOKEN.findall(old)
    new_tokens = _TOKEN.findall(new)
    # offsets of each old token
    offsets = [0]
    for token in old_tokens:
        offsets.append(offsets[-1] + len(token))

    spans: list[Span] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, old_tokens, new_tokens, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        spans.append((offsets[i1], offsets[i2], "".join(new_tokens[j1:j2])))
    return spans


def apply_spans(pieces: list[str], spans: list[Span]) -> list[str]:
    """The pieces after making the changes. Replaced text goes into the piece
    that held its first character; an insertion goes into the piece before
    it; text removed from other pieces simply disappears."""
    owner: list[int] = []
    for index, piece in enumerate(pieces):
        owner.extend([index] * len(piece))
    text = "".join(pieces)
    if not owner:
        return list(pieces)

    out: list[list[str]] = [[] for _ in pieces]
    position = 0
    for start, end, new in sorted(spans):
        for i in range(position, start):
            out[owner[i]].append(text[i])
        if end > start:
            target = owner[start]
        else:
            target = owner[start - 1] if start > 0 else owner[0]
        out[target].append(new)
        position = max(position, end)
    for i in range(position, len(text)):
        out[owner[i]].append(text[i])
    return ["".join(parts) for parts in out]
