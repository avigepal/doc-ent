"""Conversion backends — each wraps one conversion engine behind the same
tiny interface (`convert(path) -> ConversionResult`) so the pipeline and
its tests never depend on a specific engine being installed.

Docling is the primary backend for convert_fast, reached over HTTP on a
docling-serve instance (local or remote -- see docker-compose.docling.yml). Marker (hard PDFs) and
Apache Tika (generic catch-all) are planned fallbacks — not wired in yet;
add them here as additional classes with the same interface and have the
task try them in order on failure.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

from app.handlers.registry import is_plain_text


# The PDF reader leaves "GLYPH<127>" (or the HTML-escaped "GLYPH&lt;127&gt;")
# where a character had no Unicode mapping -- in practice a bullet symbol. It
# is noise in search results and gets copied into edited documents.
_GLYPH_PLACEHOLDER = re.compile(r"GLYPH(?:<|&lt;)\s*\d+\s*(?:>|&gt;)[ \t]?")


def clean_converted_markdown(markdown: str) -> str:
    return _GLYPH_PLACEHOLDER.sub("", markdown)


# ---------- is the extracted text real text? ----------
#
# Some PDFs (fonts stored as Type3 glyph drawings, or with a broken character
# map) make Docling's default PDF reader return nonsense that looks like text:
#   "## nI & L%  !U $&n# :1 "!t#"   and   "2;/9730<5=:+76319644=6@3A@3:?"
# It is stored as a successful conversion, and then nothing can be searched or
# answered from the file. Real prose, code and tables score under 0.1 on the
# measure below; that kind of output scores 1.0 and more.

# markdown syntax and image markers, which are not part of the text
_MARKDOWN_SYNTAX = re.compile(r"^\s*(?:#{1,6}\s+|[-*+]\s+|\d+\.\s+|>\s*)|<!--.*?-->|```|\|[-:| ]+\||\|", re.M)
# characters that are almost never in real prose: symbols, Latin-1 punctuation, control codes, the replacement character
_ODD_CHARS = set("#$%&*+<=>@[\\]^_`{|}~\ufffd") | {chr(c) for c in range(0x80, 0xC0)} | {
    chr(c) for c in range(0, 32) if c not in (9, 10, 13)
}
_VOWELS = set("aeiouyAEIOUY")

GARBLED_THRESHOLD = 0.35


def garble_score(markdown: str) -> float:
    """0 for clean text, 1 and above for text that is mostly nonsense.
    Short texts score 0: there isn't enough to judge."""
    text = html.unescape(_MARKDOWN_SYNTAX.sub(" ", markdown))
    tokens = text.split()
    if len(tokens) < 15:
        return 0.0
    chars = [c for token in tokens for c in token]
    odd_share = sum(1 for c in chars if c in _ODD_CHARS) / len(chars)

    # plain English-looking words with no vowel at all ("Qxzt"); other scripts are left alone
    words = [t.strip(".,;:!?()\"'") for t in tokens]
    words = [w for w in words if len(w) >= 4 and w.isascii() and w.isalpha()]
    vowelless_share = sum(1 for w in words if not (set(w) & _VOWELS)) / len(words) if words else 0.0

    # runs that mix letters, digits and symbols, like "2;/9730<5=:+7"
    mixed_share = sum(1 for t in tokens if len(t) >= 6 and sum(c in _ODD_CHARS or c in ";:/" for c in t) >= 3) / len(tokens)
    return round(max(odd_share * 3, vowelless_share, mixed_share * 2), 3)


def looks_garbled(markdown: str) -> bool:
    return garble_score(markdown) >= GARBLED_THRESHOLD


@dataclass(frozen=True)
class ConversionResult:
    markdown: str
    engine: str
    engine_metadata: dict = field(default_factory=dict)


class ConversionBackend(Protocol):
    def convert(self, path: Path) -> ConversionResult: ...


class DoclingServerError(RuntimeError):
    pass


# docling-serve's synchronous upload endpoint (see docker-compose.docling.yml).
_CONVERT_PATH = "/v1/convert/file"


class DoclingBackend:
    """Converts PDF/Office files by sending them to a docling-serve instance
    (DOCLING_URL) -- the same way the LLM is reached through llama-server. No
    Docling code or model runs in this project's containers, so the server can
    be on this machine or a remote Linux box; only the URL differs."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            from app.config import settings

            self._client = httpx.Client(timeout=settings.docling_timeout)
        return self._client

    def _convert_once(self, path: Path, pdfium: bool = False, force_ocr: bool = False) -> tuple[str, bool, int | None]:
        """One round trip: (markdown, succeeded, page count).
        `pdfium=True` swaps the server's PDF reader from docling-parse to
        pypdfium2, and `force_ocr=True` reads every page as an image instead of
        trusting the PDF's own text -- see convert()."""
        from app.config import settings

        data = {
            "to_formats": ["md", "json"],  # json carries the page list
            # the server embeds pictures as base64 by default; the index wants a short marker
            "image_export_mode": "placeholder",
            "do_ocr": str(settings.docling_ocr or force_ocr).lower(),
            "force_ocr": str(force_ocr).lower(),
            "do_table_structure": str(settings.docling_tables).lower(),
            "table_mode": "accurate" if settings.docling_table_mode.lower() == "accurate" else "fast",
        }
        if pdfium or force_ocr:
            data["pdf_backend"] = "pypdfium2"
        headers = {"X-Api-Key": settings.docling_api_key} if settings.docling_api_key else {}
        url = settings.docling_url.rstrip("/") + _CONVERT_PATH
        with path.open("rb") as handle:
            try:
                response = self._get_client().post(url, data=data, files={"files": (path.name, handle)}, headers=headers)
            except httpx.HTTPError as exc:
                raise DoclingServerError(f"docling server unreachable at {settings.docling_url}: {exc}") from exc
        if response.status_code in (401, 403):
            raise DoclingServerError("docling server rejected the API key (DOCLING_API_KEY)")
        if response.status_code >= 400:
            raise DoclingServerError(f"docling server returned {response.status_code}: {response.text[:300]}")
        body = response.json()
        document = body.get("document") or {}
        pages = (document.get("json_content") or {}).get("pages")
        return document.get("md_content") or "", body.get("status") == "success", len(pages) if pages else None

    def convert(self, path: Path) -> ConversionResult:
        markdown, ok, pages = self._convert_once(path)
        engine = "docling"

        # The server's default PDF reader (docling-parse, a C++ library) fails on
        # some perfectly good PDFs -- "Page N failed to parse" -- and then
        # yields an empty or partial document, which used to be stored as a
        # successful (empty) conversion. When that happens, redo the file with
        # pypdfium2 as the reader (same layout and table models on top) and
        # keep whichever result has more text.
        #
        # The same reader can also "succeed" with nonsense (see garble_score):
        # then the cleaner of the two wins, and if neither is readable the
        # pages are read as images (OCR), which doesn't depend on the PDF's
        # own text at all.
        if path.suffix.lower() == ".pdf":
            failed = not markdown.strip() or not ok
            if failed or looks_garbled(markdown):
                retry_markdown, _, retry_pages = self._convert_once(path, pdfium=True)
                if failed:
                    better = len(retry_markdown.strip()) > len(markdown.strip())
                else:
                    better = garble_score(retry_markdown) < garble_score(markdown)
                if better:
                    markdown, pages, engine = retry_markdown, retry_pages, "docling-pdfium"
            if looks_garbled(markdown):
                ocr_markdown, _, ocr_pages = self._convert_once(path, force_ocr=True)
                if ocr_markdown.strip() and garble_score(ocr_markdown) < garble_score(markdown):
                    markdown, pages, engine = ocr_markdown, ocr_pages, "docling-ocr"
        # "title" defaults to the filename -- Docling doesn't reliably expose
        # an embedded document title across formats via its basic API, so
        # this is a safe, always-available fallback rather than a guess at
        # fragile library internals.
        metadata: dict = {"title": path.stem}
        if pages is not None:
            metadata["page_count"] = pages
        return ConversionResult(markdown=clean_converted_markdown(markdown), engine=engine, engine_metadata=metadata)


class PlainTextBackend:
    """For formats that are already plain text (text/plain, text/csv) and
    need no real conversion — Docling rejects these outright ("Input
    document does not match any allowed format"), confirmed live against
    a real .txt file. Just reads the file as-is."""

    def convert(self, path: Path) -> ConversionResult:
        text = path.read_text(encoding="utf-8", errors="replace")
        return ConversionResult(markdown=text, engine="plaintext", engine_metadata={"title": path.stem})


# Shared singletons + mime routing, reused by both the ingestion pipeline
# (app/tasks/convert.py) and ad-hoc dashboard uploads (app/search/
# adhoc_upload.py) so "how do we convert a file" lives in exactly one place.
docling_backend = DoclingBackend()
plaintext_backend = PlainTextBackend()


def resolve_conversion_backend(mime_type: str, filename: str | None = None) -> ConversionBackend:
    if mime_type.startswith("image/"):
        # lazy: vision_backend imports this module (ConversionResult)
        from app.conversion.vision_backend import vision_backend

        return vision_backend

    # Docling rejects text formats outright, so anything already text (see
    # registry.is_plain_text) is read as-is. `filename` lets a log or YAML file
    # that libmagic could only call "application/octet-stream" still be read.
    return plaintext_backend if is_plain_text(mime_type, filename) else docling_backend
