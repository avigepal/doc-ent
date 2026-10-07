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

import mailbox
import shutil
import subprocess
import tempfile
from datetime import datetime
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path

from app.conversion.backends import ConversionResult


def _extract_msg_module():
    """Isolated so tests can monkeypatch it without requiring the real
    extract-msg package or a real .msg file."""
    import extract_msg

    return extract_msg


def _body_text(msg: Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        return ""

    payload = msg.get_payload(decode=True)
    if payload is None:
        return msg.get_payload() or ""
    charset = msg.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def _render_message(subject: str, sender: str, to: str, date: str, body: str) -> str:
    return (
        f"## {subject}\n\n"
        f"**From:** {sender}  \n"
        f"**To:** {to}  \n"
        f"**Date:** {date}\n\n"
        f"{body.strip()}\n"
    )


def _convert_eml(path: Path) -> tuple[str, int]:
    import email

    with path.open("rb") as fh:
        msg = email.message_from_binary_file(fh)

    section = _render_message(
        subject=msg.get("Subject", "(no subject)"),
        sender=msg.get("From", "(unknown sender)"),
        to=msg.get("To", "(unknown recipient)"),
        date=msg.get("Date", "(unknown date)"),
        body=_body_text(msg),
    )
    return section, 1


def _convert_mbox(path: Path) -> tuple[str, int]:
    box = mailbox.mbox(str(path))
    try:
        sections = []
        count = 0
        for msg in box:
            sections.append(
                _render_message(
                    subject=msg.get("Subject", "(no subject)"),
                    sender=msg.get("From", "(unknown sender)"),
                    to=msg.get("To", "(unknown recipient)"),
                    date=msg.get("Date", "(unknown date)"),
                    body=_body_text(msg),
                )
            )
            count += 1
        return "\n---\n\n".join(sections), count
    finally:
        box.close()


def _convert_msg(path: Path) -> tuple[str, int]:
    extract_msg = _extract_msg_module()
    msg = extract_msg.Message(str(path))
    try:
        section = _render_message(
            subject=msg.subject or "(no subject)",
            sender=msg.sender or "(unknown sender)",
            to=msg.to or "(unknown recipient)",
            date=str(msg.date) if msg.date else "(unknown date)",
            body=msg.body or "",
        )
        return section, 1
    finally:
        msg.close()


def _convert_pst(path: Path) -> tuple[str, int]:
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
        total = 0
        for mbox_file in sorted(Path(out_dir).rglob("*")):
            if not mbox_file.is_file():
                continue
            text, count = _convert_mbox(mbox_file)
            if text:
                sections.append(text)
            total += count
        return "\n---\n\n".join(sections), total


_CONVERTERS = {
    ".eml": _convert_eml,
    ".mbox": _convert_mbox,
    ".msg": _convert_msg,
    ".pst": _convert_pst,
}


def _extract_eml_metadata(path: Path) -> dict:
    import email

    with path.open("rb") as fh:
        msg = email.message_from_binary_file(fh)

    doc_created_at = None
    date_header = msg.get("Date")
    if date_header:
        try:
            doc_created_at = parsedate_to_datetime(date_header).isoformat()
        except (TypeError, ValueError):
            pass

    return {"title": msg.get("Subject"), "author": msg.get("From"), "doc_created_at": doc_created_at}


def _extract_msg_metadata(path: Path) -> dict:
    extract_msg = _extract_msg_module()
    msg = extract_msg.Message(str(path))
    try:
        doc_created_at = None
        if msg.date:
            if isinstance(msg.date, datetime):
                doc_created_at = msg.date.isoformat()
            else:
                try:
                    doc_created_at = parsedate_to_datetime(str(msg.date)).isoformat()
                except (TypeError, ValueError):
                    doc_created_at = str(msg.date)
        return {"title": msg.subject, "author": msg.sender, "doc_created_at": doc_created_at}
    finally:
        msg.close()


class EmailArchiveBackend:
    def convert(self, path: Path) -> ConversionResult:
        suffix = path.suffix.lower()
        converter = _CONVERTERS.get(suffix)
        if converter is None:
            raise ValueError(f"unsupported email archive extension: {suffix}")

        markdown, message_count = converter(path)
        engine_metadata = {"message_count": message_count, "source_format": suffix.lstrip(".")}

        # title/author/doc_created_at only make sense for a single message —
        # .mbox/.pst can hold many, so there's no one answer for those.
        if suffix == ".eml" and message_count == 1:
            engine_metadata.update(_extract_eml_metadata(path))
        elif suffix == ".msg" and message_count == 1:
            engine_metadata.update(_extract_msg_metadata(path))

        return ConversionResult(markdown=markdown, engine="email_archive", engine_metadata=engine_metadata)
