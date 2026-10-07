import mailbox
from email.message import EmailMessage
from pathlib import Path

import pytest

from app.conversion.email_backend import EmailArchiveBackend


def _make_eml(path: Path, subject: str, sender: str, to: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg["Date"] = "Mon, 1 Jan 2024 10:00:00 +0000"
    msg.set_content(body)
    path.write_bytes(bytes(msg))


def test_convert_eml_extracts_headers_and_body(tmp_path: Path):
    eml_path = tmp_path / "note.eml"
    _make_eml(eml_path, "Hello there", "alice@example.com", "bob@example.com", "This is the body.")

    result = EmailArchiveBackend().convert(eml_path)

    assert result.engine == "email_archive"
    assert "Hello there" in result.markdown
    assert "alice@example.com" in result.markdown
    assert "This is the body." in result.markdown
    assert result.engine_metadata["message_count"] == 1
    assert result.engine_metadata["title"] == "Hello there"
    assert result.engine_metadata["author"] == "alice@example.com"
    assert result.engine_metadata["doc_created_at"] == "2024-01-01T10:00:00+00:00"


def test_convert_mbox_concatenates_messages_with_thread_tags(tmp_path: Path):
    mbox_path = tmp_path / "archive.mbox"
    box = mailbox.mbox(str(mbox_path))
    box.lock()
    try:
        for i in range(3):
            msg = EmailMessage()
            msg["Subject"] = f"Message {i}"
            msg["From"] = f"sender{i}@example.com"
            msg["To"] = "team@example.com"
            msg.set_content(f"Body of message {i}")
            box.add(msg)
        box.flush()
    finally:
        box.unlock()
        box.close()

    result = EmailArchiveBackend().convert(mbox_path)

    assert result.engine_metadata["message_count"] == 3
    # multi-message formats don't get a single title/author/date
    assert "title" not in result.engine_metadata
    for i in range(3):
        assert f"Message {i}" in result.markdown
        assert f"Body of message {i}" in result.markdown
    # order preserved and messages separated
    assert result.markdown.index("Message 0") < result.markdown.index("Message 1") < result.markdown.index("Message 2")


def test_convert_empty_mbox_returns_empty_message_list(tmp_path: Path):
    mbox_path = tmp_path / "empty.mbox"
    mailbox.mbox(str(mbox_path)).close()

    result = EmailArchiveBackend().convert(mbox_path)

    assert result.engine_metadata["message_count"] == 0
    assert result.markdown.strip() == ""


def test_unsupported_extension_raises_value_error(tmp_path: Path):
    bogus = tmp_path / "file.txt"
    bogus.write_text("not an email archive")

    with pytest.raises(ValueError, match="unsupported email archive"):
        EmailArchiveBackend().convert(bogus)


def test_convert_pst_without_readpst_binary_raises_clear_error(tmp_path: Path, monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    pst_path = tmp_path / "archive.pst"
    pst_path.write_bytes(b"fake-pst-bytes")

    with pytest.raises(RuntimeError, match="readpst"):
        EmailArchiveBackend().convert(pst_path)


def test_convert_msg_extracts_subject_sender_and_body(tmp_path: Path, monkeypatch):
    import app.conversion.email_backend as email_backend_module

    class FakeMsgMessage:
        def __init__(self, path):
            self.subject = "Quarterly report"
            self.sender = "carol@example.com"
            self.to = "dave@example.com"
            self.date = "2024-01-01"
            self.body = "See attached figures."

        def close(self):
            pass

    monkeypatch.setattr(email_backend_module, "_extract_msg_module", lambda: type(
        "M", (), {"Message": FakeMsgMessage}
    ))

    msg_path = tmp_path / "note.msg"
    msg_path.write_bytes(b"fake-msg-bytes")

    result = EmailArchiveBackend().convert(msg_path)

    assert "Quarterly report" in result.markdown
    assert "carol@example.com" in result.markdown
    assert "See attached figures." in result.markdown
    assert result.engine_metadata["message_count"] == 1
    assert result.engine_metadata["title"] == "Quarterly report"
    assert result.engine_metadata["author"] == "carol@example.com"
