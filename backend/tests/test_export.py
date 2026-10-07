from pathlib import Path

import pytest

from app.export.pandoc_export import EXPORT_FORMATS, export_markdown


def test_export_markdown_builds_correct_pandoc_command_for_pdf(tmp_path: Path):
    captured = {}

    def fake_runner(cmd):
        captured["cmd"] = cmd
        Path(cmd[cmd.index("-o") + 1]).write_bytes(b"%PDF-fake")
        return 0

    source = tmp_path / "summary.md"
    source.write_text("# Report\n\nBody")
    out_dir = tmp_path / "exports"

    result = export_markdown(source, out_dir, fmt="pdf", runner=fake_runner)

    assert result.suffix == ".pdf"
    assert result.exists()
    cmd = captured["cmd"]
    assert cmd[0] == "pandoc"
    assert str(source) in cmd
    assert "--pdf-engine" in cmd


def test_export_markdown_defaults_to_pdf(tmp_path: Path):
    def fake_runner(cmd):
        Path(cmd[cmd.index("-o") + 1]).write_bytes(b"%PDF-fake")
        return 0

    source = tmp_path / "summary.md"
    source.write_text("# x")

    result = export_markdown(source, tmp_path / "exports", runner=fake_runner)

    assert result.suffix == ".pdf"


def test_export_markdown_supports_docx_and_json_as_secondary_formats(tmp_path: Path):
    def fake_runner(cmd):
        Path(cmd[cmd.index("-o") + 1]).write_bytes(b"fake-output")
        return 0

    source = tmp_path / "summary.md"
    source.write_text("# x")

    docx_result = export_markdown(source, tmp_path / "exports", fmt="docx", runner=fake_runner)
    json_result = export_markdown(source, tmp_path / "exports", fmt="json", runner=fake_runner)

    assert docx_result.suffix == ".docx"
    assert json_result.suffix == ".json"


def test_export_markdown_rejects_unknown_format(tmp_path: Path):
    source = tmp_path / "summary.md"
    source.write_text("# x")

    with pytest.raises(ValueError, match="unsupported export format"):
        export_markdown(source, tmp_path / "exports", fmt="exe", runner=lambda cmd: 0)


def test_export_markdown_raises_when_pandoc_exits_nonzero(tmp_path: Path):
    source = tmp_path / "summary.md"
    source.write_text("# x")

    with pytest.raises(RuntimeError, match="pandoc"):
        export_markdown(source, tmp_path / "exports", fmt="pdf", runner=lambda cmd: 1)


def test_export_formats_lists_pdf_first():
    assert EXPORT_FORMATS[0] == "pdf"
