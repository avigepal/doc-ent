"""Tiny idempotent migration runner — this project has no Alembic (too
small to need it yet). init.sql only runs on a brand-new Postgres volume,
so schema additions also need a path that applies to an already-running
database. Each statement here must be safe to run every startup."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

_STATEMENTS = [
    "ALTER TABLE files ADD COLUMN IF NOT EXISTS title TEXT",
    "ALTER TABLE files ADD COLUMN IF NOT EXISTS author TEXT",
    "ALTER TABLE files ADD COLUMN IF NOT EXISTS doc_created_at TIMESTAMPTZ",
    "ALTER TABLE files ADD COLUMN IF NOT EXISTS page_count INT",
    """
    CREATE TABLE IF NOT EXISTS query_history (
        id BIGSERIAL PRIMARY KEY,
        question TEXT NOT NULL,
        answer TEXT NOT NULL,
        sources JSONB NOT NULL DEFAULT '[]'::jsonb,
        grounded BOOLEAN NOT NULL DEFAULT FALSE,
        cross_doc JSONB,
        statistical JSONB,
        filter_folders JSONB NOT NULL DEFAULT '[]'::jsonb,
        filter_author TEXT,
        filter_title TEXT,
        attached_filenames JSONB NOT NULL DEFAULT '[]'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS export_history (
        id BIGSERIAL PRIMARY KEY,
        query_history_id BIGINT REFERENCES query_history(id) ON DELETE SET NULL,
        filename TEXT NOT NULL,
        fmt VARCHAR NOT NULL,
        stored_path TEXT NOT NULL,
        size_bytes BIGINT NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_query_history_created_at ON query_history (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_export_history_created_at ON export_history (created_at DESC)",
]


def run_migrations(engine: Engine) -> None:
    with engine.begin() as conn:
        for statement in _STATEMENTS:
            conn.execute(text(statement))
