-- Phase 1: ingestion foundation schema.
-- Runs automatically on first postgres container start (docker-entrypoint-initdb.d).

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS files (
    id             BIGSERIAL PRIMARY KEY,
    path           TEXT NOT NULL UNIQUE,
    sha256         TEXT NOT NULL,
    mime_type      TEXT NOT NULL,
    size_bytes     BIGINT NOT NULL,
    queue          TEXT NOT NULL,              -- which conversion queue this file was routed to
    status         TEXT NOT NULL DEFAULT 'discovered',  -- discovered -> converted -> summarized -> ...
    discovered_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- document-intrinsic metadata, populated at convert time (NULL until
    -- then, and still NULL after if the backend couldn't determine it —
    -- see app/conversion/backends.py / email_backend.py)
    title          TEXT,
    author         TEXT,
    doc_created_at TIMESTAMPTZ,                -- the document's own creation date, not ours
    page_count     INT
);

CREATE INDEX IF NOT EXISTS idx_files_sha256 ON files (sha256);
CREATE INDEX IF NOT EXISTS idx_files_status ON files (status);

CREATE TABLE IF NOT EXISTS jobs (
    id          BIGSERIAL PRIMARY KEY,
    file_id     BIGINT REFERENCES files(id) ON DELETE CASCADE,
    job_type    TEXT NOT NULL,     -- convert_fast | convert_ocr | convert_vision | convert_email_archive | summarize | reduce | correlate
    state       TEXT NOT NULL DEFAULT 'pending',  -- pending | running | done | failed
    retries     INT NOT NULL DEFAULT 0,
    error       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs (state);

-- phase 4: chunk embeddings for search / correlation retrieval
CREATE TABLE IF NOT EXISTS chunks (
    id         BIGSERIAL PRIMARY KEY,
    file_id    BIGINT REFERENCES files(id) ON DELETE CASCADE,
    text       TEXT NOT NULL,
    embedding  vector(1024),   -- adjust to match the embedding model's dimension
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

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
);

CREATE TABLE IF NOT EXISTS export_history (
    id BIGSERIAL PRIMARY KEY,
    query_history_id BIGINT REFERENCES query_history(id) ON DELETE SET NULL,
    filename TEXT NOT NULL,
    fmt VARCHAR NOT NULL,
    stored_path TEXT NOT NULL,
    size_bytes BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_query_history_created_at ON query_history (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_export_history_created_at ON export_history (created_at DESC);
