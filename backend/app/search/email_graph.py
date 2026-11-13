"""Answering "who mailed whom" from the stored email rows (table email_links), without a model.

A search finds the emails that look most like the question; it cannot promise to find all of them, and it
cannot count. These questions are answered from rows written when each email was converted:

    who mailed whom                       every sender -> recipient pair, with counts
    who emailed alice                     everybody who wrote to alice
    who did alice email                   everybody alice wrote to
    emails from alice to bob              that pair
    emails between alice and bob          both directions

The wording is read with rules. Only questions that clearly ask this are taken; anything else, or a scope
that holds no emails, goes on to the ordinary search.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator

from sqlalchemy import and_, distinct, func, or_, select
from sqlalchemy.dialects.postgresql import array_agg
from sqlalchemy.orm import Session

from app.models import EmailLinkRecord as L
from app.models import FileRecord
from app.search.pgvector_retrieval import file_filter_conditions

MAX_ROWS = 50
MAX_SOURCES = 20
MAX_NAME_WORDS = 3
_SUBJECT_CHARS = 60
_SUBJECTS_SHOWN = 3
_HINTS = 8

_PRONOUNS = {"whom", "who", "me", "us", "him", "her", "them", "everyone", "everybody", "anyone", "anybody", "someone", "somebody", "you"}
_ARTICLES = {"the", "a", "an", "my", "our", "his", "her", "their", "mr", "mrs", "ms", "dr"}
_STOP = {
    "to", "from", "about", "regarding", "re", "in", "on", "since", "during", "last", "this", "that", "for", "with", "and",
    "between", "by", "at", "after", "before", "of", "is", "are", "was", "were", "has", "have", "had", "did", "do", "does",
    "who", "what", "which", "when", "where", "how", "many", "much", "email", "emails", "mail", "mails", "message",
    "messages", "sent", "send", "wrote", "write", "received", "get", "got", "please", "show", "list", "me", "all", "every",
}

_MAIL_VERB = r"(?:e-?mail(?:ed|s|ing)?|mail(?:ed|s|ing)?|messag(?:ed|es|ing))"
_NOUN = r"(?:e-?mails?|mails?|messages?|correspondence|conversations?)"
_WRITE_TO = r"(?:wrote to|writes to|writing to|sen[dt](?:s|ing)? (?:an? )?(?:e-?mail|mail|message)s? to)"

_SENDER_FORM = re.compile(
    rf"\bwho (?:did|does|has|have|had|is|was|else did)\s+(?P<a>.+?)\s+(?:{_MAIL_VERB}|{_WRITE_TO})\b"
)
_RECIPIENT_FORM = re.compile(rf"\bwho (?:has |have |had |ever |all |else |all of |is |are |was |were )*(?:{_MAIL_VERB}|{_WRITE_TO})\s*(?P<b>.*)")
_FROM_TO = re.compile(rf"\b{_NOUN}\s+(?:sent\s+)?from\s+(?P<a>.+?)(?:\s+to\s+(?P<b>.+))?$")
_TO_ONLY = re.compile(rf"\b{_NOUN}\s+(?:sent\s+)?to\s+(?P<b>.+)$")
_BETWEEN = re.compile(rf"\b{_NOUN}\s+(?:exchanged\s+)?between\s+(?P<a>.+?)\s+and\s+(?P<b>.+)$")


@dataclass(frozen=True)
class MailQuestion:
    sender: tuple[str, ...] = ()  # words that must appear in the sender's name or address
    recipient: tuple[str, ...] = ()
    either_way: bool = False  # sender and recipient may be swapped ("between A and B")

    def describe(self) -> str:
        sender, recipient = " ".join(self.sender), " ".join(self.recipient)
        if self.either_way:
            return f"between {sender} and {recipient}"
        parts = []
        if sender:
            parts.append(f"from {sender}")
        if recipient:
            parts.append(f"to {recipient}")
        return " ".join(parts)


def _words(text: str | None) -> tuple[str, ...]:
    """The name at the start of `text`: up to a few words, stopping at a word like "to" or "about"."""
    if not text:
        return ()
    words: list[str] = []
    for raw in re.findall(r"[\w.@+'-]+", text):
        word = raw.strip(".'-")
        if not word:
            continue
        if not words and word in _ARTICLES:
            continue
        if word in _STOP or word in _PRONOUNS:
            break
        words.append(word)
        if len(words) == MAX_NAME_WORDS:
            break
    return tuple(words)


def _whole_name(text: str | None) -> tuple[str, ...]:
    """Like _words, but the text must be nothing except a name: "alice brown" yes, "copied on the contract" no."""
    words = _words(text)
    consumed = [w for w in re.findall(r"[\w.@+'-]+", text or "") if w.strip(".'-") and w.strip(".'-") not in _ARTICLES]
    return words if len(words) == len(consumed) else ()


def parse_mail_question(question: str) -> MailQuestion | None:
    """The question as who-wrote-to-whom, or None when it is not clearly one."""
    text = " ".join(question.lower().replace("?", " ").split())
    if not text or len(text) > 200:
        return None

    match = _BETWEEN.search(text)
    if match:
        a, b = _words(match["a"]), _words(match["b"])
        if a and b:
            return MailQuestion(sender=a, recipient=b, either_way=True)
        return None

    match = _SENDER_FORM.search(text)
    if match and _whole_name(match["a"]):
        return MailQuestion(sender=_whole_name(match["a"]))

    match = _RECIPIENT_FORM.search(text)
    if match:
        return MailQuestion(recipient=_words(match["b"]))

    match = _FROM_TO.search(text)
    if match and _words(match["a"]):
        return MailQuestion(sender=_words(match["a"]), recipient=_words(match["b"]))

    match = _TO_ONLY.search(text)
    if match and _words(match["b"]):
        return MailQuestion(recipient=_words(match["b"]))
    return None


# ---------- the rows ----------


def _person_matches(name_col, addr_col, words: tuple[str, ...]):
    return and_(*(or_(name_col.icontains(w, autoescape=True), addr_col.icontains(w, autoescape=True)) for w in words))


def _conditions(question: MailQuestion, scope: list):
    conditions = [L.sender_addr != "", L.recipient_addr != "", *scope]
    sender = _person_matches(L.sender_name, L.sender_addr, question.sender) if question.sender else None
    recipient = _person_matches(L.recipient_name, L.recipient_addr, question.recipient) if question.recipient else None
    if question.either_way and sender is not None and recipient is not None:
        swapped = and_(
            _person_matches(L.sender_name, L.sender_addr, question.recipient),
            _person_matches(L.recipient_name, L.recipient_addr, question.sender),
        )
        conditions.append(or_(and_(sender, recipient), swapped))
    else:
        for part in (sender, recipient):
            if part is not None:
                conditions.append(part)
    return conditions


@dataclass(frozen=True)
class PairRow:
    sender_name: str
    sender_addr: str
    recipient_name: str
    recipient_addr: str
    emails: int
    first: object
    last: object
    subjects: tuple[str, ...]


@dataclass(frozen=True)
class PairResult:
    rows: list[PairRow]
    pair_count: int
    email_count: int
    sources: list[str]


def has_emails(session: Session, scope: list) -> bool:
    query = select(L.id).join(FileRecord, FileRecord.id == L.file_id).where(L.sender_addr != "", *scope).limit(1)
    return session.execute(query).first() is not None


def email_pairs(session: Session, question: MailQuestion, scope: list) -> PairResult:
    conditions = _conditions(question, scope)
    join = L.file_id == FileRecord.id
    pairs = (
        select(
            L.sender_addr,
            func.max(L.sender_name),
            L.recipient_addr,
            func.max(L.recipient_name),
            func.count(distinct(func.row(L.file_id, L.msg_index))),
            func.min(L.sent_at),
            func.max(L.sent_at),
            array_agg(distinct(L.subject)),
        )
        .join(FileRecord, join)
        .where(*conditions)
        .group_by(L.sender_addr, L.recipient_addr)
        .order_by(func.count(distinct(func.row(L.file_id, L.msg_index))).desc(), L.sender_addr, L.recipient_addr)
    )
    rows = [
        PairRow(
            sender_name=sender_name or "",
            sender_addr=sender_addr,
            recipient_name=recipient_name or "",
            recipient_addr=recipient_addr,
            emails=count,
            first=first,
            last=last,
            subjects=tuple(s for s in (subjects or []) if s)[:_SUBJECTS_SHOWN],
        )
        for sender_addr, sender_name, recipient_addr, recipient_name, count, first, last, subjects in session.execute(
            pairs.limit(MAX_ROWS)
        ).all()
    ]
    pair_count = session.execute(
        select(func.count(distinct(func.row(L.sender_addr, L.recipient_addr)))).join(FileRecord, join).where(*conditions)
    ).scalar_one()
    email_count = session.execute(
        select(func.count(distinct(func.row(L.file_id, L.msg_index)))).join(FileRecord, join).where(*conditions)
    ).scalar_one()
    sources = [
        path
        for (path,) in session.execute(
            select(FileRecord.path).join(L, join).where(*conditions).distinct().order_by(FileRecord.path).limit(MAX_SOURCES)
        ).all()
    ]
    return PairResult(rows, pair_count, email_count, sources)


def top_people(session: Session, scope: list) -> list[str]:
    """The most active senders in scope, to help when a name matched nothing."""
    query = (
        select(L.sender_addr, func.max(L.sender_name), func.count())
        .join(FileRecord, L.file_id == FileRecord.id)
        .where(L.sender_addr != "", *scope)
        .group_by(L.sender_addr)
        .order_by(func.count().desc())
        .limit(_HINTS)
    )
    return [_label(name or "", addr) for addr, name, _ in session.execute(query).all()]


# ---------- the answer ----------


def _label(name: str, addr: str) -> str:
    return f"{name} <{addr}>" if name and addr and name.lower() != addr else addr or name


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _day(moment) -> str:
    return moment.strftime("%Y-%m-%d") if moment else ""


def _subject_line(subjects: tuple[str, ...]) -> str:
    return "; ".join(s if len(s) <= _SUBJECT_CHARS else s[: _SUBJECT_CHARS - 1] + "…" for s in subjects)


def answer_text(question: MailQuestion, result: PairResult, hints: list[str]) -> str:
    what = question.describe()
    if not result.rows:
        text = f"I found no emails {what} in the selected documents." if what else "I found no emails in the selected documents."
        if hints:
            text += "\n\nThe people who send the most email here: " + "; ".join(hints) + "."
        return text

    emails = f"{result.email_count} email{'s' if result.email_count != 1 else ''}"
    pairs = f"{result.pair_count} sender → recipient pair{'s' if result.pair_count != 1 else ''}"
    lines = [f"{emails} {what}".strip() + f", in {pairs}." if what else f"{emails} in {pairs}."]
    if result.pair_count > len(result.rows):
        lines[0] += f" The {len(result.rows)} busiest are shown."
    lines += ["", "| From | To | Emails | First | Last | Example subjects |", "|---|---|---|---|---|---|"]
    for row in result.rows:
        lines.append(
            f"| {_cell(_label(row.sender_name, row.sender_addr))} | {_cell(_label(row.recipient_name, row.recipient_addr))} "
            f"| {row.emails} | {_day(row.first)} | {_day(row.last)} | {_cell(_subject_line(row.subjects))} |"
        )
    return "\n".join(lines)


def email_events(
    session: Session, question: MailQuestion, raw_dir: str, folders: list[str] | None, file_ids: list[int] | None
) -> Iterator[dict] | None:
    """Events for the answer, or None when the scope holds no emails (the ordinary search then takes over)."""
    scope = file_filter_conditions(raw_dir, folders, None, None, file_ids)
    if not has_emails(session, scope):
        return None
    result = email_pairs(session, question, scope)
    hints = [] if result.rows else top_people(session, scope)
    text = answer_text(question, result, hints)
    return _events(text, result.sources)


def _events(text: str, sources: list[str], graph: dict | None = None) -> Iterator[dict]:
    yield {"event": "meta", "data": {"sources": sources, "grounded": True}}
    yield {"event": "token", "data": {"text": text}}
    yield {"event": "extra", "data": {"cross_doc": None, "statistical": None, "graph": graph}}


# ---------- relation chart: who is linked to whom ----------
#
#     relation between alice, bob and carol     the emails among those people
#     is there any relation between alice and bob
#     who is connected to alice                 everybody alice wrote to or got mail from
#
# Same stored rows as above, drawn as a graph: people are nodes, email traffic is the edge between them.

MAX_GRAPH_PAIRS = 500
MAX_PEOPLE = 10
_RELATION_BETWEEN = re.compile(r"\b(?:relation(?:ship)?s?|connections?|connected)\s+between\s+(?P<names>.+)$")
_RELATION_AROUND = re.compile(r"\b(?:connected to|connections? of|contacts? of|linked to)\s+(?P<name>.+)$")
_NAME_SPLIT = re.compile(r",|\band\b")


@dataclass(frozen=True)
class RelationQuestion:
    people: tuple[tuple[str, ...], ...]  # one tuple of name words per person

    @property
    def around(self) -> bool:
        return len(self.people) == 1


def parse_relation_question(question: str) -> RelationQuestion | None:
    """The people a relation chart is about, or None when the question does not ask for one."""
    text = " ".join(question.lower().replace("?", " ").split())
    if not text or len(text) > 200:
        return None

    between = _RELATION_BETWEEN.search(text)
    if between:
        pieces = _NAME_SPLIT.split(between["names"])
        people = tuple(dict.fromkeys(p for p in (_words(piece) for piece in pieces) if p))
        if len(people) < 2 or len(people) > MAX_PEOPLE:
            return None  # "relation between" needs two people
        return RelationQuestion(people=people)

    around = _RELATION_AROUND.search(text)
    if around and _words(around["name"]):
        return RelationQuestion(people=(_words(around["name"]),))
    return None


def _matches_person(words: tuple[str, ...], name: str | None, addr: str | None) -> bool:
    """The same test as the SQL in _person_matches: every word appears in the name or the address."""
    name, addr = (name or "").lower(), (addr or "").lower()
    return all(w in name or w in addr for w in words)


def _relation_conditions(question: RelationQuestion) -> list:
    if question.around:
        words = question.people[0]
        return [or_(_person_matches(L.sender_name, L.sender_addr, words), _person_matches(L.recipient_name, L.recipient_addr, words))]
    sender_any = or_(*(_person_matches(L.sender_name, L.sender_addr, w) for w in question.people))
    recipient_any = or_(*(_person_matches(L.recipient_name, L.recipient_addr, w) for w in question.people))
    return [sender_any, recipient_any]


def relation_rows(session: Session, question: RelationQuestion, scope: list) -> list[PairRow]:
    conditions = [L.sender_addr != "", L.recipient_addr != "", *scope, *_relation_conditions(question)]
    query = (
        select(
            L.sender_addr,
            func.max(L.sender_name),
            L.recipient_addr,
            func.max(L.recipient_name),
            func.count(distinct(func.row(L.file_id, L.msg_index))),
            func.min(L.sent_at),
            func.max(L.sent_at),
            array_agg(distinct(L.subject)),
        )
        .join(FileRecord, L.file_id == FileRecord.id)
        .where(*conditions)
        .group_by(L.sender_addr, L.recipient_addr)
        .order_by(func.count(distinct(func.row(L.file_id, L.msg_index))).desc(), L.sender_addr, L.recipient_addr)
        .limit(MAX_GRAPH_PAIRS)
    )
    return [
        PairRow(
            sender_name=sender_name or "",
            sender_addr=sender_addr,
            recipient_name=recipient_name or "",
            recipient_addr=recipient_addr,
            emails=count,
            first=first,
            last=last,
            subjects=tuple(s for s in (subjects or []) if s)[:_SUBJECTS_SHOWN],
        )
        for sender_addr, sender_name, recipient_addr, recipient_name, count, first, last, subjects in session.execute(query).all()
    ]


def build_graph(question: RelationQuestion, rows: list[PairRow]) -> dict:
    """Nodes and edges for the chart. Named people are "p0", "p1", ...; anyone else found by the
    search is keyed by address. Edges join two nodes; `forward` counts mail from the first node to the second."""
    labels: dict[str, str] = {f"p{i}": " ".join(words) for i, words in enumerate(question.people)}
    edges: dict[tuple[str, str], dict] = {}

    def add(a: str, b: str, row: PairRow) -> None:
        key = (a, b) if a <= b else (b, a)
        edge = edges.setdefault(key, {"forward": 0, "backward": 0, "first": None, "last": None, "subjects": []})
        edge["forward" if a == key[0] else "backward"] += row.emails
        for moment in (row.first, row.last):
            if moment is not None:
                edge["first"] = moment if edge["first"] is None else min(edge["first"], moment)
                edge["last"] = moment if edge["last"] is None else max(edge["last"], moment)
        for subject in row.subjects:
            if subject not in edge["subjects"] and len(edge["subjects"]) < _SUBJECTS_SHOWN:
                edge["subjects"].append(subject)

    for row in rows:
        if question.around:
            center = question.people[0]
            sent_by_center = _matches_person(center, row.sender_name, row.sender_addr)
            got_by_center = _matches_person(center, row.recipient_name, row.recipient_addr)
            if sent_by_center == got_by_center:
                continue  # a message between the person and themself, or between two others
            other_name, other_addr = (row.recipient_name, row.recipient_addr) if sent_by_center else (row.sender_name, row.sender_addr)
            other = f"addr:{other_addr}"
            labels.setdefault(other, _label(other_name, other_addr))
            if sent_by_center:
                add("p0", other, row)
            else:
                add(other, "p0", row)
        else:
            a = next((f"p{i}" for i, w in enumerate(question.people) if _matches_person(w, row.sender_name, row.sender_addr)), None)
            b = next((f"p{i}" for i, w in enumerate(question.people) if _matches_person(w, row.recipient_name, row.recipient_addr)), None)
            if a is None or b is None or a == b:
                continue
            add(a, b, row)

    linked = {node for pair in edges for node in pair}
    out_edges = [
        {
            "source": a,
            "target": b,
            "emails": e["forward"] + e["backward"],
            "forward": e["forward"],
            "backward": e["backward"],
            "first": _day(e["first"]),
            "last": _day(e["last"]),
            "subjects": e["subjects"],
        }
        for (a, b), e in sorted(edges.items(), key=lambda kv: -(kv[1]["forward"] + kv[1]["backward"]))
    ]
    nodes = [
        {"id": node, "label": labels[node], "person": node.startswith("p")}
        for node in labels
        if node in linked or (question.around and node == "p0")
    ]
    missing = [] if question.around else [" ".join(w) for i, w in enumerate(question.people) if f"p{i}" not in linked]
    return {"mode": "around" if question.around else "between", "nodes": nodes, "edges": out_edges, "missing": missing}


def relation_text(question: RelationQuestion, graph: dict) -> str:
    people = [" ".join(w) for w in question.people]
    if question.around:
        head = f"{people[0].title()} has email links with {len(graph['edges'])} people."
    else:
        head = f"Emails among {', '.join(p.title() for p in people)}: {len(graph['edges'])} linked pair(s)."
    if graph["missing"]:
        head += " No emails found for: " + ", ".join(m.title() for m in graph["missing"]) + "."

    labels = {n["id"]: n["label"] for n in graph["nodes"]}
    lines = [head, "", "| Person | Person | First → second | Second → first | First | Last | Example subjects |", "|---|---|---|---|---|---|---|"]
    for e in graph["edges"]:
        lines.append(
            f"| {_cell(labels[e['source']])} | {_cell(labels[e['target']])} | {e['forward']} | {e['backward']} "
            f"| {e['first']} | {e['last']} | {_cell(_subject_line(tuple(e['subjects'])))} |"
        )
    return "\n".join(lines)


def relation_events(
    session: Session, question: RelationQuestion, raw_dir: str, folders: list[str] | None, file_ids: list[int] | None
) -> Iterator[dict] | None:
    """Events for the relation chart, or None when no email links the people asked about (the ordinary
    search then takes over). That keeps an ordinary "connection between the contract and the invoice"
    question from being answered as a chart of nobody."""
    scope = file_filter_conditions(raw_dir, folders, None, None, file_ids)
    if not has_emails(session, scope):
        return None
    graph = build_graph(question, relation_rows(session, question, scope))
    if not graph["edges"]:
        return None
    sources = [
        path
        for (path,) in session.execute(
            select(FileRecord.path)
            .join(L, L.file_id == FileRecord.id)
            .where(L.sender_addr != "", L.recipient_addr != "", *scope, *_relation_conditions(question))
            .distinct()
            .order_by(FileRecord.path)
            .limit(MAX_SOURCES)
        ).all()
    ]
    return _events(relation_text(question, graph), sources, graph)
