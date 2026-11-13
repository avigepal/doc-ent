from pathlib import Path

import pytest

from app.conversion.ocr_backend import PAGE_PROMPT, PdfOcrBackend

fitz = pytest.importorskip("fitz", reason="PyMuPDF is only in the Docker image")


class FakeVision:
    """Answers each page in turn; records the prompts and images it was sent."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def chat_with_image(self, system, user, data_url):
        self.calls.append((system, user, data_url))
        return self.answers.pop(0)


def _pdf(path: Path, pages: int) -> Path:
    document = fitz.open()
    for _ in range(pages):
        document.new_page()
    document.save(str(path))
    document.close()
    return path


def test_each_page_is_read_by_the_model_and_kept_under_its_page_heading(tmp_path):
    vision = FakeVision(["Invoice 42", "Total 99"])
    result = PdfOcrBackend(llm=vision).convert(_pdf(tmp_path / "scan.pdf", pages=2))

    assert result.engine == "vision-ocr"
    assert result.engine_metadata == {"title": "scan", "page_count": 2}
    assert result.markdown == "## Page 1\n\nInvoice 42\n\n## Page 2\n\nTotal 99"
    assert len(vision.calls) == 2
    assert all(user == PAGE_PROMPT and data_url.startswith("data:image/jpeg;base64,") for _, user, data_url in vision.calls)


def test_blank_pages_add_nothing_so_an_empty_file_stays_empty(tmp_path):
    vision = FakeVision(["", "   "])
    result = PdfOcrBackend(llm=vision).convert(_pdf(tmp_path / "blank.pdf", pages=2))

    assert result.markdown == ""
