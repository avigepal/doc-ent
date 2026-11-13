from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import settings
from app.ingestion.email_store import link_rows, store_email_links
from app.models import FileRecord
from app.search.email_graph import (
    MailQuestion,
    PairResult,
    PairRow,
    answer_text,
    email_events,
    email_pairs,
    has_emails,
    parse_mail_question,
    top_people,
)


# ---------- reading the question ----------


@pytest.mark.parametrize(
    "question, expected",
    [
        ("who mailed whom", MailQuestion()),
        ("Who emailed whom?", MailQuestion()),
        ("who is emailing whom", MailQuestion()),
        ("who emailed alice", MailQuestion(recipient=("alice",))),
        ("Who has emailed Alice Brown?", MailQuestion(recipient=("alice", "brown"))),
        ("who wrote to bob@example.com", MailQuestion(recipient=("bob@example.com",))),
        ("who did alice email", MailQuestion(sender=("alice",))),
        ("who did Alice Brown mail?", MailQuestion(sender=("alice", "brown"))),
        ("who has bob messaged", MailQuestion(sender=("bob",))),
        ("emails from alice", MailQuestion(sender=("alice",))),
        ("show me all emails from alice to bob", MailQuestion(sender=("alice",), recipient=("bob",))),
        ("emails to the finance team", MailQuestion(recipient=("finance", "team"))),
        ("emails between alice and bob", MailQuestion(sender=("alice",), recipient=("bob",), either_way=True)),
        ("email between alice and bob about the contract", MailQuestion(sender=("alice",), recipient=("bob",), either_way=True)),
    ],
)
def test_who_wrote_to_whom_questions_are_understood(question, expected):
    assert parse_mail_question(question) == expected


@pytest.mark.parametrize(
    "question",
    [
        "what is the warranty on the power supply?",
        "who sent the invoice?",  # about a document, not about emails
        "who wrote the report",
        "what is the email address of acme",
        "summarize the email",
        "Who was copied on the supply contract draft email?",  # a question about one email, not a sender
        "who was cc'd on the email",
        "who is mentioned in the email",
        "how long is the notice period",
        "difference between the two leases",
        "emails",
        "",
    ],
)
def test_other_questions_are_left_to_the_ordinary_search(question):
    assert parse_mail_question(question) is None


def test_a_question_is_described_in_words():
    assert MailQuestion(sender=("alice",), recipient=("bob",)).describe() == "from alice to bob"
    assert MailQuestion(sender=("alice",), recipient=("bob",), either_way=True).describe() == "between alice and bob"
    assert MailQuestion().describe() == ""


# ---------- the rows kept per message ----------


def _message(index=0, sender="a@x.com", to=("b@x.com",), cc=(), subject="Hi", sent_at="2024-03-05T10:00:00+00:00"):
    person = lambda addr: {"name": addr.split("@")[0].title(), "addr": addr}
    return {
        "index": index,
        "subject": subject,
        "sender": person(sender) if sender else None,
        "to": [person(a) for a in to],
        "cc": [person(a) for a in cc],
        "sent_at": sent_at,
    }


def test_one_row_per_message_and_recipient():
    rows = link_rows(7, [_message(to=("b@x.com", "c@x.com"), cc=("d@x.com",))])

    assert [(r.recipient_addr, r.kind) for r in rows] == [("b@x.com", "to"), ("c@x.com", "to"), ("d@x.com", "cc")]
    assert {r.sender_addr for r in rows} == {"a@x.com"} and {r.file_id for r in rows} == {7}
    assert rows[0].sent_at == datetime(2024, 3, 5, 10, tzinfo=timezone.utc)


def test_someone_in_both_to_and_cc_counts_once_as_to():
    rows = link_rows(1, [_message(to=("b@x.com",), cc=("b@x.com",))])

    assert [(r.recipient_addr, r.kind) for r in rows] == [("b@x.com", "to")]


def test_a_message_without_recipients_or_sender_is_still_kept():
    rows = link_rows(1, [_message(to=()), _message(index=1, sender=None)])

    assert rows[0].recipient_addr == "" and rows[0].sender_addr == "a@x.com"
    assert rows[1].sender_addr == "" and rows[1].recipient_addr == "b@x.com"


def test_a_bad_date_is_kept_as_no_date():
    assert link_rows(1, [_message(sent_at="garbage")])[0].sent_at is None


# ---------- the answer ----------


def _row(**kwargs):
    base = dict(
        sender_name="Alice",
        sender_addr="alice@x.com",
        recipient_name="Bob",
        recipient_addr="bob@x.com",
        emails=3,
        first=datetime(2024, 1, 2, tzinfo=timezone.utc),
        last=datetime(2024, 3, 4, tzinfo=timezone.utc),
        subjects=("Contract | draft", "Meeting"),
    )
    return PairRow(**{**base, **kwargs})


def test_the_answer_is_a_table_with_counts_dates_and_subjects():
    text_out = answer_text(MailQuestion(), PairResult([_row()], 1, 3, ["/raw/a.eml"]), [])

    assert text_out.startswith("3 emails in 1 sender → recipient pair.")
    assert "| Alice <alice@x.com> | Bob <bob@x.com> | 3 | 2024-01-02 | 2024-03-04 | Contract \\| draft; Meeting |" in text_out


def test_the_answer_says_when_only_the_busiest_pairs_are_shown():
    result = PairResult([_row()], 120, 400, [])

    assert "The 1 busiest are shown." in answer_text(MailQuestion(), result, [])


def test_no_match_says_so_and_names_who_does_send_email():
    text_out = answer_text(MailQuestion(sender=("zed",)), PairResult([], 0, 0, []), ["Alice <alice@x.com>"])

    assert text_out.startswith("I found no emails from zed in the selected documents.")
    assert "Alice <alice@x.com>" in text_out


# ---------- the database ----------


@pytest.fixture()
def session():
    try:
        engine = create_engine(settings.database_url, connect_args={"connect_timeout": 2})
        connection = engine.connect()
        connection.execute(text("select 1"))
        connection.execute(text("select 1 from email_links limit 1"))
        connection.rollback()
    except Exception:
        pytest.skip("no Postgres with the email_links table reachable")
    transaction = connection.begin()
    db = Session(bind=connection)
    try:
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()


RAW = "/zz_email_test/raw"


def _file(db, relative):
    record = FileRecord(
        path=f"{RAW}/{relative}", sha256=f"h-{relative}", mime_type="message/rfc822", size_bytes=1,
        queue="convert_email_archive", status="converted",
    )
    db.add(record)
    db.flush()
    return record.id


@pytest.fixture()
def mailbox_rows(session):
    one = _file(session, "mail/one.eml")
    two = _file(session, "mail/two.mbox")
    other = _file(session, "legal/other.eml")
    store_email_links(session, one, [_message(0, "alice@x.com", ("bob@x.com",), subject="Contract", sent_at="2024-01-02T09:00:00+00:00")])
    store_email_links(
        session,
        two,
        [
            _message(0, "alice@x.com", ("bob@x.com", "carol@x.com"), subject="Plan", sent_at="2024-02-01T09:00:00+00:00"),
            _message(1, "bob@x.com", ("alice@x.com",), subject="Re: Plan", sent_at="2024-02-02T09:00:00+00:00"),
            _message(2, "alice@x.com", ("bob@x.com",), subject="Plan v2", sent_at="2024-03-04T09:00:00+00:00"),
        ],
    )
    store_email_links(session, other, [_message(0, "zed@y.com", ("yan@y.com",), subject="Legal")])
    session.flush()
    return {"one": one, "two": two, "other": other}


def _scope(ids):
    from app.search.pgvector_retrieval import file_filter_conditions

    return file_filter_conditions(None, None, None, None, list(ids.values()))


def _pairs(session, ids, question):
    return {(r.sender_addr, r.recipient_addr): r for r in email_pairs(session, question, _scope(ids)).rows}


def test_every_pair_is_counted_by_message_not_by_row(session, mailbox_rows):
    result = email_pairs(session, MailQuestion(), _scope(mailbox_rows))
    pairs = {(r.sender_addr, r.recipient_addr): r.emails for r in result.rows}

    assert pairs[("alice@x.com", "bob@x.com")] == 3  # one in one.eml, two in the mbox
    assert pairs[("alice@x.com", "carol@x.com")] == 1
    assert pairs[("bob@x.com", "alice@x.com")] == 1
    assert pairs[("zed@y.com", "yan@y.com")] == 1
    assert result.pair_count == 4 and result.email_count == 5  # alice's mail to bob and carol is one message
    assert result.rows[0].sender_addr == "alice@x.com"  # busiest first


def test_dates_and_example_subjects_come_with_each_pair(session, mailbox_rows):
    row = _pairs(session, mailbox_rows, MailQuestion())[("alice@x.com", "bob@x.com")]

    assert row.first.date().isoformat() == "2024-01-02" and row.last.date().isoformat() == "2024-03-04"
    assert set(row.subjects) <= {"Contract", "Plan", "Plan v2"} and len(row.subjects) == 3


def test_who_emailed_alice_and_who_did_alice_email(session, mailbox_rows):
    to_alice = _pairs(session, mailbox_rows, MailQuestion(recipient=("alice",)))
    from_alice = _pairs(session, mailbox_rows, MailQuestion(sender=("alice",)))

    assert set(to_alice) == {("bob@x.com", "alice@x.com")}
    assert set(from_alice) == {("alice@x.com", "bob@x.com"), ("alice@x.com", "carol@x.com")}


def test_between_two_people_covers_both_directions(session, mailbox_rows):
    between = _pairs(session, mailbox_rows, MailQuestion(sender=("alice",), recipient=("bob",), either_way=True))
    one_way = _pairs(session, mailbox_rows, MailQuestion(sender=("alice",), recipient=("bob",)))

    assert set(between) == {("alice@x.com", "bob@x.com"), ("bob@x.com", "alice@x.com")}
    assert set(one_way) == {("alice@x.com", "bob@x.com")}


def test_a_name_matches_the_display_name_or_the_address_case_insensitively(session, mailbox_rows):
    assert _pairs(session, mailbox_rows, MailQuestion(sender=("ALICE",)))
    assert _pairs(session, mailbox_rows, MailQuestion(sender=("alice@x.com",)))
    assert not _pairs(session, mailbox_rows, MailQuestion(sender=("nobody",)))


def test_wildcard_characters_in_a_name_match_literally(session, mailbox_rows):
    assert not _pairs(session, mailbox_rows, MailQuestion(sender=("%",)))
    assert not _pairs(session, mailbox_rows, MailQuestion(sender=("_lice",)))


def test_only_the_files_in_scope_are_counted(session, mailbox_rows):
    only_legal = {"other": mailbox_rows["other"]}

    assert set(_pairs(session, only_legal, MailQuestion())) == {("zed@y.com", "yan@y.com")}
    assert has_emails(session, _scope(only_legal))
    assert not has_emails(session, _scope({"none": -1}))


def test_storing_again_replaces_the_files_rows(session, mailbox_rows):
    store_email_links(session, mailbox_rows["other"], [_message(0, "new@y.com", ("yan@y.com",))])
    session.flush()

    pairs = _pairs(session, {"other": mailbox_rows["other"]}, MailQuestion())
    assert set(pairs) == {("new@y.com", "yan@y.com")}


def test_the_events_carry_the_table_and_the_files_it_came_from(session, mailbox_rows):
    events = list(
        email_events(session, MailQuestion(sender=("alice",)), "", None, [mailbox_rows["one"], mailbox_rows["two"]])
    )

    assert [e["event"] for e in events] == ["meta", "token", "extra"]
    assert events[0]["data"]["grounded"] is True and len(events[0]["data"]["sources"]) == 2
    assert "| From | To | Emails |" in events[1]["data"]["text"]


def test_a_scope_without_emails_gives_no_events_so_the_ordinary_search_runs(session):
    plain = FileRecord(path=f"{RAW}/docs/a.pdf", sha256="h", mime_type="application/pdf", size_bytes=1, queue="convert_fast")
    session.add(plain)
    session.flush()

    assert email_events(session, MailQuestion(), "", None, [plain.id]) is None


def test_the_busiest_senders_are_offered_as_hints(session, mailbox_rows):
    assert top_people(session, _scope(mailbox_rows))[0].endswith("<alice@x.com>")
