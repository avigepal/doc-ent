"""Edit a .docx in place: fonts, styles, tables, images, headers, numbering
and page setup all stay, because only the text inside the existing runs is
touched. See layout.py for when this is used instead of building a new file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from docx import Document
from docx.oxml.ns import qn

from app.editing.engine import EditProgress
from app.editing.layout import (
    MAX_PARAGRAPHS,
    LayoutResult,
    LayoutUnsupported,
    ParagraphLLM,
    batches,
    reword_batch,
)
from app.editing.literal import find_spans, looks_like_name, parse_replacement
from app.editing.textspans import apply_spans, diff_spans

_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_P, _R, _T = qn("w:p"), qn("w:r"), qn("w:t")


def _own_runs(paragraph) -> list:
    """The runs that belong to this paragraph itself (not to a paragraph
    nested in one of its text boxes), including runs inside hyperlinks."""
    runs = []
    for run in paragraph.iter(_R):
        owner = next(run.iterancestors(_P), None)
        if owner is paragraph and run.findall(_T):
            runs.append(run)
    return runs


def _run_text(run) -> str:
    return "".join(t.text or "" for t in run.findall(_T))


def _set_run_text(run, text: str) -> None:
    nodes = run.findall(_T)
    nodes[0].text = text
    nodes[0].set(_XML_SPACE, "preserve")
    for extra in nodes[1:]:
        extra.text = ""


def _paragraph_text(paragraph) -> str:
    return "".join(_run_text(r) for r in _own_runs(paragraph))


def _write_spans(paragraph, spans) -> None:
    runs = _own_runs(paragraph)
    old = [_run_text(r) for r in runs]
    new = apply_spans(old, spans)
    for run, before, after in zip(runs, old, new):
        if before != after:
            _set_run_text(run, after)


def _in_fallback(paragraph) -> bool:
    """Text boxes are stored twice (a modern copy and a legacy fallback);
    only one is read when rewriting."""
    return any(a.tag.endswith("}Fallback") for a in paragraph.iterancestors())


def _header_footer_paragraphs(doc) -> list:
    found = []
    for section in doc.sections:
        for part in (
            section.header, section.first_page_header, section.even_page_header,
            section.footer, section.first_page_footer, section.even_page_footer,
        ):
            # touching an inherited header/footer would create one
            if not part.is_linked_to_previous:
                found.extend(part._element.iter(_P))
    return found


def edit_docx(src: Path, dst: Path, instruction: str, llm: ParagraphLLM) -> Iterator[EditProgress | LayoutResult]:
    doc = Document(str(src))
    body = list(doc.element.body.iter(_P))

    replacement = parse_replacement(instruction)
    if replacement is not None:
        count = 0
        # body, tables and text boxes, then headers and footers
        for paragraph in body + _header_footer_paragraphs(doc):
            spans = find_spans(_paragraph_text(paragraph), replacement)
            if spans:
                _write_spans(paragraph, spans)
                count += len(spans)
        if count:
            doc.save(str(dst))
            yield LayoutResult(changed=count, replaced=replacement, replacements=count)
            return
        if looks_like_name(replacement.old):
            raise ValueError(f'"{replacement.old}" doesn\'t appear in the file, so there was nothing to replace')

    paragraphs = [(i, p) for i, p in enumerate(body) if not _in_fallback(p)]
    texts = {i: _paragraph_text(p) for i, p in paragraphs}
    todo = [(i, texts[i]) for i, _ in paragraphs if texts[i].strip()]
    if not todo:
        raise LayoutUnsupported("it has no text to edit")
    if len(todo) > MAX_PARAGRAPHS:
        raise LayoutUnsupported(f"it is too long to edit this way ({len(todo)} paragraphs)")

    by_index = dict(paragraphs)
    changed = 0
    done = 0
    for batch in batches(todo):
        yield EditProgress(f"Rewriting paragraphs {done + 1}–{done + len(batch)} of {len(todo)}")
        for ident, new_text in reword_batch(llm, instruction, batch).items():
            spans = diff_spans(texts[ident], new_text)
            if spans:
                _write_spans(by_index[ident], spans)
                changed += 1
        done += len(batch)

    if not changed:
        raise ValueError("nothing in the file needed to change for that request")
    doc.save(str(dst))
    yield LayoutResult(changed=changed)
