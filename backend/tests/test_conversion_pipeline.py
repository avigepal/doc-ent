import json
from pathlib import Path

from app.conversion.backends import ConversionResult
from app.conversion.pipeline import convert_and_store


class FakeBackend:
    def __init__(self, markdown: str = "# Hello\n\nWorld", engine: str = "fake"):
        self.markdown = markdown
        self.engine = engine
        self.calls: list[Path] = []

    def convert(self, path: Path) -> ConversionResult:
        self.calls.append(path)
        return ConversionResult(markdown=self.markdown, engine=self.engine, engine_metadata={"pages": 1})


def test_convert_and_store_writes_markdown_and_sidecar_preserving_relative_path(tmp_path: Path):
    raw_root = tmp_path / "raw"
    converted_root = tmp_path / "converted"
    source = raw_root / "sub" / "report.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF-fake")

    backend = FakeBackend(markdown="# Report\n\nBody text")
    result = convert_and_store(
        raw_root=raw_root,
        file_path=source,
        converted_root=converted_root,
        backend=backend,
        extra_metadata={"sha256": "abc123", "mime_type": "application/pdf"},
    )

    expected_md = converted_root / "sub" / "report.md"
    expected_meta = converted_root / "sub" / "report.md.json"

    assert expected_md.exists()
    assert expected_md.read_text(encoding="utf-8") == "# Report\n\nBody text"
    assert expected_meta.exists()

    metadata = json.loads(expected_meta.read_text(encoding="utf-8"))
    assert metadata["sha256"] == "abc123"
    assert metadata["mime_type"] == "application/pdf"
    assert metadata["engine"] == "fake"
    assert metadata["engine_metadata"] == {"pages": 1}
    assert metadata["char_count"] == len("# Report\n\nBody text")
    assert metadata["source_path"] == str(source)

    assert result.markdown_path == expected_md
    assert result.metadata_path == expected_meta
    assert result.char_count == len("# Report\n\nBody text")
    assert result.engine_metadata == {"pages": 1}
    assert backend.calls == [source]


def test_convert_and_store_creates_parent_directories(tmp_path: Path):
    raw_root = tmp_path / "raw"
    converted_root = tmp_path / "converted"
    source = raw_root / "a" / "b" / "c" / "deep.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"x")

    convert_and_store(
        raw_root=raw_root,
        file_path=source,
        converted_root=converted_root,
        backend=FakeBackend(),
        extra_metadata={},
    )

    assert (converted_root / "a" / "b" / "c" / "deep.md").exists()


def test_convert_and_store_overwrites_on_rerun(tmp_path: Path):
    raw_root = tmp_path / "raw"
    converted_root = tmp_path / "converted"
    source = raw_root / "doc.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"x")

    convert_and_store(
        raw_root=raw_root, file_path=source, converted_root=converted_root,
        backend=FakeBackend(markdown="first"), extra_metadata={},
    )
    convert_and_store(
        raw_root=raw_root, file_path=source, converted_root=converted_root,
        backend=FakeBackend(markdown="second"), extra_metadata={},
    )

    assert (converted_root / "doc.md").read_text(encoding="utf-8") == "second"


def test_empty_conversion_output_is_a_failure_and_writes_nothing(tmp_path: Path):
    import pytest

    from app.conversion.pipeline import EmptyConversionError

    raw_root = tmp_path / "raw"
    raw_root.mkdir()
    src = raw_root / "scan.pdf"
    src.write_bytes(b"%PDF-")

    with pytest.raises(EmptyConversionError):
        convert_and_store(
            raw_root=raw_root,
            file_path=src,
            converted_root=tmp_path / "converted",
            backend=FakeBackend(markdown="  \n "),
            extra_metadata={},
        )

    assert not (tmp_path / "converted").exists()
