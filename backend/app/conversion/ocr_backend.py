"""OCR for scanned PDFs that the document reader could not read at all.

convert_fast already retries a PDF with three readers (Docling's default,
pypdfium2, and full-page OCR). When all of them come back empty, the file
has no usable text layer and its pages are images. Those files are handed
here: each page is rendered to an image and the vision-capable model
transcribes it, the same model that already reads standalone images (see
vision_backend.py). The result is Markdown with a `## Page N` heading per
page, so the chunker and indexer treat it like any other document.

One model call per page, so a long scan takes a while. Pages with no text
produce nothing, and a file with no text on any page is treated as empty
(the caller marks it failed).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from app.conversion.backends import ConversionResult
from app.conversion.vision_backend import VISION_TIMEOUT_SECONDS, prepare_image_data_url

# Resolution a page is rendered at before it goes to the model. 150 dpi keeps
# typical print legible while keeping each request small.
OCR_DPI = 150

SYSTEM_PROMPT = (
    "You transcribe scanned document pages for a search index. "
    "Be faithful to the page: never invent text that is not visible."
)

PAGE_PROMPT = (
    "Transcribe all text on this page exactly as written, in reading order. "
    "Keep tables as Markdown tables. Reply with the transcription only. "
    "If the page has no text, reply with nothing."
)


class PdfOcrBackend:
    def __init__(self, llm=None) -> None:
        self._llm = llm

    def _get_llm(self):
        if self._llm is None:
            from app.config import settings
            from app.summarization.llm_client import LlamaClient

            self._llm = LlamaClient(
                base_url=settings.llama_vision_url,
                model=settings.llama_vision_model,
                api_key=settings.llama_vision_api_key or None,
                timeout=VISION_TIMEOUT_SECONDS,
            )
        return self._llm

    def convert(self, path: Path) -> ConversionResult:
        import fitz  # PyMuPDF: renders the pages

        pages: list[str] = []
        with fitz.open(path) as document, tempfile.TemporaryDirectory() as scratch:
            page_count = document.page_count
            for number, page in enumerate(document, start=1):
                image_path = Path(scratch) / f"page-{number}.png"
                page.get_pixmap(dpi=OCR_DPI).save(str(image_path))
                data_url, _ = prepare_image_data_url(image_path)
                text = self._get_llm().chat_with_image(SYSTEM_PROMPT, PAGE_PROMPT, data_url).strip()
                if text:
                    pages.append(f"## Page {number}\n\n{text}")

        return ConversionResult(
            markdown="\n\n".join(pages),
            engine="vision-ocr",
            engine_metadata={"title": path.stem, "page_count": page_count},
        )


pdf_ocr_backend = PdfOcrBackend()
