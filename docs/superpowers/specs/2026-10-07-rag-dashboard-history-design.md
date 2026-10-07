# RAG Dashboard Restructure + Query/Export History

**Date**: 2026-10-07
**Status**: Approved, ready for implementation

## Context

The dashboard today is two pages (Status, Ask) behind a top-tab header.
That was enough while the pipeline was being built phase by phase, but it
does not read as a dashboard for a retrieval app: there is no overview of
what has been ingested, no way to browse the corpus, and — the gap that
prompted this — no record of what has been asked or exported. Every query
result is lost the moment the page is navigated away from, and every
exported PDF/docx is forgotten even though the file itself is still
sitting on the server in `exports/adhoc/`.

This change restructures the dashboard into five sections behind a
persistent sidebar, adds server-side history for both queries and
exports, and tightens the visual language into a denser, data-heavy
console.

## Decisions

Settled during brainstorming:

1. **History lives in Postgres**, not browser storage. It survives cache
   clears, is reachable from any device, and lets export history offer
   real re-download links to the already-generated files. Accepted
   trade-off: the app authenticates with a single shared bearer token, so
   there is no per-user identity — history is shared by everyone who logs
   in.
2. **Full snapshots**, not question-only rows. A history entry stores the
   answer, sources and filters, so reopening it restores the complete
   result instantly instead of re-running a slow local LLM.
3. **Full dashboard restructure**, not an incremental addition.
4. **Denser, data-heavy visual direction** — keep the existing color
   tokens (so dark mode keeps working) but tighten type, rows and spacing
   toward an analytics console.

## Architecture

### Data model

Two tables, added via the existing idempotent runner in
`backend/app/migrations.py` (this project has no Alembic — migrations are
`CREATE TABLE IF NOT EXISTS` / `ALTER TABLE ... IF NOT EXISTS` statements
run at startup from `main.py`). The same DDL is mirrored into
`backend/sql/init.sql` so fresh installs get the tables directly.

**`query_history`**

| Column | Type | Notes |
|---|---|---|
| `id` | bigserial PK | |
| `question` | text | |
| `answer` | text | |
| `sources` | jsonb | list of file paths |
| `grounded` | boolean | |
| `cross_doc` | jsonb, null | `{answer, sources}` when correlation ran |
| `statistical` | jsonb, null | `{answer, correlation_summary}` |
| `filter_folders` | jsonb | folder scope used, `[]` for whole corpus |
| `filter_author` | text, null | |
| `filter_title` | text, null | |
| `attached_filenames` | jsonb | non-empty for `+`-upload queries |
| `created_at` | timestamptz | |

Named `filter_*` deliberately: `files` already has `title`/`author`
columns meaning something different (document-intrinsic metadata), and an
unprefixed name here would be ambiguous when reading queries that join
both tables.

**`export_history`**

| Column | Type | Notes |
|---|---|---|
| `id` | bigserial PK | |
| `query_history_id` | bigint FK → `query_history.id`, null | null when exported outside a query flow |
| `filename` | text | the sanitized slug |
| `fmt` | varchar | `pdf` / `docx` / `json` |
| `stored_path` | text | absolute path under `exports/` |
| `size_bytes` | bigint | |
| `created_at` | timestamptz | |

`ON DELETE SET NULL` for the FK: deleting a query should not destroy the
record of a file that still exists on disk.

### API surface

New endpoints in `backend/app/main.py`, all bearer-gated via the existing
`require_bearer_token` dependency:

| Endpoint | Purpose |
|---|---|
| `GET /stats` | Overview metrics — document counts by status, chunks indexed, storage bytes, folder count, query/export totals, recent activity feed (the 10 most recent events merged across newly ingested files, queries run and exports created, each as `{type, label, at}`, sorted newest first) |
| `GET /documents` | Every ingested file in one call with metadata, filterable by `folder` / `status` / `q` |
| `GET /history/queries` | Paged, newest first (`limit`, `offset`) |
| `GET /history/queries/{id}` | Full snapshot for restoring a result |
| `DELETE /history/queries/{id}` | Remove one entry |
| `GET /history/exports` | Paged export list |
| `GET /history/exports/{id}/download` | Re-stream `stored_path`; 404 with a regenerate hint if the file is gone |

**Recording is server-side.** `/query` and `/query/upload` write their
history row after producing a result and add `history_id` to the
response. The Ask page passes that `history_id` to `/export`, which links
the resulting export row back to the query it came from. The browser
never writes history, so a result is recorded whether or not the tab
survives.

`GET /documents` is new rather than reusing the existing
`/folders/{name}/files`: the Documents page needs one call across all
folders, and looping the per-folder endpoint would be N round trips.

### Reuse

- `build_folder_like_patterns` (`app/search/folder_filter.py`) for any
  folder-scoped SQL in `/documents` — it already handles the
  backslash-escaping bug that broke folder filtering on Windows paths.
- `safe_filename` (`app/export/adhoc.py`) wherever user-supplied names
  reach the filesystem.
- `run_migrations` (`app/migrations.py`) — append statements, do not
  introduce a migration framework.
- The existing `FolderStatus` aggregation in
  `app/ingestion/folder_status.py` as the model for how `/stats` should
  separate pure aggregation (unit-testable) from the DB query wrapper.

### Frontend structure

`dashboard/src/App.tsx` moves from a top-tab header to a persistent left
sidebar (~220px: wordmark, five nav items, footer with logout + health
dot). Content widens from `max-w-5xl` to `max-w-7xl`. The sidebar
collapses to a top bar on narrow screens.

| Route | Page | Notes |
|---|---|---|
| `/` | Overview | KPI tiles, ingestion-status stacked bar, folder summary, recent activity |
| `/ask` | Ask | Existing behavior preserved; folder sidebar becomes a horizontal scope bar |
| `/documents` | Documents | Sortable/filterable corpus table |
| `/history` | History | Searches section + Exports section |
| `/ingestion` | Ingestion | Today's Status page, restyled |

The folder sidebar currently inside `Ask.tsx` must become a horizontal
scope bar — nesting a second sidebar inside the new app sidebar reads
badly and costs the content area its width.

Routing changes: `/` becomes Overview (today it redirects to `/status`),
and the old `/status` path redirects to `/ingestion` so existing
bookmarks keep working. The catch-all currently pointing at `/status`
repoints to `/`.

### Visual direction

Denser and data-heavy, built on the existing tokens rather than replacing
them:

- Keep every CSS custom property in `dashboard/src/index.css` unchanged
  (`--paper`, `--ink`, `--index`, `--locator`, …) so dark mode keeps
  working untouched.
- Tighten the shared class constants in `dashboard/src/ui.ts` in place
  rather than adding a parallel set — body ~13px, mono labels 11px
  uppercase, headings below today's `text-2xl`, table rows at `py-2`.
  Revising the shared constants is what keeps the restyle consistent
  instead of drifting page by page.
- KPI tiles: large display-font number, small mono label above, secondary
  stat beneath.
- No chart library. The stacked status bar and any bars are plain
  CSS/SVG.
- Keep the clipped-corner index-tab citation chips — they are tied to
  citations, which is the heart of a RAG app, and are the one archive
  flourish worth carrying into the denser look.

## Testing

Following this repo's established split:

**Unit-tested with fakes/injected dependencies** (pure logic, no DB):
- Stats aggregation/rollup shaping
- History record → response-dict serialization
- Any filter/pattern building for `/documents`

**Import smoke tests** for DB-dependent code, matching the existing
pattern that already caught a missing `python-multipart` dependency and a
broken indentation bug: `import app.main`, assert the new routes are
registered, `import app.celery_app`.

**End-to-end verification** (requires the Docker stack running):
1. `docker compose up -d --build`, confirm all containers healthy.
2. `curl` `/stats` and `/documents` with the bearer token — shapes match
   the spec, counts match what `/folders` reports.
3. Run a query from the Ask page; confirm a `query_history` row appears
   and the response carries `history_id`.
4. Export that result as PDF; confirm an `export_history` row appears
   with a `stored_path` that exists on disk.
5. Open History: the search entry restores the full result without
   re-running the model; the export entry downloads the same file.
6. Delete a history entry; confirm it disappears and the linked export
   row survives with a null FK.
7. Walk all five pages in the browser at desktop and narrow widths,
   confirm dark mode still renders correctly on each.

## Implementation notes

- Load the `dataviz` skill before writing the KPI tiles / stacked status
  bar, and `frontend-design` before the restyle — both apply directly to
  this work.
- The backend is reachable at `localhost:8000` and serves the built
  dashboard as static files, so the UI must be rebuilt
  (`docker compose up -d --build`) to be seen in the container; local
  `npm run dev` against the API works for faster iteration.

## Out of scope

- Per-user identity / per-user history (the app has one shared bearer
  token; adding real auth is a separate project).
- History retention policies or automatic cleanup — entries accumulate
  until deleted manually.
- Pinning/starring history entries.
