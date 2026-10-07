from pathlib import Path

from app.export.adhoc import prepare_adhoc_export, safe_filename


def test_safe_filename_exact_cases():
    assert safe_filename("simple") == "simple"
    assert safe_filename("has spaces") == "has-spaces"
    assert safe_filename("../../etc/passwd") == "etc-passwd"
    assert safe_filename("a/b\\c") == "a-b-c"
    assert safe_filename("...") == "export"
    assert safe_filename("") == "export"
    assert safe_filename("   ") == "export"


def test_prepare_adhoc_export_writes_content_and_runs_pandoc(tmp_path: Path):
    captured = {}

    def fake_runner(cmd):
        captured["cmd"] = cmd
        Path(cmd[cmd.index("-o") + 1] if "-o" in cmd else cmd[-1]).write_bytes(b"fake-output")
        return 0

    result = prepare_adhoc_export(
        content="# My Answer\n\nSome findings.",
        filename="what is revenue growth",
        exports_root=tmp_path,
        fmt="pdf",
        runner=fake_runner,
    )

    source_md = tmp_path / "adhoc" / "what-is-revenue-growth.md"
    assert source_md.read_text(encoding="utf-8") == "# My Answer\n\nSome findings."
    assert result == tmp_path / "adhoc" / "what-is-revenue-growth.pdf"
    assert result.exists()


def test_prepare_adhoc_export_sanitizes_dangerous_filename(tmp_path: Path):
    def fake_runner(cmd):
        Path(cmd[cmd.index("-o") + 1] if "-o" in cmd else cmd[-1]).write_bytes(b"fake-output")
        return 0

    result = prepare_adhoc_export(
        content="x",
        filename="../../etc/passwd",
        exports_root=tmp_path,
        fmt="pdf",
        runner=fake_runner,
    )

    # must stay inside exports_root/adhoc, never escape via the filename
    assert result.parent == tmp_path / "adhoc"
    assert result.name == "etc-passwd.pdf"
