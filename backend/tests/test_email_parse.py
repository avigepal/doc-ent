import email
from email.message import EmailMessage
from pathlib import Path

from app.conversion.email_backend import EmailArchiveBackend
from app.conversion.email_parse import POLICY, html_to_text, parse_message, people_from_strings, render_message


def _parse(raw: bytes):
    return parse_message(email.message_from_bytes(raw, policy=POLICY))


def test_encoded_headers_are_decoded_to_readable_text():
    raw = (
        b"From: =?UTF-8?B?QW5hbmQgUGF0ZWw=?= <Anand.Patel@Example.com>\r\n"
        b"To: =?utf-8?q?Zo=C3=AB_M=C3=BCller?= <zoe@example.com>\r\n"
        b"Subject: =?UTF-8?B?UXVhcnRlcmx5IHJlcG9ydCDigJMgZmluYWw=?=\r\n"
        b"Date: Mon, 1 Jan 2024 10:00:00 +0000\r\n\r\nHello"
    )
    message = _parse(raw)

    assert message.subject == "Quarterly report – final"
    assert message.sender.name == "Anand Patel" and message.sender.addr == "anand.patel@example.com"
    assert message.to[0].name == "Zoë Müller"


def test_every_recipient_is_found_including_cc_and_names_with_commas():
    raw = (
        b'From: Alice <alice@example.com>\r\nTo: "Smith, Bob" <bob@example.com>, carol@example.com\r\n'
        b"Cc: Dave <dave@example.com>, bob@example.com\r\nSubject: s\r\n\r\nbody"
    )
    message = _parse(raw)

    assert [(p.name, p.addr) for p in message.to] == [("Smith, Bob", "bob@example.com"), ("", "carol@example.com")]
    assert [p.addr for p in message.cc] == ["dave@example.com", "bob@example.com"]


def test_a_message_with_only_an_html_body_still_has_text():
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = "a@example.com", "b@example.com", "News"
    msg.set_content("<html><head><style>p{color:red}</style></head><body><p>Hello <b>world</b></p><p>Second line</p></body></html>", subtype="html")

    body = parse_message(email.message_from_bytes(bytes(msg), policy=POLICY)).body

    assert "Hello world" in body and "Second line" in body
    assert "color:red" not in body and "<" not in body


def test_plain_text_wins_over_html_when_both_are_there():
    msg = EmailMessage()
    msg["From"], msg["Subject"] = "a@example.com", "Both"
    msg.set_content("plain version")
    msg.add_alternative("<p>html version</p>", subtype="html")

    assert parse_message(email.message_from_bytes(bytes(msg), policy=POLICY)).body == "plain version"


def test_attachment_names_are_listed_and_shown_in_the_text():
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = "a@example.com", "b@example.com", "Invoice attached"
    msg.set_content("Please see the invoice.")
    msg.add_attachment(b"%PDF-1.4", maintype="application", subtype="pdf", filename="invoice_0412.pdf")
    msg.add_attachment(b"x", maintype="application", subtype="octet-stream", filename="notes.txt")

    message = parse_message(email.message_from_bytes(bytes(msg), policy=POLICY))

    assert message.attachments == ["invoice_0412.pdf", "notes.txt"]
    assert message.body == "Please see the invoice."
    assert "**Attachments:** invoice_0412.pdf, notes.txt" in render_message(message)


def test_a_broken_date_or_missing_headers_do_not_stop_the_message():
    message = _parse(b"Subject: odd\r\nDate: not a date at all\r\n\r\nbody text")

    assert message.sent_at is None and message.date_text == "not a date at all"
    assert message.sender is None and message.to == []
    text = render_message(message)
    assert "(unknown sender)" in text and "(unknown recipient)" in text and "body text" in text


def test_semicolon_separated_people_from_a_msg_file_are_split():
    people = people_from_strings(["Dave <Dave@Example.com>; eve@example.com"])

    assert [(p.name, p.addr) for p in people] == [("Dave", "dave@example.com"), ("", "eve@example.com")]


def test_html_to_text_keeps_line_breaks_and_drops_scripts():
    assert html_to_text("<div>one</div><div>two<br>three</div><script>x=1</script>") == "one\n\ntwo\nthree"


# ---------- through the backend ----------


def _write(path: Path, raw: bytes) -> Path:
    path.write_bytes(raw)
    return path


def test_the_converted_text_has_cc_decoded_names_and_attachments(tmp_path):
    msg = EmailMessage()
    msg["From"] = "=?UTF-8?B?QW5hbmQgUGF0ZWw=?= <anand@example.com>"
    msg["To"] = "bob@example.com"
    msg["Cc"] = "carol@example.com"
    msg["Subject"] = "Plan"
    msg["Date"] = "Mon, 1 Jan 2024 10:00:00 +0000"
    msg.set_content("Details inside.")
    msg.add_attachment(b"x", maintype="application", subtype="pdf", filename="plan.pdf")

    result = EmailArchiveBackend().convert(_write(tmp_path / "plan.eml", bytes(msg)))

    assert "**From:** Anand Patel <anand@example.com>" in result.markdown
    assert "**Cc:** carol@example.com" in result.markdown
    assert "**Attachments:** plan.pdf" in result.markdown
    assert result.engine_metadata["author"] == "Anand Patel <anand@example.com>"
    assert result.engine_metadata["title"] == "Plan"
    assert result.engine_metadata["doc_created_at"] == "2024-01-01T10:00:00+00:00"


def test_every_message_is_listed_with_who_wrote_to_whom(tmp_path):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Cc"], msg["Subject"] = "a@example.com", "b@example.com", "c@example.com", "Hi"
    msg.set_content("x")

    [row] = EmailArchiveBackend().convert(_write(tmp_path / "m.eml", bytes(msg))).engine_metadata["messages"]

    assert row["sender"] == {"name": "", "addr": "a@example.com"}
    assert [p["addr"] for p in row["to"]] == ["b@example.com"] and [p["addr"] for p in row["cc"]] == ["c@example.com"]
    assert row["subject"] == "Hi" and row["index"] == 0


def test_the_per_message_rows_are_not_written_into_the_metadata_file(tmp_path):
    import json

    from app.conversion.pipeline import convert_and_store

    raw_root, converted_root = tmp_path / "raw", tmp_path / "converted"
    raw_root.mkdir()
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = "a@example.com", "b@example.com", "Hi"
    msg.set_content("x")
    path = _write(raw_root / "m.eml", bytes(msg))

    stored = convert_and_store(raw_root, path, converted_root, EmailArchiveBackend(), {})

    assert "messages" in stored.engine_metadata  # the convert task needs them
    assert "messages" not in json.loads(stored.metadata_path.read_text(encoding="utf-8"))["engine_metadata"]
