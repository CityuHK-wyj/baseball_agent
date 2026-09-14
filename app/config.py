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


settings = Settings()


