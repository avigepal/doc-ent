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


def test_glyph_placeholders_are_stripped_from_converted_text():
    from app.conversion.backends import clean_converted_markdown

    dirty = "- GLYPH&lt;127&gt; Remains powered on.\n- GLYPH<127> Stays connected.\nNormal GLYPH is a word."
    assert clean_converted_markdown(dirty) == "- Remains powered on.\n- Stays connected.\nNormal GLYPH is a word."


def test_the_docling_backend_returns_cleaned_markdown():
    primary = FakeConverter("- GLYPH&lt;127&gt; first point")
    result = _backend(primary, FakeConverter("")).convert(Path("a.pdf"))
    assert result.markdown == "- first point"


# ---------- text that looks fine to the converter but is nonsense ----------

from app.conversion.backends import garble_score, looks_garbled  # noqa: E402

GARBLED = (
    '## nI & L%  !U $&n# :1 "!t#\n\n- 2;/9730<5=:+76319644=6@3A@3:?\n- :27**@/3=66+3,+@/3-6:=6+1\n'
    "- > 4)'4:3.6@3::43:<863*+66+:3,396-:57=4:73<5(5@/\n\nUC\DPIW[\_WIMP\MSR\WEFOTGWOI'PXWSKQXGWL\WGH\_MWX\GXWGPH\WJSX\\WG'X\ZYNV]^VV\n"
    "## ab$mjcz \"led\"m !fhg\" $ nt\"&lutnI yIw o: \"!t# 68=~-8=63+6=3-+@+:3@73 9<=6:43*&"
)
CLEAN = (
    "## Step 1: Sign Up & Log In\n\n- Go to the signup page\n- Create your free account\n"
    "- You'll instantly get free credits to test OTPs\n\nTip: use these credits to send test OTPs right away before going live. "
    "The API returns a token that must be included in the header of every request."
)
CODE = (
    "## Loops\n\n```\nfor i in range(5):\n    if i == 2:\n        continue\n    print(i)  # Output: 0, 1, 3, 4\n```\n\n"
    "| Operator | Meaning |\n|---|---|\n| ** | Exponentiation |\n| // | Floor Division |\n| != | Not Equal |\n\n"
    "Use the continue statement to skip the rest of the current iteration of the loop."
)


def test_nonsense_text_is_told_apart_from_prose_code_and_tables():
    assert looks_garbled(GARBLED) and garble_score(GARBLED) > 0.5
    assert not looks_garbled(CLEAN)
    assert not looks_garbled(CODE)


def test_a_short_text_is_never_called_garbled():
    assert garble_score("#$%&*") == 0.0 and garble_score("") == 0.0


def test_text_in_other_scripts_is_not_mistaken_for_nonsense():
    assert not looks_garbled("यह एक सामान्य हिंदी वाक्य है जो परीक्षण के लिए लिखा गया है। " * 3 + "Python 3 है।")


def test_garbled_output_is_redone_with_the_pdfium_reader():
    primary, pdfium = FakeConverter(GARBLED), FakeConverter(CLEAN)
    result = _backend(primary, pdfium).convert(Path("a.pdf"))

    assert result.engine == "docling-pdfium" and result.markdown == CLEAN


def test_the_cleaner_result_wins_even_when_it_is_shorter():
    primary, pdfium = FakeConverter(GARBLED * 3), FakeConverter(CLEAN)
    assert _backend(primary, pdfium).convert(Path("a.pdf")).markdown == CLEAN


def test_a_fallback_that_is_no_cleaner_is_not_used():
    primary, pdfium, ocr = FakeConverter(GARBLED), FakeConverter(GARBLED * 2), FakeConverter(GARBLED)
    backend = _backend(primary, pdfium)
    backend._ocr_converter = ocr

    assert backend.convert(Path("a.pdf")).engine == "docling"


def test_pages_are_read_as_images_when_neither_reader_gives_text():
    primary, pdfium, ocr = FakeConverter(GARBLED), FakeConverter(GARBLED), FakeConverter(CLEAN)
    backend = _backend(primary, pdfium)
    backend._ocr_converter = ocr

    result = backend.convert(Path("a.pdf"))

    assert result.engine == "docling-ocr" and result.markdown == CLEAN


def test_ocr_is_not_run_when_a_reader_already_gave_clean_text():
    primary, pdfium, ocr = FakeConverter(GARBLED), FakeConverter(CLEAN), FakeConverter("never used")
    backend = _backend(primary, pdfium)
    backend._ocr_converter = ocr

    backend.convert(Path("a.pdf"))

    assert ocr.calls == 0


def test_clean_text_never_triggers_a_retry():
    primary, pdfium = FakeConverter(CODE), FakeConverter(CLEAN)
    _backend(primary, pdfium).convert(Path("a.pdf"))
    assert pdfium.calls == 0
