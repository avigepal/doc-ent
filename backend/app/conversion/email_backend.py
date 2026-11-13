"""Email archive conversion backend — phase 2's convert_email_archive.

Handles .eml, .mbox, .msg, and .pst, normalizing each into a single
thread-tagged Markdown document (one `##` section per message) that then
flows through the same convert_and_store pipeline as every other format.

.msg needs the `extract-msg` package (pure Python, in requirements.txt).
.pst needs the `readpst` binary on PATH (part of libpff/pst-utils on
Linux) since there's no reliable pure-Python PST parser — readpst
converts it to mbox files first, which are then parsed the normal way.
"""

from __future__ import annotations

import email
import mailbox
import shutil
import subprocess
import tempfile
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

from app.conversion.backends import ConversionResult
from app.conversion.email_parse import (
    POLICY,
    ParsedMessage,
    Person,
    dedupe,
    parse_message,
    people_from_strings,
    render_message,
)


def _extract_msg_module():
    """Isolated so tests can monkeypatch it without requiring the real
    extract-msg package or a real .msg file."""
    import extract_msg

    return extract_msg


def _read_message(fh) -> ParsedMessage:
    return parse_message(email.message_from_binary_file(fh, policy=POLICY))


def _convert_eml(path: Path) -> tuple[str, list[ParsedMessage]]:
    with path.open("rb") as fh:
        message = _read_message(fh)
    return render_message(message), [message]


def _mbox_messages(path: Path) -> list[ParsedMessage]:
    box = mailbox.mbox(str(path), factory=lambda fh: email.message_from_binary_file(fh, policy=POLICY))
    try:
        return [parse_message(msg) for msg in box]
    finally:
        box.close()


def _convert_mbox(path: Path) -> tuple[str, list[ParsedMessage]]:
    messages = _mbox_messages(path)
    return "\n---\n\n".join(render_message(m) for m in messages), messages


def _msg_datetime(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return parsedate_to_datetime(str(value))
    except (TypeError, ValueError, IndexError):
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None


def _convert_msg(path: Path) -> tuple[str, list[ParsedMessage]]:
    extract_msg = _extract_msg_module()
    msg = extract_msg.Message(str(path))
    try:
        sender = people_from_strings([msg.sender or ""])
        sent_at = _msg_datetime(msg.date)
        names = []
        try:
            names = [a.longFilename or a.shortFilename for a in (msg.attachments or [])]
        except Exception:
            pass
        message = ParsedMessage(
            subject=" ".join((msg.subject or "").split()),
            sender=sender[0] if sender else (Person(msg.sender.strip(), "") if msg.sender else None),
            to=dedupe(people_from_strings([msg.to or ""])),
            cc=dedupe(people_from_strings([getattr(msg, "cc", "") or ""])),
            sent_at=sent_at,
            date_text=str(msg.date) if msg.date else "",
            body=msg.body or "",
            attachments=[n for n in names if n],
        )
        return render_message(message), [message]
    finally:
        msg.close()


def _convert_pst(path: Path) -> tuple[str, list[ParsedMessage]]:
    if shutil.which("readpst") is None:
        raise RuntimeError(
            "readpst binary not found on PATH. Install pst-utils/libpff "
            "(e.g. `apt-get install pst-utils`) to convert .pst archives."
        )

    with tempfile.TemporaryDirectory() as out_dir:
        subprocess.run(
            ["readpst", "-o", out_dir, "-M", str(path)],
            check=True,
            capture_output=True,
        )
        sections: list[str] = []
        messages: list[ParsedMessage] = []
        for mbox_file in sorted(Path(out_dir).rglob("*")):
            if not mbox_file.is_file():
                continue
            text, found = _convert_mbox(mbox_file)
            if text:
                sections.append(text)
            messages.extend(found)
        return "\n---\n\n".join(sections), messages


_CONVERTERS = {
    ".eml": _convert_eml,
    ".mbox": _convert_mbox,
    ".msg": _convert_msg,
    ".pst": _convert_pst,
}


class EmailArchiveBackend:
    def convert(self, path: Path) -> ConversionResult:
        suffix = path.suffix.lower()
        converter = _CONVERTERS.get(suffix)
        if converter is None:
            raise ValueError(f"unsupported email archive extension: {suffix}")

        markdown, messages = converter(path)
        engine_metadata: dict = {
            "message_count": len(messages),
            "source_format": suffix.lstrip("."),
            # who wrote to whom, per message: stored as rows by the convert task (see ingestion/email_store.py)
            "messages": [m.as_row(i) for i, m in enumerate(messages)],
        }

        # title/author/doc_created_at only make sense for a single message —
        # .mbox/.pst can hold many, so there's no one answer for those.
        if suffix in (".eml", ".msg") and len(messages) == 1:
            only = messages[0]
            engine_metadata.update(
                {
                    "title": only.subject or None,
                    "author": only.sender.label() if only.sender else None,
                    "doc_created_at": only.sent_at.isoformat() if only.sent_at else None,
                }
            )

        return ConversionResult(markdown=markdown, engine="email_archive", engine_metadata=engine_metadata)
