from pathlib import Path

import httpx
import pytest

from app.conversion.backends import DoclingBackend, DoclingServerError


class FakeConverter:
    """One reader on the fake docling server: answers a request with fixed markdown."""

    def __init__(self, markdown: str, status: str = "success", pages: int = 3):
        self.markdown = markdown
        self.status = status
        self.pages = pages
        self.calls = 0

    def reply(self) -> httpx.Response:
        self.calls += 1
        document = {"md_content": self.markdown, "json_content": {"pages": {str(n): {} for n in range(self.pages)}}}
        return httpx.Response(200, json={"status": self.status, "document": document})


class FakeServer:
    """Stands in for docling-serve: tells the readers apart by the request's form
    fields, the way the real server does (default reader, pypdfium2, whole-page OCR)."""

    def __init__(self, primary, pdfium, ocr):
        self.primary, self.pdfium, self.ocr = primary, pdfium, ocr

    def post(self, url, data, files, headers):
        if data.get("force_ocr") == "true":
            return self.ocr.reply()
        if data.get("pdf_backend") == "pypdfium2":
            return self.pdfium.reply()
        return self.primary.reply()


@pytest.fixture(autouse=True)
def _files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("a.pdf", "report.docx"):
        Path(name).write_bytes(b"x")


def _backend(primary: FakeConverter, pdfium: FakeConverter, ocr: FakeConverter | None = None) -> DoclingBackend:
    return DoclingBackend(client=FakeServer(primary, pdfium, ocr or FakeConverter("")))


def test_a_good_docling_result_is_used_as_is_without_the_fallback():
    primary, pdfium = FakeConverter("# Real text"), FakeConverter("other")
    result = _backend(primary, pdfium).convert(Path("a.pdf"))

    assert result.markdown == "# Real text"
    assert result.engine == "docling"
    assert pdfium.calls == 0


def test_an_empty_docling_result_falls_back_to_the_pdfium_reader():
    # docling-parse "Page N failed to parse": nothing extracted, status partial
    primary = FakeConverter("", status="partial_success", pages=0)
    pdfium = FakeConverter("## Recovered text", pages=3)

    result = _backend(primary, pdfium).convert(Path("a.pdf"))

    assert result.markdown == "## Recovered text"
    assert result.engine == "docling-pdfium"
    assert result.engine_metadata["page_count"] == 3


def test_a_partial_result_is_replaced_when_the_fallback_has_more_text():
    primary = FakeConverter("one page only", status="partial_success")
    pdfium = FakeConverter("one page only, plus the other pages that failed to parse")

    result = _backend(primary, pdfium).convert(Path("a.pdf"))

    assert result.engine == "docling-pdfium"


def test_the_original_is_kept_when_the_fallback_finds_no_more_text():
    primary = FakeConverter("some text", status="partial_success")
    pdfium = FakeConverter("")

    result = _backend(primary, pdfium).convert(Path("a.pdf"))

    assert result.markdown == "some text"
    assert result.engine == "docling"


def test_non_pdf_files_never_use_the_pdf_fallback():
    primary = FakeConverter("", status="partial_success")
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
    backend = _backend(primary, pdfium, ocr)

    assert backend.convert(Path("a.pdf")).engine == "docling"


def test_pages_are_read_as_images_when_neither_reader_gives_text():
    primary, pdfium, ocr = FakeConverter(GARBLED), FakeConverter(GARBLED), FakeConverter(CLEAN)
    backend = _backend(primary, pdfium, ocr)

    result = backend.convert(Path("a.pdf"))

    assert result.engine == "docling-ocr" and result.markdown == CLEAN


def test_ocr_is_not_run_when_a_reader_already_gave_clean_text():
    primary, pdfium, ocr = FakeConverter(GARBLED), FakeConverter(CLEAN), FakeConverter("never used")
    backend = _backend(primary, pdfium, ocr)

    backend.convert(Path("a.pdf"))

    assert ocr.calls == 0


def test_clean_text_never_triggers_a_retry():
    primary, pdfium = FakeConverter(CODE), FakeConverter(CLEAN)
    _backend(primary, pdfium).convert(Path("a.pdf"))
    assert pdfium.calls == 0


# ---------- talking to the server ----------


def test_the_request_goes_to_the_configured_server_with_the_api_key(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "docling_url", "http://gpu-box:5001/")
    monkeypatch.setattr(settings, "docling_api_key", "secret")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return FakeConverter("# Real text").reply()

    backend = DoclingBackend(client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = backend.convert(Path("report.docx"))

    assert result.markdown == "# Real text" and result.engine_metadata["page_count"] == 3
    assert str(seen[0].url) == "http://gpu-box:5001/v1/convert/file"
    assert seen[0].headers["x-api-key"] == "secret"


def test_an_unreachable_server_is_a_clear_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    backend = DoclingBackend(client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(DoclingServerError, match="unreachable"):
        backend.convert(Path("report.docx"))


def test_a_rejected_api_key_is_a_clear_error():
    backend = DoclingBackend(
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(401)))
    )
    with pytest.raises(DoclingServerError, match="API key"):
        backend.convert(Path("report.docx"))
