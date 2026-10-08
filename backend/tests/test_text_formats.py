from pathlib import Path

import pytest

from app.conversion.backends import PlainTextBackend, DoclingBackend, resolve_conversion_backend
from app.handlers.registry import is_plain_text, route_mime
from app.ingestion.walker import scan_directory


@pytest.mark.parametrize(
    "mime, name",
    [
        ("application/json", "data.json"),
        ("application/x-ndjson", "events.ndjson"),
        ("application/xml", "feed.xml"),
        ("application/yaml", "config.yaml"),
        ("application/x-yaml", "config.yml"),
        ("application/toml", "pyproject.toml"),
        ("text/plain", "server.log"),
        ("text/x-shellscript", "run.sh"),
        # libmagic could not identify these and fell back to a generic type
        ("application/octet-stream", "app.log"),
        ("application/octet-stream", "values.yaml"),
        ("application/octet-stream", "settings.toml"),
        ("application/octet-stream", "export.json"),
    ],
)
def test_text_formats_are_routed_and_read_as_plain_text(mime, name):
    assert route_mime(mime, filename=name) == "convert_fast"
    assert is_plain_text(mime, name)
    assert isinstance(resolve_conversion_backend(mime, name), PlainTextBackend)


def test_the_extension_never_rescues_a_non_generic_type():
    # an executable renamed to .log, a PDF named .json: the content type wins
    assert route_mime("application/x-dosexec", filename="trojan.log") == "unsupported"
    assert not is_plain_text("application/x-dosexec", "trojan.log")
    assert not is_plain_text("application/pdf", "report.json")


def test_dotenv_files_are_not_treated_as_text_so_secrets_are_not_indexed():
    assert route_mime("application/octet-stream", filename=".env") == "unsupported"


def test_html_still_goes_to_docling_not_plain_text():
    assert not is_plain_text("text/html", "page.html")
    assert isinstance(resolve_conversion_backend("text/html", "page.html"), DoclingBackend)


def test_real_files_on_disk_are_picked_up_as_convertible(tmp_path: Path):
    (tmp_path / "data.json").write_text('{"name": "docent", "items": [1, 2, 3]}')
    (tmp_path / "config.yaml").write_text("server:\n  port: 8080\n  debug: true\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "docent"\nversion = "1.0"\n')
    (tmp_path / "app.log").write_text("2026-10-08 12:00:01 INFO started\n2026-10-08 12:00:02 ERROR boom\n")
    (tmp_path / "setup.exe").write_bytes(bytes([0x4D, 0x5A, 0x90, 0x00]) + bytes(300))

    queues = {Path(f.path).name: f.queue for f in scan_directory(tmp_path)}

    assert queues == {
        "data.json": "convert_fast",
        "config.yaml": "convert_fast",
        "pyproject.toml": "convert_fast",
        "app.log": "convert_fast",
        "setup.exe": "unsupported",
    }


def test_plain_text_backend_returns_the_file_content_unchanged(tmp_path: Path):
    log = tmp_path / "app.log"
    log.write_text("INFO started\nERROR boom\n")

    result = PlainTextBackend().convert(log)

    assert result.markdown == "INFO started\nERROR boom\n"
    assert result.engine == "plaintext"
