from pathlib import Path
from types import SimpleNamespace

from docling.datamodel.base_models import ConversionStatus

from app.conversion.backends import DoclingBackend


class FakeConverter:
    """Stands in for docling's DocumentConverter."""

    def __init__(self, markdown: str, status=ConversionStatus.SUCCESS, pages: int = 3):
        self.markdown = markdown
        self.status = status
        self.pages = pages
        self.calls = 0

    def convert(self, _path: str):
        self.calls += 1
        document = SimpleNamespace(export_to_markdown=lambda: self.markdown, pages=list(range(self.pages)))
        return SimpleNamespace(status=self.status, document=document)


def _backend(primary: FakeConverter, pdfium: FakeConverter) -> DoclingBackend:
    backend = DoclingBackend()
    backend._converter = primary
    backend._pdfium_converter = pdfium
    return backend


def test_a_good_docling_result_is_used_as_is_without_the_fallback():
    primary, pdfium = FakeConverter("# Real text"), FakeConverter("other")
    result = _backend(primary, pdfium).convert(Path("a.pdf"))

    assert result.markdown == "# Real text"
    assert result.engine == "docling"
    assert pdfium.calls == 0


def test_an_empty_docling_result_falls_back_to_the_pdfium_reader():
    # docling-parse "Page N failed to parse": nothing extracted, status partial
    primary = FakeConverter("", status=ConversionStatus.PARTIAL_SUCCESS, pages=0)
    pdfium = FakeConverter("## Recovered text", pages=3)

    result = _backend(primary, pdfium).convert(Path("a.pdf"))

    assert result.markdown == "## Recovered text"
    assert result.engine == "docling-pdfium"
    assert result.engine_metadata["page_count"] == 3


def test_a_partial_result_is_replaced_when_the_fallback_has_more_text():
    primary = FakeConverter("one page only", status=ConversionStatus.PARTIAL_SUCCESS)
    pdfium = FakeConverter("one page only, plus the other pages that failed to parse")

    result = _backend(primary, pdfium).convert(Path("a.pdf"))

    assert result.engine == "docling-pdfium"


def test_the_original_is_kept_when_the_fallback_finds_no_more_text():
    primary = FakeConverter("some text", status=ConversionStatus.PARTIAL_SUCCESS)
    pdfium = FakeConverter("")

    result = _backend(primary, pdfium).convert(Path("a.pdf"))

    assert result.markdown == "some text"
    assert result.engine == "docling"


def test_non_pdf_files_never_use_the_pdf_fallback():
    primary = FakeConverter("", status=ConversionStatus.PARTIAL_SUCCESS)
    pdfium = FakeConverter("text")

    result = _backend(primary, pdfium).convert(Path("report.docx"))

    assert result.markdown == ""
    assert pdfium.calls == 0
