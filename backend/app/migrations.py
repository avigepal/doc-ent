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
]


def run_migrations(engine: Engine) -> None:
    with engine.begin() as conn:
        for statement in _STATEMENTS:
            conn.execute(text(statement))
