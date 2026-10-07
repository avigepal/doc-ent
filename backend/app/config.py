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
