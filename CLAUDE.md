# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Docent (the repo is `doc-ent`) is a self-hosted document-intelligence tool: files dropped into a folder are converted to Markdown, chunked, embedded into pgvector, and then searched, summarized, edited and exported from a React dashboard. `README.md` has the phase-by-phase history and curl examples; this file is the map.

## Commands

Backend (Python, run from `backend/`; the local venv is `backend/.venv`):

```bash
python -m pytest tests -q                          # whole suite
python -m pytest tests/test_xlsx_ops.py::test_name -q   # one test
```

- `tests/test_chained_edits.py` and `tests/test_combined_edit.py` import PyMuPDF, which a bare local venv lacks. Locally add `--ignore` for them (they run in the Docker image).
- Tests that need Postgres (`test_progress_sql.py`, `test_scan_incremental.py`, `test_documents_tree.py`) **skip** when no database answers. To run them: `docker compose up -d postgres`, then `POSTGRES_HOST=localhost POSTGRES_PASSWORD=change-me python -m pytest ...`. They run inside a rolled-back transaction.
- The host name `redis` only resolves inside Docker. Code that reads Redis outside it (pause flag, scan status) fails open, but each failed lookup is slow, so stub those in anything you benchmark.

Dashboard (`dashboard/`, Vite + React + Tailwind v4; no frontend test runner):

```bash
npm run dev        # :5173, proxies API calls to the backend on :8000
npx tsc -b         # typecheck only
npm run build      # tsc -b && vite build (the api image bundles dist/)
npm run lint       # oxlint
```

Stack (Docker or Podman Compose, `.env` copied from `.env.example`):

```bash
docker compose build api                                  # one image, tag doc-worker:latest, used by api AND every celery-* worker
docker compose up -d                                       # postgres, redis, flower, api, workers, beat
docker compose -f docker-compose.docling.yml up -d         # the Docling server (needs a GPU box); set DOCLING_SERVE_API_KEY
```

The LLMs are **not** in compose: text, vision and embedding models run in `llama-server` (or Ollama) elsewhere and are reached via `LLAMA_*_URL`. Docling likewise runs as its own `docling-serve`, reached via `DOCLING_URL`. Neither Docling nor torch is in the app image.

## Architecture

**One image, many workers.** `backend/app/main.py` (FastAPI, also serves the built dashboard) and every Celery worker run the same image and differ only in `command:`. Queue names in `celery_app.py` (`task_routes`) must match the `-Q` flags in `docker-compose.yml`: `convert_fast`, `convert_ocr`, `convert_vision`, `convert_email_archive`, `upload`, `summarize`, `reduce`, `correlate` (also runs indexing), `report`, `auto_ingest`. Celery Beat fires `auto_ingest_cycle` every `AUTO_INGEST_INTERVAL_SECONDS`.

**Ingestion pipeline.** `data/raw/<folder>/...` → `ingestion/walker.py` (hash + libmagic type) → `handlers/registry.py` routes by mime type → `tasks/convert.py` → `conversion/backends.py` → Markdown in `data/converted/` → `summarization/chunker.py` → `tasks/index.py` embeds chunks into `chunks` (pgvector, **1024-dim** fixed in `models.py`). `pipeline_runner.py` is the shared logic behind both the manual `/ingest/*` endpoints and the timer, so they always behave the same. `queue_guard.py` (Redis claims) keeps a file from being queued twice. `run_scan` writes in batches of 200 and reports progress via `scan_status.py`.

**Conversion.** `resolve_conversion_backend` picks: images → vision LLM; plain text → read as-is; old `.doc/.xls/.ppt` → `legacy_office.py` (headless LibreOffice to the modern format, then Docling); everything else → `DoclingBackend`, an HTTP client for docling-serve with a retry ladder (default reader → pypdfium2 → full-page OCR) triggered by `garble_score`. Always request `image_export_mode=placeholder`, or base64 images flood the index.

**One entry point for questions.** `POST /query/stream` (SSE) in `main.py`. Routing is rules first, model second (`routing/router.py`): keyword lookup, catalog answer, search, chat, report. Three behaviors are **explicit toggles via `payload.mode`** and are never guessed from wording: `report` (reads every document in scope, see `search/report*.py`; big folders run as a `report` job and survive closing the tab), `edit` (`editing/service.py`) and `spreadsheet` (`search/spreadsheet.py`). Generated files are recorded in export history (`history/store.py`) and announced with a `file` event.

**Editing never rewrites a whole document.** For Word (`docx_ops.py`) and Excel (`xlsx_ops.py`) the model gets a numbered/described view and replies with a short list of operations that code applies, so untouched content keeps its formatting. Text from documents must never become a live spreadsheet formula: `export/tables.py` forces text cells, and `xlsx_ops.safe_formula` refuses formulas that reach outside the workbook. Sorting or deleting by a column of formulas needs values calculated by LibreOffice (`computed_values`).

**Pause / resume.** `pipeline_control.py` holds a Redis flag. Stop purges the pipeline queues and clears queue claims; the timer and manual `/ingest/*` calls (409) honor it, while dashboard uploads deliberately do not.

**Progress page.** `ingestion/progress_query.py` computes everything with SQL aggregates (never load every file/job row). The pure builders in `ingestion/progress.py` remain as the reference implementation that `tests/test_progress_sql.py` checks the SQL against. A job's state is its *latest* job per (file, type): `NEWEST` in the SQL.

**Auth.** Two separate secrets: `LOGIN_PASSWORD` (typed on the login screen, checked by `POST /auth/login`, answered with an HMAC session token that expires) and `BEARER_TOKEN` (scripts only). `require_bearer_token` accepts either. With `LOGIN_PASSWORD` empty the bearer token is the password. `app/auth.py`.

**Schema changes** go in `app/migrations.py` (idempotent statements run at API startup) **and** `backend/sql/init.sql` (only runs on a brand-new volume). There is no Alembic.

**Data directory** (`DATA_DIR`, bind-mounted from `HOST_DATA_DIR`): `raw/`, `converted/`, `summaries/`, `outputs/` (edit results), `exports/`. `raw/uploads/` holds a chat's attachments, not the library: it is hidden from Documents and never deleted by Delete all.

## Dashboard conventions

- Pages in `dashboard/src/pages/` stay thin; put new UI pieces in `dashboard/src/components/` (the Ask page was getting too big).
- Every call goes through `api/client.ts`; `authFetch` drops the session and opens `/login` on any 401.
- Tailwind classes must be written out in full (no building class names from variables) so Tailwind can see them; shared class strings live in `ui.ts`.
- Flat neutral theme, no gradients. Colors are CSS variables in `index.css` for light, system-dark and pinned-dark (keep the last two blocks identical). Use `--surface` for cards/inputs/dialogs and `--paper` for the page.

## Gotchas

- **Windows paths in SQL.** PostgreSQL's `LIKE` treats backslash as its escape character, and folder names contain `_` and `%`. Match path prefixes with `column.startswith(prefix, autoescape=True)` (see `ingestion/documents_query.py`) or `search/folder_filter.py`, never a hand-built `LIKE`.
- Embedding dimension is part of the database column; changing the embedding model means a new column size and re-embedding everything. A different quantization of the same model needs re-embedding for clean results.
- GPU memory is shared between llama-server and Docling on the Linux box; the layout that works keeps the chat model alone on one GPU and puts embeddings and Docling on the other.
- `.env` is git-ignored and `docker compose` only reads it at container start: recreate containers after changing it.
