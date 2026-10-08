"""What file an edit produces, and writing it to disk.

Text formats (Markdown, plain text, CSV, JSON, YAML...) come back in the
same format, with the model's edited text written as-is. A PDF comes back
as a PDF and a Word file as a Word file; everything else (PPTX, HTML,
images, email...) becomes a DOCX. Those documents were converted to
Markdown on ingest, so the edited Markdown is rebuilt with pandoc (and
weasyprint for PDF) -- the original layout (fonts, headers, images) is
not preserved. The request can name another format ("...as a PDF").
"""

from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from pathlib import Path
from typing import Callable

from app.handlers.registry import is_plain_text

_TEMPLATES = Path(__file__).resolve().parent.parent / "export" / "templates"
_PDF_TEMPLATE = _TEMPLATES / "edited_document.html"
_PDF_STYLESHEET = _TEMPLATES / "edited_document.css"

# extension -> output format, for files that are already text
TEXT_OUTPUT_FORMATS = {
    ".md": "md", ".markdown": "md", ".txt": "txt", ".csv": "csv", ".json": "json", ".jsonl": "jsonl",
    ".ndjson": "ndjson", ".yaml": "yaml", ".yml": "yml", ".toml": "toml", ".log": "log", ".ini": "ini",
    ".cfg": "cfg", ".conf": "conf", ".properties": "properties", ".xml": "xml",
}

# formats whose structure a part-by-part edit would break
STRUCTURED_FORMATS = {"csv", "json", "jsonl", "ndjson", "yaml", "yml", "toml", "xml", "ini", "cfg", "conf", "properties"}

SPREADSHEET_EXTENSIONS = (".xlsx", ".xls")


class EditNotSupportedError(Exception):
    pass


# formats a document-style file can be rebuilt as
DOCUMENT_FORMATS = ("pdf", "docx", "md", "txt")

_REQUESTED_FORMAT = re.compile(
    r"\b(?:as|to|into|in)\s+(?:an?\s+|the\s+)?(?:plain\s+)?(pdf|docx|word|markdown|md|txt|text)\b"
    r"(?:\s+(?:file|document|doc|format))?",
    re.IGNORECASE,
)
_REQUESTED_NAMES = {"pdf": "pdf", "docx": "docx", "word": "docx", "markdown": "md", "md": "md", "txt": "txt", "text": "txt"}


def requested_format(instruction: str) -> str | None:
    """The format named in the request ("...as a PDF", "save it in Word"),
    or None."""
    match = _REQUESTED_FORMAT.search(instruction)
    return _REQUESTED_NAMES[match.group(1).lower()] if match else None


def output_format(filename: str, mime_type: str, requested: str | None = None) -> str:
    """The format of the new file: the one asked for if the file can be
    rebuilt that way, else the file's own format (PDF stays PDF, text stays
    text), else DOCX."""
    suffix = Path(filename).suffix.lower()
    if suffix in SPREADSHEET_EXTENSIONS:
        raise EditNotSupportedError(
            "editing spreadsheets isn't supported yet; text documents, PDFs and Word files work"
        )

    own = TEXT_OUTPUT_FORMATS.get(suffix) if is_plain_text(mime_type, filename) else None
    # data files (JSON, CSV, YAML...) keep their format; turning them into a
    # document would break them
    if own in STRUCTURED_FORMATS:
        return own
    if requested in DOCUMENT_FORMATS:
        return requested
    if own:
        return own
    return "pdf" if suffix == ".pdf" else "docx"


def is_structured(fmt: str) -> bool:
    return fmt in STRUCTURED_FORMATS


def safe_stem(name: str) -> str:
    """File-name-safe version of a stem that keeps non-ASCII letters (a
    Hindi title shouldn't collapse to "export")."""
    # keep letters, digits and combining marks (the vowel signs in Hindi,
    # Tamil... are "marks", which \w would treat as separators)
    kept = "".join(
        ch if (ch.isalnum() or unicodedata.category(ch).startswith("M") or ch in "-_") else "-" for ch in name
    )
    stem = re.sub(r"-{2,}", "-", kept).strip("-_")
    return stem or "document"


def validate_output(text: str, fmt: str) -> str | None:
    """A warning for output that is supposed to be a strict format but isn't,
    else None. The file is still written -- the user may want to fix it."""
    if fmt == "json":
        try:
            json.loads(text)
        except ValueError as exc:
            return f"the result isn't valid JSON ({exc}); check it before using it"
    return None


def _default_runner(cmd: list[str]) -> int:
    return subprocess.run(cmd, capture_output=True).returncode


def output_stem(source_stem: str, version: int = 1) -> str:
    return f"{safe_stem(source_stem)}-edited" + (f"-{version}" if version > 1 else "")


def output_filename(source_stem: str, fmt: str, version: int = 1) -> str:
    return f"{output_stem(source_stem, version)}.{fmt}"


def write_output(
    text: str,
    fmt: str,
    out_dir: Path,
    source_stem: str,
    runner: Callable[[list[str]], int] = _default_runner,
    version: int = 1,
) -> Path:
    """Write the edited text as `<source>-edited.<fmt>` inside out_dir
    (`<source>-edited-2.<fmt>` for the second edit in a row, and so on)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = output_stem(source_stem, version)
    out_path = out_dir / f"{stem}.{fmt}"

    if fmt not in ("docx", "pdf"):
        out_path.write_text(text, encoding="utf-8")
        return out_path

    markdown_path = out_dir / f"{stem}.md"
    html_path = out_dir / f"{stem}.html"
    markdown_path.write_text(text, encoding="utf-8")
    try:
        if fmt == "docx":
            returncode = runner(["pandoc", str(markdown_path), "-f", "gfm", "-o", str(out_path)])
            if returncode != 0:
                raise RuntimeError(f"pandoc exited with code {returncode} building the Word file")
        else:
            # Markdown -> plain HTML (a bare template, so pandoc adds no styling of its own)
            # -> PDF with a neutral stylesheet, not the branded report one
            pandoc_cmd = [
                "pandoc", str(markdown_path), "-f", "gfm", "-t", "html5", "--template", str(_PDF_TEMPLATE),
                "--metadata", f"pagetitle={source_stem}", "-o", str(html_path),
            ]
            if runner(pandoc_cmd) != 0:
                raise RuntimeError("pandoc failed building the PDF")
            weasyprint_cmd = ["weasyprint", "-s", str(_PDF_STYLESHEET), str(html_path), str(out_path)]
            if runner(weasyprint_cmd) != 0:
                raise RuntimeError("weasyprint failed building the PDF")
    finally:
        markdown_path.unlink(missing_ok=True)
        html_path.unlink(missing_ok=True)
    return out_path
