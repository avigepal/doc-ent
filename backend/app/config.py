from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    postgres_user: str = "pipeline"
    postgres_password: str = "change-me"
    postgres_db: str = "pipeline"
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    redis_url: str = "redis://redis:6379/0"

    data_dir: str = "/data/pipeline"
    # For programs that call the API directly (curl, scripts); never sent to the browser.
    bearer_token: str = "change-me-to-a-long-random-token"
    # What people type on the dashboard's login screen. Empty = the bearer token is the password
    # (the old behavior), so set this. See app/auth.py.
    login_password: str = ""
    # How long a dashboard sign-in lasts before the password is asked for again.
    session_hours: int = 168

    llama_text_url: str = "http://llama-text:8080"
    llama_vision_url: str = "http://llama-vision:8080"
    llama_embed_url: str = "http://llama-embed:8080"

    # Empty for a local llama-server (no auth needed). Set to call a
    # remote OpenAI-compatible endpoint instead.
    llama_text_api_key: str = ""
    llama_vision_api_key: str = ""
    llama_embed_api_key: str = ""

    llama_text_model: str = "local"
    llama_vision_model: str = "local"
    llama_embed_model: str = "local"

    # Set in the Docker image (see backend/Dockerfile's dashboard-build
    # stage); left empty for local `uvicorn` dev, where main.py falls
    # back to computing dashboard/dist's path relative to this repo.
    dashboard_dist_dir: str = ""

    # How often (seconds) the auto-ingest beat task scans raw/, enqueues
    # conversion, and enqueues summarization. 0 disables the periodic
    # schedule entirely (manual /ingest/* calls still work either way).
    auto_ingest_interval_seconds: int = 60

    # Summarizing a file is many sequential LLM calls and is by far the
    # slowest stage; search doesn't need it. False makes auto-ingest stop
    # after convert + index (the manual /ingest/summarize still works).
    auto_summarize: bool = True

    # How many passages of the user's documents go to the model with a
    # question. 8 missed the answer in 1 of 28 test questions; 12 found it in
    # all 28 (see backend/app/evaluation).
    search_top_k: int = 12
    # A detailed answer ("in detail", "tell me everything about ...") is planned as sections and written
    # one section per model call (app/search/sectioned.py), when the sources are at least this long.
    sectioned_answers: bool = True
    sectioned_min_chars: int = 6000

    # "Full report" mode reads every passage of the documents in scope (see
    # app/search/report.py). report_batch_chars: how much of a document one
    # note-taking call reads; report_max_files / report_max_chars: the largest
    # selection it accepts (it reads all of it, so it takes minutes on a big
    # one); report_notes_chars: the notes the final report is written from.
    # report_parallel: how many note-taking calls run at once. It only speeds
    # things up if llama-server was started with at least that many slots
    # (`parallel` / `-np`); with one slot the extra calls just queue. Keep it
    # one below the slot count so a chat can still be answered meanwhile.
    report_parallel: int = 3
    report_batch_chars: int = 12000
    report_max_files: int = 50  # matches MAX_UPLOAD_FILES in main.py
    report_max_chars: int = 1000000
    report_notes_chars: int = 40000
    # A report about a subject (a person, a company) in a folder with more than report_max_files
    # documents reads only the documents that mention it: at most report_subject_max_files of them
    # (the passages around the mentions, not whole documents). Above report_confirm_above documents
    # the dashboard first shows how many were found and how long it will take;
    # report_seconds_per_call is the guess that estimate is based on.
    report_subject_max_files: int = 300
    report_confirm_above: int = 20
    report_seconds_per_call: int = 20
    # A report over a big folder runs in the celery-report worker, so it outlives the browser tab. If no
    # worker picks it up within report_queue_wait_seconds it is run inside the request instead.
    report_background: bool = True
    report_queue_wait_seconds: int = 45

    # Docling (PDF/Office conversion) runs in its own docling-serve, like the
    # llama-servers: this project only sends it files over HTTP (see
    # app/conversion/backends.py and docker-compose.docling.yml).
    # docling_url: where that server is (this machine or a remote Linux box).
    # docling_api_key: its DOCLING_SERVE_API_KEY; empty when it has none.
    # docling_timeout: seconds one file may take (big scans are slow).
    # docling_ocr: OCR text inside images/scans. Off = faster, but scanned
    #   pages and screenshots-in-PDFs come out with no text.
    # docling_tables: rebuild table structure. Off = faster, but tables
    #   come out as plain text.
    # docling_table_mode: "fast" or "accurate" (slower, better on complex tables).
    docling_url: str = "http://host.docker.internal:5001"
    docling_api_key: str = ""
    docling_timeout: float = 900.0
    docling_ocr: bool = True
    docling_tables: bool = True
    docling_table_mode: str = "fast"

    # Chunks sent to the embedding server per request while indexing.
    # Bigger = fewer round trips, until the server's batch/slot limits.
    embed_batch_size: int = 16

    # A file queued for a stage isn't queued for it again for this long --
    # see app/queue_guard.py. Longer than a typical backlog takes to drain
    # keeps the queue free of duplicates; the tasks skip finished work, so a
    # too-short value only wastes a few no-op tasks.
    queue_dedupe_seconds: int = 1800

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
