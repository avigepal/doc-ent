import subprocess
from pathlib import Path

import pytest

from app.conversion import legacy_office
from app.conversion.backends import ConversionResult, DoclingBackend, resolve_conversion_backend
from app.conversion.legacy_office import (
    LegacyOfficeBackend,
    LegacyOfficeError,
    convert_with_libreoffice,
    legacy_target,
)

DOC = "application/msword"
XLS = "application/vnd.ms-excel"
PPT = "application/vnd.ms-powerpoint"


# ---------- which files count as legacy Office ----------


def test_the_three_old_formats_are_recognised_by_their_content_type():
    assert legacy_target(DOC, "report.doc") == "docx"
    assert legacy_target(XLS, "sheet.xls") == "xlsx"
    assert legacy_target(PPT, "deck.ppt") == "pptx"


def test_the_content_type_wins_over_a_missing_or_wrong_extension():
    assert legacy_target(DOC, "report") == "docx"
    assert legacy_target(XLS, "report.dat") == "xlsx"
    assert legacy_target(PPT) == "pptx"


def test_an_ole_file_libmagic_cannot_name_is_told_apart_by_its_extension():
    for generic in ("application/octet-stream", "application/x-ole-storage", "application/x-cfb"):
        assert legacy_target(generic, "OLD.XLS") == "xlsx"
        assert legacy_target(generic, "a.doc") == "docx"
    assert legacy_target("application/octet-stream", "photo.dat") is None
    assert legacy_target("application/octet-stream") is None


def test_modern_office_files_and_text_saved_as_xls_are_not_legacy():
    docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert legacy_target(docx, "new.docx") is None
    assert legacy_target(docx, "mislabelled.doc") is None  # really a .docx: Docling reads it as is
    assert legacy_target("text/html", "export.xls") is None  # an HTML page saved as .xls
    assert legacy_target("text/plain", "data.xls") is None
    assert legacy_target("application/pdf", "a.pdf") is None


# ---------- routing ----------


def test_legacy_files_are_routed_to_the_converter_and_the_rest_are_not():
    backend = resolve_conversion_backend(DOC, "report.doc")
    assert isinstance(backend, LegacyOfficeBackend)
    assert isinstance(resolve_conversion_backend(XLS, "sheet.xls"), LegacyOfficeBackend)
    assert isinstance(resolve_conversion_backend(PPT, "deck.ppt"), LegacyOfficeBackend)

    assert isinstance(resolve_conversion_backend("application/pdf", "a.pdf"), DoclingBackend)
    docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert isinstance(resolve_conversion_backend(docx, "a.docx"), DoclingBackend)


# ---------- the LibreOffice step ----------


class FakeLibreOffice:
    """Stands in for `soffice`: records the command and writes the converted file."""

    def __init__(self, write=True, returncode=0, stderr=""):
        self.write, self.returncode, self.stderr = write, returncode, stderr
        self.commands = []

    def __call__(self, command, **kwargs):
        self.commands.append((command, kwargs))
        out_dir = Path(command[command.index("--outdir") + 1])
        source = Path(command[-1])
        target = command[command.index("--convert-to") + 1]
        if self.write:
            (out_dir / f"{source.stem}.{target}").write_text("converted")
        return subprocess.CompletedProcess(command, self.returncode, stdout="", stderr=self.stderr)


class RecordingInner:
    def __init__(self):
        self.seen = []

    def convert(self, path):
        self.seen.append((path.name, path.read_text()))
        return ConversionResult(markdown="# From Docling", engine="docling", engine_metadata={"page_count": 4})


def test_the_old_file_is_converted_on_a_copy_and_the_modern_file_goes_to_docling(tmp_path, monkeypatch):
    fake = FakeLibreOffice()
    monkeypatch.setattr(legacy_office.subprocess, "run", fake)
    original = tmp_path / "Quarterly Report.doc"
    original.write_bytes(b"legacy bytes")
    inner = RecordingInner()

    result = LegacyOfficeBackend(inner=inner, target="docx").convert(original)

    command, kwargs = fake.commands[0]
    assert command[0] == "soffice" and "--headless" in command
    assert command[command.index("--convert-to") + 1] == "docx"
    assert any(part.startswith("-env:UserInstallation=") for part in command)  # a private profile
    assert kwargs["timeout"] == legacy_office.CONVERT_TIMEOUT_S
    assert inner.seen == [("Quarterly Report.docx", "converted")]  # Docling got the modern file
    assert original.read_bytes() == b"legacy bytes"  # the original is untouched
    assert result.markdown == "# From Docling"
    assert result.engine == "libreoffice+docling"
    assert result.engine_metadata == {"page_count": 4, "title": "Quarterly Report"}


def test_the_target_comes_from_the_extension_when_the_caller_does_not_say(tmp_path, monkeypatch):
    fake = FakeLibreOffice()
    monkeypatch.setattr(legacy_office.subprocess, "run", fake)
    sheet = tmp_path / "numbers.xls"
    sheet.write_bytes(b"x")

    LegacyOfficeBackend(inner=RecordingInner()).convert(sheet)

    command = fake.commands[0][0]
    assert command[command.index("--convert-to") + 1] == "xlsx"


def test_a_file_with_no_known_kind_is_refused(tmp_path):
    odd = tmp_path / "mystery.bin"
    odd.write_bytes(b"x")
    with pytest.raises(LegacyOfficeError, match="not a .doc, .xls or .ppt"):
        LegacyOfficeBackend(inner=RecordingInner()).convert(odd)


def test_libreoffice_missing_is_a_clear_error(tmp_path, monkeypatch):
    def missing(command, **kwargs):
        raise FileNotFoundError("soffice")

    monkeypatch.setattr(legacy_office.subprocess, "run", missing)
    with pytest.raises(LegacyOfficeError, match="not installed"):
        convert_with_libreoffice(tmp_path / "a.doc", "docx", _fresh(tmp_path))


def test_a_conversion_that_hangs_is_cut_off(tmp_path, monkeypatch):
    def hangs(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(legacy_office.subprocess, "run", hangs)
    with pytest.raises(LegacyOfficeError, match="longer than"):
        convert_with_libreoffice(tmp_path / "a.doc", "docx", _fresh(tmp_path))


def test_a_file_libreoffice_cannot_read_reports_its_message(tmp_path, monkeypatch):
    monkeypatch.setattr(
        legacy_office.subprocess, "run", FakeLibreOffice(write=False, returncode=1, stderr="Error: source file could not be loaded")
    )
    with pytest.raises(LegacyOfficeError, match="could not be loaded"):
        convert_with_libreoffice(tmp_path / "a.doc", "docx", _fresh(tmp_path))


def test_success_with_no_output_file_is_still_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(legacy_office.subprocess, "run", FakeLibreOffice(write=False, returncode=0))
    with pytest.raises(LegacyOfficeError, match="could not convert"):
        convert_with_libreoffice(tmp_path / "a.doc", "docx", _fresh(tmp_path))


def _fresh(tmp_path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    return work
