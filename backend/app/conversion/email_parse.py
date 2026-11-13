"""Reading an email message properly: decoded headers, every recipient, a body even when the message is
HTML only, and the names of its attachments.

Used by the email conversion backend (the Markdown a document is searched from) and, through the same
`ParsedMessage`, for the rows that answer "who mailed whom" (app/ingestion/email_store.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from email import policy
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Iterable

UNKNOWN_SENDER = "(unknown sender)"
UNKNOWN_RECIPIENT = "(unknown recipient)"
NO_SUBJECT = "(no subject)"
UNKNOWN_DATE = "(unknown date)"

# one pass over the file keeps header parsing the same for .eml and every message of an mbox
POLICY = policy.default


@dataclass(frozen=True)
class Person:
    name: str
    addr: str  # lowercase; the identity (the same person writes under many display names)

    def label(self) -> str:
        return f"{self.name} <{self.addr}>" if self.name and self.addr else (self.addr or self.name)


@dataclass
class ParsedMessage:
    subject: str = ""
    sender: Person | None = None
    to: list[Person] = field(default_factory=list)
    cc: list[Person] = field(default_factory=list)
    sent_at: datetime | None = None
    date_text: str = ""
    message_id: str = ""
    body: str = ""
    attachments: list[str] = field(default_factory=list)

    def as_row(self, index: int) -> dict:
        """What is kept per message for questions about who wrote to whom (JSON-safe)."""
        return {
            "index": index,
            "subject": self.subject,
            "sender": _person_dict(self.sender),
            "to": [_person_dict(p) for p in self.to],
            "cc": [_person_dict(p) for p in self.cc],
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "message_id": self.message_id,
            "attachments": self.attachments,
        }


def _person_dict(person: Person | None) -> dict | None:
    return None if person is None else {"name": person.name, "addr": person.addr}


# ---------- people ----------


def _clean(value: str) -> str:
    return " ".join(str(value).split())


def people_from_strings(values: Iterable[str]) -> list[Person]:
    """People in header-like strings ("Dave <d@x.com>; eve@x.com"). A semicolon separates like a comma."""
    people: list[Person] = []
    for name, addr in getaddresses([v.replace(";", ",") for v in values if v]):
        addr = addr.strip().lower()
        if addr and "@" in addr:
            people.append(Person(_clean(name).strip("\"'"), addr))
    return people


def _people(header) -> list[Person]:
    """The people in an address header, whether it is a parsed header object or a plain string."""
    if header is None:
        return []
    try:
        people = [
            Person(_clean(a.display_name), a.addr_spec.lower())
            for a in header.addresses
            if a.addr_spec and "@" in a.addr_spec
        ]
        if people:
            return people
    except Exception:  # a malformed header: fall back to reading its text
        pass
    return people_from_strings([str(header)])


def dedupe(people: list[Person]) -> list[Person]:
    seen: dict[str, Person] = {}
    for person in people:
        existing = seen.get(person.addr)
        if existing is None or (not existing.name and person.name):
            seen[person.addr] = person
    return list(seen.values())


# ---------- body ----------


class _HtmlText(HTMLParser):
    _BLOCKS = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._skip += 1
        elif tag in self._BLOCKS:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self._skip = max(0, self._skip - 1)
        elif tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _HtmlText()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _part_text(part) -> str:
    try:
        return part.get_content()
    except Exception:  # an unknown charset or a broken part: decode it as best we can
        payload = part.get_payload(decode=True) or b""
        return payload.decode(part.get_content_charset() or "utf-8", errors="replace")


def _body_text(msg) -> str:
    """The plain-text body, or the HTML body turned into text when there is no plain text."""
    try:
        plain = msg.get_body(preferencelist=("plain",))
        if plain is not None:
            text = _part_text(plain).strip()
            if text:
                return text
        html = msg.get_body(preferencelist=("html",))
        if html is not None:
            return html_to_text(_part_text(html))
    except Exception:
        pass
    return ""


# ---------- one message ----------


def _sent_at(msg) -> tuple[datetime | None, str]:
    raw = msg.get("Date")
    if raw is None:
        return None, ""
    text = _clean(str(raw))
    try:
        parsed = getattr(raw, "datetime", None)
        if isinstance(parsed, datetime):
            return parsed, text
    except Exception:
        pass
    try:
        return parsedate_to_datetime(text), text
    except (TypeError, ValueError, IndexError):
        return None, text


def _attachment_names(msg) -> list[str]:
    names: list[str] = []
    try:
        for part in msg.iter_attachments():
            filename = part.get_filename()
            if filename:
                names.append(_clean(filename))
    except Exception:
        pass
    return names


def parse_message(msg: Message) -> ParsedMessage:
    """`msg` must have been parsed with POLICY (email.message_from_binary_file(..., policy=POLICY))."""
    senders = _people(msg.get("From"))
    sent_at, date_text = _sent_at(msg)
    return ParsedMessage(
        subject=_clean(str(msg.get("Subject", ""))),
        sender=senders[0] if senders else None,
        to=dedupe(_people(msg.get("To"))),
        cc=dedupe(_people(msg.get("Cc"))),
        sent_at=sent_at,
        date_text=date_text,
        message_id=_clean(str(msg.get("Message-ID", ""))),
        body=_body_text(msg),
        attachments=_attachment_names(msg),
    )


# ---------- Markdown ----------


def _people_line(people: list[Person], fallback: str) -> str:
    return ", ".join(p.label() for p in people) if people else fallback


def render_message(message: ParsedMessage) -> str:
    lines = [
        f"## {message.subject or NO_SUBJECT}\n",
        f"**From:** {message.sender.label() if message.sender else UNKNOWN_SENDER}  ",
        f"**To:** {_people_line(message.to, UNKNOWN_RECIPIENT)}  ",
    ]
    if message.cc:
        lines.append(f"**Cc:** {_people_line(message.cc, '')}  ")
    lines.append(f"**Date:** {message.date_text or UNKNOWN_DATE}")
    if message.attachments:
        lines.append(f"\n**Attachments:** {', '.join(message.attachments)}")
    return "\n".join(lines) + f"\n\n{message.body.strip()}\n"
