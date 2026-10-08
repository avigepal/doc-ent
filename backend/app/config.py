from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    postgres_user: str = "pipeline"
    postgres_password: str = "change-me"
    postgres_db: str = "pipeline"
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    redis_url: str = "redis://redis:6379/0"

    data_dir: str = "/data/pipeline"
    bearer_token: str = "change-me-to-a-long-random-token"

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

    # Docling (PDF/Office conversion) speed vs. fidelity -- see
    # app/conversion/backends.py. Defaults match Docling's own.
    # docling_ocr: OCR text inside images/scans. Off = faster, but scanned
    #   pages and screenshots-in-PDFs come out with no text.
    # docling_tables: rebuild table structure. Off = faster, but tables
    #   come out as plain text.
    # docling_table_mode: "fast" or "accurate" (slower, better on complex tables).
    # docling_page_batch_size: pages the models process per batch; 0 =
    #   Docling's default (4). Raise it (8, 16) to use the GPU better, as
    #   far as VRAM allows.
    docling_ocr: bool = True
    docling_tables: bool = True
    docling_table_mode: str = "fast"
    docling_page_batch_size: int = 0

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
