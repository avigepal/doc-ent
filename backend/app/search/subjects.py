"""Subject reports: "everything about Asha Rao", "the link between Asha Rao and
Acme Ltd" over a folder far too big to read in full.

The request is turned into one or two subjects (a name plus the other ways it
is written). The documents that mention them are found with the chunks'
full-text index, ranked so documents mentioning every subject come first, and
only the passages around the mentions (the matching chunks and their
neighbours) are handed to the report engine in app/search/report.py.

Pure helpers (parsing the model's reply, ranking, formatting the plan) are
unit-tested; the SQL needs Postgres.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from functools import reduce
from typing import Protocol, Sequence

from sqlalchemy import and_, func, literal_column, select
from sqlalchemy.orm import Session

from app.models import ChunkRecord, FileRecord
from app.search.report import ReportDocument

MAX_SUBJECTS = 3
MAX_ALIASES = 5
MAX_NAME_CHARS = 80
# A document that mentions the subject on every page still only contributes
# its best passages.
MAX_CHUNKS_PER_FILE = 30

_NO_THINKING = {"chat_template_kwargs": {"enable_thinking": False}}


class LLMClient(Protocol):
    def chat(self, system: str, user: str, temperature: float = 0.2, **kwargs) -> str: ...


@dataclass(frozen=True)
class Subject:
    name: str
    aliases: tuple[str, ...] = ()

    @property
    def terms(self) -> tuple[str, ...]:
        """Every way this subject is written, the name first."""
        return (self.name, *self.aliases)


EXTRACT_SYSTEM_PROMPT = (
    "You read a request for a report and name what it is about.\n"
    'Reply with JSON only: {"subjects": [{"name": "...", "aliases": ["..."]}]}.\n'
    "- A subject is a specific person, organisation, product, place or topic that the report is about, "
    "written the way it would appear in a document (for a person, the full name).\n"
    "- aliases: other ways the same subject is written that you can tell from the request "
    '(a short form, initials, a spelling variant). Never guess aliases that are not clearly the same thing. '
    "Do not list pronouns or job titles.\n"
    "- At most three subjects. A request about the link or relationship between two subjects has two.\n"
    'If the request names no specific subject (for example "summarise everything"), reply {"subjects": []}.'
)


def parse_subjects(reply: str) -> list[Subject]:
    """The model's reply as subjects; anything malformed is no subjects."""
    start, end = reply.find("{"), reply.rfind("}")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(reply[start : end + 1])
    except ValueError:
        return []
    raw = data.get("subjects") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []

    subjects: list[Subject] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()[:MAX_NAME_CHARS]
        if len(name) < 2 or name.lower() in seen:
            continue
        aliases: list[str] = []
        for alias in item.get("aliases") or []:
            alias = str(alias).strip()[:MAX_NAME_CHARS]
            if len(alias) >= 2 and alias.lower() not in seen and alias.lower() != name.lower() and alias not in aliases:
                aliases.append(alias)
        seen.add(name.lower())
        seen.update(a.lower() for a in aliases)
        subjects.append(Subject(name, tuple(aliases[:MAX_ALIASES])))
    return subjects[:MAX_SUBJECTS]


def extract_subjects(request: str, llm: LLMClient) -> list[Subject]:
    """What the report is about. A failed or empty model call means "no
    subject": the caller then reads the whole scope as before."""
    try:
        reply = llm.chat(
            EXTRACT_SYSTEM_PROMPT, request, temperature=0.0, max_tokens=300, extra_body=_NO_THINKING
        )
    except Exception:
        return []
    return parse_subjects(reply)


# ---------- ranking and the plan shown before reading ----------

@dataclass
class SubjectHits:
    """Documents that mention the subjects: file id -> path, and per subject how many passages mention it."""

    paths: dict[int, str] = field(default_factory=dict)
    counts: list[dict[int, int]] = field(default_factory=list)  # one {file_id: passages} per subject

    def subjects_in(self, file_id: int) -> int:
        return sum(1 for per_subject in self.counts if per_subject.get(file_id))

    def total_in(self, file_id: int) -> int:
        return sum(per_subject.get(file_id, 0) for per_subject in self.counts)


def rank_files(hits: SubjectHits) -> list[int]:
    """Documents that mention more of the subjects first, then the ones that
    mention them most, then by path so the order never changes between runs."""
    return sorted(hits.paths, key=lambda f: (-hits.subjects_in(f), -hits.total_in(f), hits.paths[f]))


def describe_duration(seconds: float) -> str:
    minutes = max(1, math.ceil(seconds / 60))
    if minutes < 60:
        return f"about {minutes} minute{'s' if minutes != 1 else ''}"
    hours, rest = divmod(minutes, 60)
    return f"about {hours} h {rest} min" if rest else f"about {hours} h"


def format_plan(
    subjects: Sequence[Subject],
    found: int,
    reading: int,
    in_all_subjects: int,
    seconds: float,
) -> str:
    names = " and ".join(f"**{s.name}**" for s in subjects)
    lines = [f"I found **{found} documents** that mention {names}."]
    if len(subjects) > 1:
        every = "both" if len(subjects) == 2 else "all"
        lines.append(f"{in_all_subjects} of them mention {every}, and those are read first.")
    if reading < found:
        lines.append(f"This is more than one report reads, so I'll use the {reading} best matches.")
    lines.append(
        f"I'll read the passages around the mentions in {reading} documents; that will take "
        f"{describe_duration(seconds)}."
    )
    lines.append(
        "Press **Read them all** to write the report, or choose a smaller folder under Scope to narrow it down."
    )
    return "\n\n".join(lines)


def subjects_hint(subjects: Sequence[Subject]) -> str:
    """Extra instruction for the report writer when the request compares or links subjects."""
    if len(subjects) < 2:
        return ""
    names = [s.name for s in subjects]
    joined = ", ".join(names[:-1]) + f" and {names[-1]}"
    return (
        f"\n\nThis report is about {joined}. After the overview, give each subject its own section. "
        f"Then add a section \"Connections between {joined}\" listing every link the notes show "
        "(documents that mention both, shared people, organisations, projects, places, dates, transactions), "
        "each with its citation. If the notes show no direct link, say so plainly instead of guessing one."
    )


# ---------- database ----------

def _subject_query(subject: Subject):
    """tsquery matching any way the subject is written, as a phrase ("asha rao"
    matches those words next to each other), stemmed like the index."""
    config = literal_column("'english'::regconfig")
    parts = [func.phraseto_tsquery(config, term) for term in subject.terms]
    return reduce(lambda a, b: a.op("||")(b), parts)


def count_scope_files(session: Session, conditions: list) -> int:
    return session.execute(select(func.count()).select_from(FileRecord).where(*conditions)).scalar_one()


def find_subject_hits(session: Session, subjects: Sequence[Subject], conditions: list) -> SubjectHits:
    hits = SubjectHits()
    for subject in subjects:
        tsquery = _subject_query(subject)
        rows = session.execute(
            select(FileRecord.id, FileRecord.path, func.count(ChunkRecord.id))
            .join(ChunkRecord, ChunkRecord.file_id == FileRecord.id)
            .where(and_(ChunkRecord.search_vector.op("@@")(tsquery), *conditions))
            .group_by(FileRecord.id, FileRecord.path)
        ).all()
        per_subject: dict[int, int] = {}
        for file_id, path, passages in rows:
            hits.paths[file_id] = path
            per_subject[file_id] = passages
        hits.counts.append(per_subject)
    return hits


def load_subject_documents(
    session: Session, subjects: Sequence[Subject], paths: dict[int, str], file_ids: Sequence[int]
) -> list[ReportDocument]:
    """For each file, the chunks that mention a subject (its best MAX_CHUNKS_PER_FILE)
    plus the chunk before and after each, in reading order."""
    if not file_ids:
        return []
    tsquery = reduce(lambda a, b: a.op("||")(b), [_subject_query(s) for s in subjects])
    matched = (
        select(
            ChunkRecord.file_id.label("file_id"),
            ChunkRecord.chunk_index.label("chunk_index"),
            func.row_number()
            .over(
                partition_by=ChunkRecord.file_id,
                order_by=func.ts_rank_cd(ChunkRecord.search_vector, tsquery).desc(),
            )
            .label("rn"),
        )
        .where(ChunkRecord.file_id.in_(list(file_ids)), ChunkRecord.search_vector.op("@@")(tsquery))
        .subquery()
    )
    near = select(matched.c.file_id, matched.c.chunk_index).where(matched.c.rn <= MAX_CHUNKS_PER_FILE).subquery()
    rows = session.execute(
        select(ChunkRecord.file_id, ChunkRecord.chunk_index, ChunkRecord.heading, ChunkRecord.text)
        .join(
            near,
            and_(
                ChunkRecord.file_id == near.c.file_id,
                ChunkRecord.chunk_index.between(near.c.chunk_index - 1, near.c.chunk_index + 1),
            ),
        )
        .distinct()
        .order_by(ChunkRecord.file_id, ChunkRecord.chunk_index)
    ).all()

    sections: dict[int, list[tuple[str, str]]] = {file_id: [] for file_id in file_ids}
    for file_id, _index, heading, text in rows:
        sections[file_id].append((heading or "", text))
    return [ReportDocument(paths[f], sections[f], f) for f in file_ids if sections[f]]
