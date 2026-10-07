from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FileRecord(Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    path: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    sha256: Mapped[str] = mapped_column(String, nullable=False, index=True)
    mime_type: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    queue: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="discovered")
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    # Document-intrinsic metadata, populated at convert time — NULL until
    # then, and still NULL after if the backend couldn't determine it.
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str | None] = mapped_column(Text, nullable=True)
    doc_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    jobs: Mapped[list["JobRecord"]] = relationship(back_populates="file")


class JobRecord(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    file_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("files.id", ondelete="CASCADE"))
    job_type: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    file: Mapped["FileRecord"] = relationship(back_populates="jobs")


class ChunkRecord(Base):
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    file_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("files.id", ondelete="CASCADE"))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding = mapped_column(Vector(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class QueryHistoryRecord(Base):
    """One row per query run through /query or /query/upload. Stores the
    full result, not just the question: re-running a query against a local
    LLM is slow, so the History page restores the stored answer instead."""

    __tablename__ = "query_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    grounded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cross_doc: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    statistical: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # filter_* rather than author/title: `files` already has title/author
    # columns meaning document-intrinsic metadata, and an unprefixed name
    # here would be ambiguous in any query joining both tables.
    filter_folders: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    filter_author: Mapped[str | None] = mapped_column(Text, nullable=True)
    filter_title: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Non-empty only for "+"-attach queries, which bypass the corpus.
    attached_filenames: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ExportHistoryRecord(Base):
    __tablename__ = "export_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # SET NULL, not CASCADE: deleting a query shouldn't destroy the record
    # of a file that still exists on disk and is still downloadable.
    query_history_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("query_history.id", ondelete="SET NULL"), nullable=True
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    fmt: Mapped[str] = mapped_column(String, nullable=False)
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
