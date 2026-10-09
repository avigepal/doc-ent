"""Tiny idempotent migration runner — this project has no Alembic (too
small to need it yet). init.sql only runs on a brand-new Postgres volume,
so schema additions also need a path that applies to an already-running
database. Each statement here must be safe to run every startup."""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

logger = logging.getLogger(__name__)

# `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` takes an ACCESS EXCLUSIVE lock
# before it checks whether the column exists, so even a no-op ALTER waits
# behind any open transaction on that table — and a long conversion holds
# one for minutes. Worse, every other write to the table then queues
# behind the waiting ALTER, freezing the whole pipeline and the API's
# startup with it. Failing fast is safe because every statement here is
# idempotent: if one is skipped, the next startup applies it.
_LOCK_TIMEOUT = "3s"

_STATEMENTS = [
    "ALTER TABLE files ADD COLUMN IF NOT EXISTS title TEXT",
    "ALTER TABLE files ADD COLUMN IF NOT EXISTS author TEXT",
    "ALTER TABLE files ADD COLUMN IF NOT EXISTS doc_created_at TIMESTAMPTZ",
    "ALTER TABLE files ADD COLUMN IF NOT EXISTS page_count INT",
    "ALTER TABLE files ADD COLUMN IF NOT EXISTS mtime_ns BIGINT",
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS queue TEXT",
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
    """
    CREATE TABLE IF NOT EXISTS conversation_pins (
        conversation_id TEXT PRIMARY KEY,
        pinned_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "ALTER TABLE query_history ADD COLUMN IF NOT EXISTS chat_only BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE query_history ADD COLUMN IF NOT EXISTS conversation_id TEXT NOT NULL DEFAULT ''",
    # How the reply was produced ("keyword", "search", "chat", ...), so a
    # restored chat can label keyword matches apart from model answers.
    "ALTER TABLE query_history ADD COLUMN IF NOT EXISTS route TEXT NOT NULL DEFAULT ''",
    "CREATE INDEX IF NOT EXISTS idx_query_history_created_at ON query_history (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_query_history_conversation_id ON query_history (conversation_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_export_history_created_at ON export_history (created_at DESC)",
    # Edited files remember what they were made from: the file card after a
    # reload, and "edit the previous result" in the same chat.
    "ALTER TABLE export_history ADD COLUMN IF NOT EXISTS source_name TEXT",
    "ALTER TABLE export_history ADD COLUMN IF NOT EXISTS source_file_id BIGINT",
    # Hybrid retrieval (vector + keyword + file metadata). Generated
    # columns keep the tsvectors in sync with no application code.
    "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS heading TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS chunk_index INT NOT NULL DEFAULT 0",
    """
    ALTER TABLE chunks ADD COLUMN IF NOT EXISTS search_vector tsvector
        GENERATED ALWAYS AS (to_tsvector('english', coalesce(heading, '') || ' ' || text)) STORED
    """,
    """
    ALTER TABLE files ADD COLUMN IF NOT EXISTS meta_vector tsvector
        GENERATED ALWAYS AS (
            to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(author, '') || ' '
                || regexp_replace(path, '[/\\\\._-]', ' ', 'g'))
        ) STORED
    """,
    "CREATE INDEX IF NOT EXISTS idx_chunks_search_vector ON chunks USING gin (search_vector)",
    "CREATE INDEX IF NOT EXISTS idx_files_meta_vector ON files USING gin (meta_vector)",
    "CREATE INDEX IF NOT EXISTS idx_chunks_file_id ON chunks (file_id)",
    "CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops)",
]


def run_migrations(engine: Engine) -> None:
    for statement in _STATEMENTS:
        try:
            with engine.begin() as conn:
                conn.execute(text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
                conn.execute(text(statement))
        except OperationalError:
            logger.warning(
                "migration skipped (table busy, will retry next startup): %s", " ".join(statement.split())[:80]
            )
