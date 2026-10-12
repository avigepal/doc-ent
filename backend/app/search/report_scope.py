"""Which documents a full report reads.

Small scopes (a few attached files, a folder of up to `report_max_files`) are
read in full, as before. A bigger folder is narrowed to the subject of the
request ("everything about Asha Rao", "the link between Asha Rao and Acme"):
only the documents that mention it are read, and only the passages around the
mentions (app/search/subjects.py). With no subject to narrow by, the whole
scope is returned and the report engine says it is too big.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterator

from sqlalchemy.orm import Session

import app.search.report as report_module
from app.search.pgvector_retrieval import file_filter_conditions
from app.search.report import (
    ReportDocument,
    ReportLimits,
    batch_sections,
)
from app.search.subjects import (
    Subject,
    count_scope_files,
    extract_subjects,
    find_subject_hits,
    format_plan,
    load_subject_documents,
    rank_files,
    subjects_hint,
)


@dataclass
class ReportScope:
    documents: list[ReportDocument]
    limits: ReportLimits
    subjects: list[Subject] = field(default_factory=list)
    # shown to the user instead of a report: how many documents were found and what
    # reading them costs, with a button to go ahead
    plan: str | None = None
    # shown instead of a report when the subject appears nowhere
    nothing: str | None = None

    @property
    def narrowed(self) -> bool:
        return bool(self.subjects)

    @property
    def prompt_extra(self) -> str:
        return subjects_hint(self.subjects)


def _status(text: str) -> dict:
    return {"event": "status", "data": {"text": text}}


def estimate_seconds(documents: list[ReportDocument], limits: ReportLimits, seconds_per_call: int) -> float:
    calls = sum(len(batch_sections(d.sections, limits.batch_chars)) for d in documents)
    return math.ceil(calls / max(limits.parallel, 1)) * seconds_per_call


def resolve_report_scope(
    session: Session,
    *,
    raw_dir: str,
    request: str,
    folders: list[str] | None,
    author: str | None,
    title: str | None,
    file_ids: list[int] | None,
    chat_only: bool,
    llm,
    limits: ReportLimits,
    subject_max_files: int,
    confirm_above: int,
    seconds_per_call: int,
    confirmed: bool,
) -> Iterator[dict]:
    """`confirmed`: the user already saw the plan (or this is a follow-up to a
    report that was confirmed), so no plan is returned. A generator: it yields
    "status" events before the slow steps so the dashboard can show progress,
    and returns the ReportScope (use `scope = yield from resolve_report_scope(...)`)."""

    def read_everything() -> ReportScope:
        documents = (
            [] if chat_only else report_module.load_report_documents(session, raw_dir, folders, author, title, file_ids)
        )
        return ReportScope(documents, limits)

    # attached files (at most the upload limit) are always read in full, and so is a small folder
    if chat_only or file_ids:
        return read_everything()
    conditions = file_filter_conditions(raw_dir, folders, author, title, None)
    if count_scope_files(session, conditions) <= limits.max_files:
        return read_everything()

    yield _status("Working out what the report is about…")
    subjects = extract_subjects(request, llm)
    if not subjects:
        return read_everything()  # nothing to narrow by: the report engine explains the folder is too big

    yield _status("Finding the documents that mention it…")
    hits = find_subject_hits(session, subjects, conditions)
    ranked = rank_files(hits)
    if not ranked:
        names = " or ".join(s.name for s in subjects)
        return ReportScope([], limits, subjects, nothing=f"None of the documents in this scope mention {names}.")

    chosen = ranked[:subject_max_files]
    documents = load_subject_documents(session, subjects, hits.paths, chosen)
    # the narrowed set has its own file limit; its text is only the passages around the mentions
    narrowed_limits = ReportLimits(
        batch_chars=limits.batch_chars,
        max_files=max(limits.max_files, subject_max_files),
        max_chars=max(limits.max_chars, sum(d.chars for d in documents)),
        notes_chars=limits.notes_chars,
        parallel=limits.parallel,
    )
    scope = ReportScope(documents, narrowed_limits, subjects)
    if not confirmed and len(documents) > confirm_above:
        everywhere = sum(1 for f in ranked if hits.subjects_in(f) == len(subjects))
        scope.plan = format_plan(
            subjects,
            found=len(ranked),
            reading=len(documents),
            in_all_subjects=everywhere,
            seconds=estimate_seconds(documents, narrowed_limits, seconds_per_call),
        )
    return scope
