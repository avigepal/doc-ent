from pathlib import Path

import pytest

from app.editing.engine import (
    SINGLE_PASS_CHARS,
    DocumentTooLargeError,
    EditProgress,
    EditResult,
    clean_model_output,
    edit_document,
    split_for_edit,
)
from app.editing.formats import (
    EditNotSupportedError,
    requested_format,
    is_structured,
    output_format,
    safe_stem,
    validate_output,
    write_output,
)


class FakeEditLLM:
    """Stands in for the model: `transform` maps the document part it was
    shown to its reply, which is streamed back in small pieces."""

    def __init__(self, transform=lambda part: part.upper(), piece_size=40):
        self.transform = transform
        self.piece_size = piece_size
        self.calls = []

    def chat_stream(self, system, user, temperature=0.2, **kwargs):
        self.calls.append({"system": system, "user": user, "temperature": temperature, **kwargs})
        part = user.split("<document>\n", 1)[1].rsplit("\n</document>", 1)[0]
        reply = self.transform(part)
        for i in range(0, len(reply), self.piece_size):
            yield reply[i : i + self.piece_size]


def run(text, instruction, llm, **kwargs):
    updates = list(edit_document(text, instruction, llm, **kwargs))
    result = updates[-1]
    assert isinstance(result, EditResult)
    return result, [u for u in updates if isinstance(u, EditProgress)]


# ---------- splitting ----------

def test_split_keeps_every_paragraph_in_order_within_the_limit():
    paragraphs = [f"Paragraph {i}. " + "word " * 30 for i in range(40)]
    parts = split_for_edit("\n\n".join(paragraphs), max_chars=500)

    assert all(len(p) <= 500 for p in parts)
    assert len(parts) > 3
    assert "\n\n".join(parts) == "\n\n".join(paragraphs)


def test_a_single_huge_paragraph_is_cut_at_whitespace_without_losing_text():
    text = "alpha beta gamma delta " * 200
    parts = split_for_edit(text, max_chars=300)

    assert all(len(p) <= 300 for p in parts)
    assert " ".join(" ".join(parts).split()) == " ".join(text.split())


def test_short_text_is_one_part():
    assert split_for_edit("one\n\ntwo", max_chars=1000) == ["one\n\ntwo"]


# ---------- cleanup ----------

def test_a_whole_document_code_fence_is_removed():
    assert clean_model_output("```markdown\n# Title\n\ntext\n```") == "# Title\n\ntext"


def test_document_tags_the_prompt_used_are_removed():
    assert clean_model_output("<document>\nhello\n</document>") == "hello"


def test_fences_inside_the_document_are_left_alone():
    text = "Intro\n\n```bash\nls\n```\n\nOutro"
    assert clean_model_output(text) == text


# ---------- editing ----------

def test_a_short_document_is_edited_in_one_pass_with_the_instruction_in_the_prompt():
    llm = FakeEditLLM()
    result, _ = run("hello world", "make it shout", llm)

    assert result.text == "HELLO WORLD"
    assert result.single_pass
    assert len(llm.calls) == 1
    assert "make it shout" in llm.calls[0]["user"]
    assert "hello world" in llm.calls[0]["user"]


def test_editing_asks_for_no_thinking_phase():
    llm = FakeEditLLM()
    run("hello", "x", llm)
    assert llm.calls[0]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_model_wrapping_the_document_in_a_fence_is_cleaned():
    llm = FakeEditLLM(transform=lambda part: f"```\n{part}!\n```")
    result, _ = run("hi", "add !", llm)
    assert result.text == "hi!"


def test_a_long_document_is_edited_part_by_part_and_rejoined_in_order():
    paragraphs = [f"Section {i}. " + "text " * 400 for i in range(30)]  # well over the single-pass limit
    text = "\n\n".join(paragraphs)
    assert len(text) > SINGLE_PASS_CHARS
    llm = FakeEditLLM(transform=lambda part: part.replace("text", "TEXT"))

    result, progress = run(text, "uppercase the word text", llm)

    assert result.parts == len(llm.calls) > 1
    assert not result.single_pass
    assert result.text == text.replace("text", "TEXT")
    assert "part 2 of" in llm.calls[1]["system"]
    assert any("part 1 of" in p.text for p in progress)


def test_a_part_the_model_returns_empty_keeps_its_original_text():
    text = "\n\n".join(f"Section {i}. " + "text " * 400 for i in range(30))
    llm = FakeEditLLM(transform=lambda part: "")
    result, _ = run(text, "x", llm)
    assert result.text == "\n\n".join(split_for_edit(text))


def test_progress_is_reported_while_a_long_reply_streams():
    llm = FakeEditLLM(transform=lambda part: part * 1, piece_size=200)
    _, progress = run("word " * 3000, "x", llm)  # ~15k characters back
    assert progress
    assert "characters written" in progress[0].text


def test_an_empty_model_reply_is_an_error():
    llm = FakeEditLLM(transform=lambda part: "   ")
    with pytest.raises(ValueError, match="empty"):
        list(edit_document("hello", "x", llm))


def test_an_empty_file_is_an_error():
    with pytest.raises(ValueError, match="no text"):
        list(edit_document("   \n", "x", FakeEditLLM()))


def test_structured_files_are_never_cut_into_parts():
    rows = "\n".join(f"{i},name{i},value{i}" for i in range(2000))  # between the two limits
    assert SINGLE_PASS_CHARS < len(rows) < 60_000
    llm = FakeEditLLM(transform=lambda part: part)

    result, _ = run(rows, "x", llm, structured=True)

    assert result.single_pass and len(llm.calls) == 1


def test_a_structured_file_over_the_limit_is_refused():
    with pytest.raises(DocumentTooLargeError):
        list(edit_document("a,b\n" * 30_000, "x", FakeEditLLM(), structured=True))


# ---------- formats ----------

@pytest.mark.parametrize(
    "name, mime, expected",
    [
        ("notes.md", "text/markdown", "md"),
        ("readme.txt", "text/plain", "txt"),
        ("data.csv", "text/csv", "csv"),
        ("data.json", "application/json", "json"),
        ("config.yaml", "application/octet-stream", "yaml"),
        ("report.pdf", "application/pdf", "pdf"),
        ("letter.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
        ("page.html", "text/html", "docx"),
        ("scan.png", "image/png", "docx"),
    ],
)
def test_output_format_keeps_the_files_own_format_and_falls_back_to_docx(name, mime, expected):
    assert output_format(name, mime) == expected


def test_spreadsheets_are_not_supported_yet():
    with pytest.raises(EditNotSupportedError):
        output_format("sheet.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def test_only_data_formats_are_structured():
    assert is_structured("json") and is_structured("csv") and is_structured("yaml")
    assert not is_structured("md") and not is_structured("docx") and not is_structured("txt")


def test_safe_stem_keeps_non_ascii_letters():
    assert safe_stem("Quarterly report (final)!") == "Quarterly-report-final"
    assert safe_stem("रिपोर्ट 2026") == "रिपोर्ट-2026"
    assert safe_stem("///") == "document"


def test_invalid_json_output_gets_a_warning_and_valid_json_does_not():
    assert validate_output('{"a": 1}', "json") is None
    assert "isn't valid JSON" in validate_output('{"a": 1,}', "json")
    assert validate_output("anything", "md") is None


def test_text_output_is_written_as_is_with_an_edited_suffix(tmp_path: Path):
    path = write_output("# Title\n", "md", tmp_path / "out", "My Notes")

    assert path.name == "My-Notes-edited.md"
    assert path.read_text(encoding="utf-8") == "# Title\n"


def test_docx_output_is_built_with_pandoc_and_the_temp_markdown_is_removed(tmp_path: Path):
    commands = []

    def fake_pandoc(cmd):
        commands.append(cmd)
        Path(cmd[cmd.index("-o") + 1]).write_bytes(b"PK-docx")
        return 0

    path = write_output("# Title", "docx", tmp_path, "report", runner=fake_pandoc)

    assert path.name == "report-edited.docx" and path.read_bytes() == b"PK-docx"
    assert commands[0][0] == "pandoc" and "gfm" in commands[0]
    assert not (tmp_path / "report-edited.md").exists()


def test_a_failing_pandoc_is_an_error_and_still_cleans_up(tmp_path: Path):
    with pytest.raises(RuntimeError, match="pandoc"):
        write_output("# Title", "docx", tmp_path, "report", runner=lambda cmd: 1)
    assert not (tmp_path / "report-edited.md").exists()


@pytest.mark.parametrize(
    "instruction, expected",
    [
        ("rewrite this formally and give it to me as a PDF", "pdf"),
        ("translate to Hindi, save it in Word", "docx"),
        ("fix the typos and output as a docx file", "docx"),
        ("convert this into markdown", "md"),
        ("make it shorter as plain text", "txt"),
        ("rewrite this in a formal tone", None),
        ("what is the pdf about", None),
    ],
)
def test_the_format_named_in_the_request_is_picked_up(instruction, expected):
    assert requested_format(instruction) == expected


def test_a_requested_format_overrides_the_default_for_documents():
    assert output_format("report.pdf", "application/pdf", "docx") == "docx"
    assert output_format("letter.docx", "application/octet-stream", "pdf") == "pdf"
    assert output_format("notes.md", "text/markdown", "pdf") == "pdf"


def test_data_files_ignore_a_requested_document_format():
    # a JSON file can't become a PDF/Word document without ceasing to be JSON
    assert output_format("data.json", "application/json", "pdf") == "json"
    assert output_format("table.csv", "text/csv", "docx") == "csv"


def test_pdf_output_goes_markdown_to_html_to_pdf_with_the_neutral_stylesheet(tmp_path: Path):
    commands = []

    def fake_runner(cmd):
        commands.append(cmd)
        if cmd[0] == "pandoc":
            Path(cmd[cmd.index("-o") + 1]).write_text("<html></html>")
        else:
            Path(cmd[-1]).write_bytes(b"%PDF-1.7 fake")
        return 0

    path = write_output("# Title Body", "pdf", tmp_path, "My Report", runner=fake_runner)

    assert path.name == "My-Report-edited.pdf" and path.read_bytes().startswith(b"%PDF")
    pandoc, weasyprint = commands
    assert pandoc[0] == "pandoc" and "--template" in pandoc and pandoc[pandoc.index("--template") + 1].endswith("edited_document.html")
    assert weasyprint[0] == "weasyprint" and "-s" in weasyprint and weasyprint[weasyprint.index("-s") + 1].endswith("edited_document.css")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["My-Report-edited.pdf"]  # temp .md and .html removed


def test_a_failing_weasyprint_is_an_error_and_cleans_up(tmp_path: Path):
    def runner(cmd):
        if cmd[0] == "pandoc":
            Path(cmd[cmd.index("-o") + 1]).write_text("<html></html>")
            return 0
        return 1

    with pytest.raises(RuntimeError, match="weasyprint"):
        write_output("# Title", "pdf", tmp_path, "report", runner=runner)
    assert list(tmp_path.iterdir()) == []


def test_the_pdf_template_and_stylesheet_ship_with_the_app():
    from app.editing.formats import _PDF_STYLESHEET, _PDF_TEMPLATE

    assert _PDF_TEMPLATE.is_file() and "$body$" in _PDF_TEMPLATE.read_text(encoding="utf-8")
    assert _PDF_STYLESHEET.is_file()
