"""Settings for the chat API. Everything comes from environment variables."""

from datetime import date

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # .env.lab is written by `lab up`; .env overrides it. Real env vars override both.
    model_config = SettingsConfigDict(env_file=(".env.lab", ".env"), extra="ignore")

    database_url: str = "postgresql://helpdesk:helpdesk@localhost:5433/helpdesk"

    # models
    ai_backend: str = "ollama"  # "ollama" | "fake"
    ollama_base_url: str = "https://ollama.lab.maheshmoholkar.in/v1"
    llm_model: str = "qwen3.5:4b"
    embed_model: str = "bge-m3"
    embed_dim: int = 1024
    llm_seed: int = 7
    answer_max_tokens: int = 800

    # retrieval
    retrieval_mode: str = "hybrid"  # "vector" | "hybrid"
    candidates_k: int = 30
    final_k: int = 5
    reranker: str = "none"  # "none" | "http" | "fake"
    rerank_url: str = ""
    abstain_threshold: float = 0.0
    as_of_date: date | None = None  # pretend "today" is this day (demos, evals); unset = real date

    # conversation
    history_turns: int = 6

    # CampusERP, the system this helpdesk extends (its FastAPI API, not the web app)
    erp_api_url: str = "http://localhost:8000"
    erp_session_cookie: str = "__Host-session"
    erp_csrf_cookie: str = "__Host-csrf"
    erp_csrf_header: str = "X-CSRF-Token"

    ingest_api_key: str = "change-me"
    cors_origins: list[str] = ["http://localhost:5173"]

    otel_exporter_otlp_endpoint: str = ""
