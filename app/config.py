"""Environment-backed configuration; importing does not connect to services."""

from dataclasses import dataclass, field
from pathlib import Path
import os

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    deepseek_api_key: str | None = field(default_factory=lambda: os.getenv("DEEPSEEK_API_KEY"), repr=False)
    deepseek_base_url: str = field(default_factory=lambda: os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
    deepseek_model: str = field(default_factory=lambda: os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro"))
    # Per-agent model selection; provider-agnostic and configuration-driven.
    llm_planner_model: str = field(default_factory=lambda: os.getenv("PLANNER_MODEL") or os.getenv("DEEPSEEK_MODEL") or "deepseek-v4-pro")
    llm_judge_model: str = field(default_factory=lambda: os.getenv("JUDGE_MODEL") or os.getenv("DEEPSEEK_MODEL") or "deepseek-v4-pro")
    llm_response_model: str = field(default_factory=lambda: os.getenv("RESPONSE_MODEL") or os.getenv("DEEPSEEK_MODEL") or "deepseek-v4-pro")
    llm_semantic_model: str = field(default_factory=lambda: os.getenv("SEMANTIC_MODEL") or os.getenv("DEEPSEEK_MODEL") or "deepseek-v4-pro")
    # Dual semantic roles are independently configurable and provider-agnostic. Each
    # falls back to SEMANTIC_MODEL / DEEPSEEK_MODEL, then to a fast default model so the
    # two-call review path stays interactive. Separate calls/prompts keep the roles
    # independent even when one provider is used.
    semantic_extractor_model: str = field(default_factory=lambda: (
        os.getenv("SEMANTIC_EXTRACTOR_MODEL") or os.getenv("SEMANTIC_MODEL")
        or os.getenv("DEEPSEEK_MODEL") or os.getenv("SEMANTIC_DEFAULT_MODEL", "deepseek-chat")))
    semantic_reviewer_model: str = field(default_factory=lambda: (
        os.getenv("SEMANTIC_REVIEWER_MODEL") or os.getenv("SEMANTIC_MODEL")
        or os.getenv("DEEPSEEK_MODEL") or os.getenv("SEMANTIC_DEFAULT_MODEL", "deepseek-chat")))
    llm_request_timeout_seconds: float = field(default_factory=lambda: float(os.getenv("LLM_TIMEOUT_SECONDS", "30")))
    postgres_host: str = field(default_factory=lambda: os.getenv("POSTGRES_HOST", "127.0.0.1"))
    postgres_port: int = field(default_factory=lambda: int(os.getenv("POSTGRES_PORT", "5433")))
    postgres_db: str = field(default_factory=lambda: os.getenv("POSTGRES_DB", "baseball_analytics"))
    postgres_user: str = field(default_factory=lambda: os.getenv("POSTGRES_USER", "baseball_readonly"))
    postgres_password: str | None = field(default_factory=lambda: os.getenv("POSTGRES_PASSWORD"), repr=False)
    parquet_archive_path: Path = field(default_factory=lambda: Path(
        os.getenv("PARQUET_ARCHIVE_PATH", str(PROJECT_ROOT / "data_loader" / "parquet_archive"))
    ))
    artifact_storage_path: Path = field(default_factory=lambda: Path(
        os.getenv("ARTIFACT_STORAGE_PATH", str(PROJECT_ROOT / ".runtime" / "artifacts"))
    ))
    operational_store_path: Path = field(default_factory=lambda: Path(
        os.getenv("OPERATIONAL_STORE_PATH", str(PROJECT_ROOT / ".runtime" / "operational.db"))
    ))
    # Shared Knowledge: a separate logical store, never the analytics or runtime tables.
    knowledge_store_path: Path = field(default_factory=lambda: Path(
        os.getenv("KNOWLEDGE_STORE_PATH", str(PROJECT_ROOT / ".runtime" / "knowledge.db"))
    ))
    knowledge_seed_path: Path = field(default_factory=lambda: Path(
        os.getenv("KNOWLEDGE_SEED_PATH", str(PROJECT_ROOT / "knowledge" / "seed"))
    ))
    knowledge_source_path: Path = field(default_factory=lambda: Path(
        os.getenv("KNOWLEDGE_SOURCE_PATH", str(PROJECT_ROOT / "knowledge" / "sources"))
    ))
    # Optional live web recovery. When blank, web recovery is interface-only and the
    # runtime reports that it is not configured rather than pretending unknown -> web.
    web_search_endpoint: str | None = field(
        default_factory=lambda: os.getenv("WEB_SEARCH_ENDPOINT") or None)
    web_search_api_key: str | None = field(
        default_factory=lambda: os.getenv("WEB_SEARCH_API_KEY"), repr=False)


settings = Settings()


