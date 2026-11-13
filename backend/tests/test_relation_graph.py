import pytest

from app.search.email_graph import (
    MAX_PEOPLE,
    PairRow,
    RelationQuestion,
    build_graph,
    parse_relation_question,
    relation_text,
)


def mail(sender: str, recipient: str, emails: int = 1, subject: str = "hello") -> PairRow:
    return PairRow(
        sender_name=sender,
        sender_addr=f"{sender}@example.com",
        recipient_name=recipient,
        recipient_addr=f"{recipient}@example.com",
        emails=emails,
        first=None,
        last=None,
        subjects=(subject,),
    )


# ---------- reading the question ----------


@pytest.mark.parametrize(
    "question, expected",
    [
        (
            "is there any relation between person a and person b and person c",
            RelationQuestion(people=(("person", "a"), ("person", "b"), ("person", "c"))),
        ),
        ("relation between Alice, Bob and Carol?", RelationQuestion(people=(("alice",), ("bob",), ("carol",)))),
        ("connections between alice and bob", RelationQuestion(people=(("alice",), ("bob",)))),
        ("who is connected to alice brown", RelationQuestion(people=(("alice", "brown"),))),
        ("contacts of bob", RelationQuestion(people=(("bob",),))),
    ],
)
def test_reads_people_from_relation_questions(question, expected):
    assert parse_relation_question(question) == expected


@pytest.mark.parametrize(
    "question",
    [
        "relation between alice",  # one person is not a relation
        "who mailed whom",  # that is the mail question, not a chart
        "what does the report say about budgets",
        "",
    ],
)
def test_ignores_other_questions(question):
    assert parse_relation_question(question) is None


def test_too_many_people_falls_through():
    names = " and ".join(f"person {chr(ord('a') + i)}" for i in range(MAX_PEOPLE + 1))
    assert parse_relation_question(f"relation between {names}") is None


# ---------- building the chart ----------


def test_between_mode_links_only_the_named_people():
    question = RelationQuestion(people=(("alice",), ("bob",), ("carol",)))
    rows = [
        mail("alice", "bob", emails=2),
        mail("bob", "alice", emails=1),
        mail("alice", "dave"),  # dave is not asked about
    ]

    graph = build_graph(question, rows)

    assert graph["mode"] == "between"
    assert [(n["id"], n["label"], n["person"]) for n in graph["nodes"]] == [
        ("p0", "alice", True),
        ("p1", "bob", True),
    ]
    assert len(graph["edges"]) == 1
    edge = graph["edges"][0]
    assert edge["emails"] == 3
    assert {edge["source"], edge["target"]} == {"p0", "p1"}
    assert graph["missing"] == ["carol"]


def test_around_mode_draws_direct_contacts_of_one_person():
    question = RelationQuestion(people=(("alice",),))
    rows = [
        mail("alice", "bob", emails=2),
        mail("carol", "alice", emails=1),
        mail("alice", "alice"),  # a message to themself is not a link
        mail("bob", "carol"),  # does not involve alice
    ]

    graph = build_graph(question, rows)

    node_ids = {n["id"] for n in graph["nodes"]}
    assert node_ids == {"p0", "addr:bob@example.com", "addr:carol@example.com"}
    assert sorted(e["emails"] for e in graph["edges"]) == [1, 2]
    assert graph["missing"] == []


def test_no_rows_gives_an_empty_chart():
    graph = build_graph(RelationQuestion(people=(("alice",), ("bob",))), [])
    assert graph["edges"] == []
    assert graph["missing"] == ["alice", "bob"]


def test_text_names_the_people_with_no_email():
    question = RelationQuestion(people=(("alice",), ("bob",), ("carol",)))
    graph = build_graph(question, [mail("alice", "bob")])

    text = relation_text(question, graph)

    assert "Emails among Alice, Bob, Carol: 1 linked pair(s)." in text
    assert "No emails found for: Carol." in text
    assert text.count("\n|") == 3  # header, separator and one edge row
