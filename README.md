# Doc Summarization Pipeline

Local-only pipeline: ingest a mixed document corpus, convert + summarize it
with llama.cpp-served local models, and serve search/correlation reports
through a FastAPI API + React dashboard. Single Linux box, Docker Compose,
Tailscale-only network until fully built, then exposed to the office LAN.

## Status

| Phase | Scope | Status |
|---|---|---|
| 1. Ingestion foundation | manifest walker, handler registry, pre-run summary | ✅ built, tested |
| 2. Conversion pipeline | Docling (`convert_fast`), email archives (`convert_email_archive`: .eml/.mbox/.msg/.pst) | ✅ built, tested; Marker/Tika fallback + `convert_ocr`/`convert_vision` still 🚧 (need Tesseract probe + llama-server) |
| 3. Summarization | chunking, map-summarize, hierarchical reduce (file-level) | ✅ built, tested; group/final reduce 🚧 (blocked on grouping-strategy decision) |
| 4. Search + correlation | pgvector retrieval, folder-scoped (search only selected top-level `raw/` folders); unified `/query` (grounded answer + cross-doc + statistical in one call) | ✅ built, tested; statistical mode's spreadsheet `tables` input 🚧 (not yet wired to a source) |
| 5. Output & hardening | pandoc PDF-first export (xelatex), ad-hoc export (download a query result directly, no saved file needed) | ✅ built, tested, **verified live** (real PDF produced through the API) — pandoc reference template + systemd/boot hardening ⬜ not started |
| — Auto-ingest | Celery Beat polls `raw/` on a timer and runs scan → convert → summarize automatically | ✅ built, **verified live** (dropped a file into `raw/`, picked up without any manual call) |
| — Containerization | `docker-compose.yml` (postgres/redis/flower/api/celery workers incl. beat, all sharing one `doc-worker:latest` tag), multi-stage Dockerfile (dashboard + API + pandoc/xelatex) | ✅ **verified** under Docker (see "Docker, verified" below); also verified under Podman via `podman compose up -d --build` (see "Podman" section) — **do not** install the separate `podman-compose` pip package, it's buggy with this repo's compose syntax |
| 6. Access | FastAPI auth, endpoints | ✅ `/health`, `/folders` (GET list + POST create), `/ingest/scan`, `/ingest/convert`, `/ingest/summarize`, `/query`, `/query/upload`, `/ask`, `/correlate`, `/export` |
| 7. React dashboard | login, Status (scan/convert/summarize), unified Ask (folder picker + create-folder, query, "+" file attach-and-ask, inline download, source citations) | ✅ built (`dashboard/`), builds clean, **verified live** (folder creation confirmed on disk, upload-and-ask confirmed converting + chunking correctly) |

## Docker, verified

This isn't just "the compose file should work" — it was actually built and run:

```bash
cp .env.example .env
docker compose build api          # multi-stage: Node builds the dashboard, then the Python/Docling image
docker compose up -d postgres redis flower api
```

Confirmed working: the image builds clean (Docling's full dependency tree
included, ~5 min build); `postgres`/`redis`/`flower`/`api` all start and
stay up; `init.sql` actually ran (`files`/`jobs`/`chunks` tables exist);
`/health` responds; the dashboard serves at `/` **and** on a direct
client-side route like `/ask` (not just the root — see the SPA-fallback
fix earlier); `/ingest/scan` is correctly bearer-gated (401 without a
token, real response with one); a Celery worker
(`celery-convert-fast`) starts and connects to Redis on its queue.

One real bug was caught and fixed in the process: the original Dockerfile
had no `.dockerignore`, so `COPY dashboard/ ./` in the build stage
overwrote the container's freshly-`npm install`ed Linux `node_modules`
with the host's Windows one, breaking `tsc`/`vite build` inside the
container. Fixed by adding [.dockerignore](.dockerignore) excluding
`node_modules`, `dist`, `.venv`, etc.

**Not yet started in Docker**: the llama.cpp services (commented out in
`docker-compose.yml` until GGUF models exist) and the remaining Celery
workers (untested here, but they share the same image/wiring as
`celery-convert-fast`, which was verified).

## Podman (alternative to Docker)

Everything above works unchanged under Podman — `docker-compose.yml` and
`backend/Dockerfile` use only standard Compose-spec / OCI syntax, nothing
Docker-exclusive. One-time setup:

```bash
# Install Podman Desktop (or the CLI) for Windows, then:
podman machine init
podman machine start
```

**Do not** `pip install podman-compose` — that separate tool is broken for
this repo's compose file (confirmed by testing): it fails to find the
Dockerfile because `dockerfile:` is a separate field from `context:` here,
and it has also been seen creating a stray, wrongly-named duplicate
container that collides on a host port with the real one.

Use the **built-in** `podman compose` instead (space, not hyphen — ships
with Podman itself, no extra install). One command builds the image and
starts the whole stack:

```bash
cp .env.example .env
podman compose up -d --build
```

That's it — `--build` makes it (re)build `doc-worker:latest` from
`backend/Dockerfile` first, then start postgres/redis/flower/api and all
9 celery-* workers. Re-running the same command later is safe (uses cached
layers, won't rebuild from scratch unless the Dockerfile/requirements.txt
changed).

**The one thing that differs**: the commented-out GPU section in
`docker-compose.yml` (`deploy.resources.reservations.devices`) is
Compose-spec syntax Podman's compose providers support less consistently
than Docker's — when the llama.cpp services get wired up for real
(phase 3/4), that block will need checking against whichever Podman
version is in use, possibly swapping to `--device nvidia.com/gpu=all` /
CDI-style device annotations instead. Everything else — the app images,
Postgres, Redis, Flower, volumes, networking — is unaffected.

## Run it (phase 1, today)

```bash
cp .env.example .env
# edit .env: set POSTGRES_PASSWORD and BEARER_TOKEN to real values

docker compose up -d postgres redis
docker compose up -d api
```

Point `HOST_DATA_DIR` (in `.env` — the host-side path; `DATA_DIR` is a
*different* variable, the path the app sees inside containers, always
`/data/pipeline`, don't change it) at a real folder with a `raw/`
subdirectory containing a sample of your corpus, then either call this
manually once:

```bash
curl -X POST http://localhost:8000/ingest/scan \
  -H "Authorization: Bearer <your BEARER_TOKEN>"
```

or just drop files into `raw/` and wait — see "Auto-ingest" below, it
does this on its own every `AUTO_INGEST_INTERVAL_SECONDS` (default 60).

This hashes + classifies every file under `raw/`, upserts into Postgres,
and returns a summary (file counts and byte totals per queue/mime type) —
no GPU, no models, nothing destructive. Re-running it is safe (upsert on
path).

## Auto-ingest

Drop files into `raw/` and they get picked up automatically — no need to
call `/ingest/scan` → `/ingest/convert` → `/ingest/summarize` by hand
every time. A Celery Beat task (`celery-beat` + `celery-auto-ingest` in
`docker-compose.yml`) runs that exact same sequence on a timer:

```bash
docker compose up -d celery-beat celery-auto-ingest celery-convert-fast celery-convert-email-archive celery-summarize
```

It polls rather than watches for filesystem events (`inotify`/`watchdog`)
on purpose: if `raw/` ends up being a mount point to a NAS/network share
— one of the plan's open questions — event-based watching is unreliable
over network filesystems; a timer isn't. Tune or disable it via
`AUTO_INGEST_INTERVAL_SECONDS` in `.env` (`0` disables the schedule;
manual `/ingest/*` calls keep working either way). The logic is shared
with the manual endpoints (`app/pipeline_runner.py`), so "click the
button" and "wait for the next tick" always do exactly the same thing.

### Phase 2: convert the scanned files

```bash
docker compose up -d celery-convert-fast celery-convert-email-archive

curl -X POST http://localhost:8000/ingest/convert \
  -H "Authorization: Bearer <your BEARER_TOKEN>"
```

Enqueues `convert_fast` (Docling) and `convert_email_archive` (.eml/.mbox/
.msg/.pst — needs the `readpst` binary on PATH for .pst specifically) for
every file routed to those queues. Output lands in `DATA_DIR/converted/`,
mirroring `raw/`'s folder structure, as a `.md` file plus a `.md.json`
metadata sidecar (sha256, mime type, engine used, char count, timestamp).
`convert_ocr` / `convert_vision` are not wired up yet — those files stay
in `discovered` status until the Tesseract text-density probe and
llama-server vision call are built.

## Run the backend tests directly (no Docker needed)

Uses [uv](https://docs.astral.sh/uv/) (faster installs, used in the Dockerfile too):

```bash
cd backend
uv venv .venv && .venv/Scripts/activate   # or source .venv/bin/activate on Linux
uv pip install -r requirements.txt
pytest -v
```

### Phase 3: summarize converted files

Requires a live llama-server at `LLAMA_TEXT_URL` — until the llama.cpp
smoke-test below is done on the real server, this will fail at request
time (the chunking/map/reduce logic itself is fully unit-tested without
one, see `backend/tests/test_chunker.py`, `test_llm_client.py`,
`test_map_reduce.py`):

```bash
docker compose up -d celery-summarize

curl -X POST http://localhost:8000/ingest/summarize \
  -H "Authorization: Bearer <your BEARER_TOKEN>"
```

Chunks each converted `.md` by `##` section/thread boundary, map-summarizes
each chunk, hierarchically reduces into one file-level summary, writes to
`DATA_DIR/summaries/`. Group-level reduce (file -> group -> final) is still
a stub pending the grouping-strategy decision (folder vs. sender/thread vs.
date range).

### Phase 4: ask anything (needs live llama-server + embeddings)

```bash
curl -X POST http://localhost:8000/query \
  -H "Authorization: Bearer <your BEARER_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"question": "how do the Q1 numbers relate across these files?"}'
```

`/query` is the single unified entry point (what the dashboard's Ask page
calls): one retrieval, reused for a grounded answer (cites sources, says
"not found" if nothing clears the similarity threshold) AND cross-document
correlation (when retrieved chunks span 2+ files) AND statistical
correlation (when spreadsheet-derived numeric tables are available — **not
wired to a real source yet**, see below) — whichever apply, in one
response. No need to pick "ask" vs "correlate" up front. `/ask` and
`/correlate` still exist separately for direct API use if you only want
one piece. All retrieval/prompt/routing logic is unit-tested with fakes
in `test_ask.py`/`test_correlate.py`/`test_query.py`, independent of a
live server.

**Folder-scoped search**: pass `folders` to search only specific
top-level folders under `raw/` instead of the whole corpus:

```bash
curl -X GET http://localhost:8000/folders -H "Authorization: Bearer <your BEARER_TOKEN>"
# {"folders": ["contracts", "invoices"]}

curl -X POST http://localhost:8000/query \
  -H "Authorization: Bearer <your BEARER_TOKEN>" -H "Content-Type: application/json" \
  -d '{"question": "...", "folders": ["contracts"]}'
```

`/folders` lists top-level `raw/` subdirectories (reads the filesystem
directly, works even before a scan). The dashboard's Ask page fetches
this list and shows it as a folder-picker above the question box — empty
selection searches everything. Filter-pattern logic is unit-tested in
`test_folder_filter.py`/`test_folders.py`.

**Per-file metadata**: `GET /folders/<name>/files` lists every file in a
folder with its processing status plus document-intrinsic metadata
(title, author, page count, the document's own creation date — distinct
from when *we* discovered it) extracted at convert time:

```bash
curl "http://localhost:8000/folders/contracts/files" \
  -H "Authorization: Bearer <your BEARER_TOKEN>"
```

What gets filled in depends on the format: `.eml`/`.msg` (single-message
only — `.mbox`/`.pst` hold many, so there's no one title/author for those)
get real subject/sender/date from the message headers; everything else
gets a filename-based title (Docling doesn't reliably expose an embedded
document title across formats via its basic API) plus a real page count
for Docling-converted files. Fields stay `null` until a file's been
converted, or permanently if the backend couldn't determine them. Schema
additions apply to an already-running Postgres via an idempotent startup
migration (`app/migrations.py`) — no manual `ALTER TABLE` needed.

**Creating folders and getting data into them**: `POST /folders` creates a
new top-level folder under `raw/` directly from the dashboard (name is
sanitized the same way export filenames are — no `../` path traversal):

```bash
curl -X POST http://localhost:8000/folders \
  -H "Authorization: Bearer <your BEARER_TOKEN>" -H "Content-Type: application/json" \
  -d '{"name": "invoices"}'
# {"created": "invoices"}
```

Once a folder exists (dashboard-created or not), getting files into it
needs **no code at all** — `raw/` is a plain directory on the server
(`HOST_DATA_DIR/raw/<folder>` on the host, bind-mounted into every
container). Any of these work directly, with zero app involvement:
- **SFTP via FileZilla** (or any SFTP client) — connect to the server over
  SSH, navigate to `HOST_DATA_DIR/raw/<folder>`, drag files in.
- **`scp`/`rsync` over SSH** from the command line.
- Just copying files locally if you're on the box itself.

Auto-ingest (or a manual Scan) picks up anything dropped in there exactly
the same as files the dashboard uploaded — this only requires the host
has an SSH/SFTP server running (standard on most Linux distros; nothing
this project needs to provide).

**Attach-and-ask (the "+" button)**: `POST /query/upload` (multipart, not
JSON) lets you ask about file(s) on the spot without ingesting them —
they're converted in-memory, never written to `raw/` or the corpus, and
exist only for that one answer:

```bash
curl -X POST http://localhost:8000/query/upload \
  -H "Authorization: Bearer <your BEARER_TOKEN>" \
  -F "question=what does this say about revenue?" \
  -F "files=@report.pdf"
```

Same response shape as `/query` (answer, sources, cross-document and
statistical findings) — but retrieval is skipped entirely: every uploaded
file is included directly (capped at 50 files / 100MB total — tune
`MAX_UPLOAD_FILES`/`MAX_UPLOAD_TOTAL_BYTES` in `app/main.py`), so this
doesn't need pgvector/embeddings populated, only a live text llama-server.
Reuses the exact same conversion backends as ingestion (Docling/plaintext/
email), just run synchronously against a temp file instead of being
queued. Conversion + chunking logic is unit-tested without needing
Docling installed, see `test_adhoc_upload.py`.

### Phase 5: export a summary to PDF

`/export` accepts either an existing file (`relative_path`, under
`DATA_DIR/summaries/`) or ad-hoc content (`content` + `filename` — e.g. a
query result built client-side, with no file saved first):

```bash
curl -X POST http://localhost:8000/export \
  -H "Authorization: Bearer <your BEARER_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"relative_path": "sub/report.md"}' -o report.pdf

curl -X POST http://localhost:8000/export \
  -H "Authorization: Bearer <your BEARER_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"content": "# Findings\n\nSome text.", "filename": "my-query"}' -o my-query.pdf
```

Runs pandoc, PDF by default (`fmt: "pdf"`), `.docx`/`.json` available as
secondary formats, and **streams the converted file straight back** as
the response (not a JSON path) — the dashboard's Ask page downloads it
directly via a blob + browser save, no separate download step or
endpoint. Needs `pandoc` + a PDF engine (`tectonic` or `xelatex`)
installed — the command-building and filename-sanitization logic is
unit-tested with an injected fake runner, see `test_export.py`/
`test_export_adhoc.py`.

### Phase 7: the React dashboard

```bash
cd dashboard
npm install
npm run dev     # http://localhost:5173, proxies API calls to :8000 (see vite.config.ts)
```

Pages: a login screen (pastes the bearer token into `localStorage`, no
validation beyond whatever the next real request returns), Status (scan
raw/, enqueue convert/summarize, shows the scan summary — queue depth
and worker health are on Flower), and **Ask** — a single box for anything
("summarize X", "what did the Q1 report say", "how do these numbers
relate") that calls `/query` and shows the grounded answer plus
cross-document/statistical findings inline, whichever apply, with
PDF/DOCX/JSON download buttons right on the result. No separate
Correlate or Export pages/steps.

Ask's sidebar lists every folder under `raw/` with a live status dot
(pulsing = processing, green = ready, amber = pending, red = a job
failed), lets you create a new one right there, and toggles which
folder(s) to scope the search to. A "+" button next to the question box
(ChatGPT/OpenWebUI-style) lets you attach files and ask about just those,
bypassing the ingested corpus entirely for that one question.

Production build: `npm run build` produces `dashboard/dist`, which
`backend/app/main.py` mounts at `/` automatically if present — one
process, one port, no separate web server. The Dockerfile builds this
automatically (multi-stage: Node builds the dashboard, then its `dist/`
is copied into the Python image) — `docker compose up -d api` serves
both the API and the dashboard from one container.

## Next steps (build order)

1. **llama.cpp smoke test** (do this on the real server before anything
   above can actually run end to end): download/quantize a GGUF build of
   Qwen3-30B-A3B-Instruct, start `llama-server`, confirm `--tensor-split`
   balances across both GPUs, hit `/v1/chat/completions` with curl. Do the
   vision model (Qwen2.5-VL GGUF + mmproj) next, since llama.cpp's VL
   support is the riskiest piece — fall back to a LLaVA/MiniCPM-V GGUF if
   quality/stability is poor. Also start the embedding llama-server
   (Qwen3-Embedding/BGE-M3 GGUF) — phase 4 needs it.
2. **Phase 2 (remaining)**: `convert_ocr` (Tesseract text-density probe ->
   Docling OCR -> Marker fallback) and `convert_vision` (needs the vision
   llama-server above).
3. **Phase 3 (remaining)**: decide grouping strategy, implement the
   group/final `reduce` task; wire spreadsheet pandas pre-aggregation in
   before chunking (spreadsheets should never reach the LLM as raw rows)
   — this is also what phase 4's statistical correlation mode is waiting on.
4. **Phase 4 (remaining)**: populate `ChunkRecord.embedding` (needs an
   embedding step after summarize, not yet wired — chunks exist but
   aren't embedded into pgvector yet); wire the pandas-aggregated tables
   into `/correlate`'s statistical mode once phase 3's spreadsheet
   pre-aggregation exists.
5. **Phase 5 (remaining)**: a pandoc reference template for consistent
   PDF/docx styling; systemd or Docker Compose `restart: always` +
   boot-start hardening.
6. **Phase 7 (remaining)**: a `/download` endpoint so Export can actually
   serve the generated file to the browser instead of just reporting its
   server-side path; a jobs/status-list endpoint so Status can show
   live progress instead of only the last scan's snapshot.
7. Expose to the office LAN only after all phases are validated against
   the full corpus.

See `docker-compose.yml` for the full planned service topology (llama.cpp
GPU services are commented out until models exist).
