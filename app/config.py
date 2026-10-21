"""Runtime configuration.

Reads environment variables only. Secrets are read from the environment and are never
persisted, logged or written into artifacts/traces.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _load_dotenv() -> None:
    """Load a local .env if present, without overwriting existing environment."""
    path = PROJECT_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


@dataclass(frozen=True)
class LLMConfig:
    api_key: str = field(default_factory=lambda: _env("DEEPSEEK_API_KEY") or _env("OPENAI_API_KEY"))
    base_url: str = field(
        default_factory=lambda: _env("DEEPSEEK_BASE_URL") or _env("OPENAI_BASE_URL") or "https://api.deepseek.com"
    )
    runtime_model: str = field(default_factory=lambda: _env("RUNTIME_MODEL") or _env("DEEPSEEK_MODEL") or "deepseek-flash")
    semantic_model: str = field(default_factory=lambda: _env("SEMANTIC_MODEL") or _env("RUNTIME_MODEL") or _env("DEEPSEEK_MODEL") or "deepseek-flash")
    planner_model: str = field(default_factory=lambda: _env("PLANNER_MODEL") or _env("RUNTIME_MODEL") or _env("DEEPSEEK_MODEL") or "deepseek-flash")
    response_model: str = field(default_factory=lambda: _env("RESPONSE_MODEL") or _env("RUNTIME_MODEL") or _env("DEEPSEEK_MODEL") or "deepseek-flash")
    analytics_model: str = field(default_factory=lambda: _env("ANALYTICS_MODEL") or _env("RUNTIME_MODEL") or _env("DEEPSEEK_MODEL") or "deepseek-flash")
    timeout_seconds: float = field(default_factory=lambda: float(_env("LLM_TIMEOUT_SECONDS", "60") or 60))
    reasoning_effort: str = field(default_factory=lambda: _env("LLM_REASONING_EFFORT", "none"))
    # Application code owns retry policy. Provider SDK hidden retries multiply the
    # application's logical attempts into unbudgeted HTTP attempts; the default of 0
    # keeps transport attempts equal to application attempts. Set explicitly only when
    # provider retries are genuinely wanted, and they are recorded as such.
    provider_max_retries: int = field(default_factory=lambda: max(0, int(_env("LLM_PROVIDER_MAX_RETRIES", "0") or 0)))

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


@dataclass(frozen=True)
class PostgresConfig:
    host: str = field(default_factory=lambda: _env("POSTGRES_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("POSTGRES_PORT", "5433") or 5433))
    db: str = field(default_factory=lambda: _env("POSTGRES_DB", "baseball_analytics"))
    user: str = field(default_factory=lambda: _env("POSTGRES_USER", "baseball_readonly"))
    password: str = field(default_factory=lambda: _env("POSTGRES_PASSWORD"))
    connect_timeout: int = 6


@dataclass(frozen=True)
class ParquetConfig:
    archive_path: str = field(
        default_factory=lambda: _env(
            "PARQUET_ARCHIVE_PATH",
            "",
        )
    )


@dataclass(frozen=True)
class WebConfig:
    search_endpoint: str = field(default_factory=lambda: _env("WEB_SEARCH_ENDPOINT"))
    search_api_key: str = field(default_factory=lambda: _env("WEB_SEARCH_API_KEY"))
    tavily_api_key: str = field(default_factory=lambda: _env("TAVILY_API_KEY"), repr=False)
    max_fetches: int = field(default_factory=lambda: int(_env("WEB_MAX_FETCHES", "4") or 4))
    fetch_timeout: float = field(default_factory=lambda: float(_env("WEB_FETCH_TIMEOUT", "15") or 15))
    user_agent: str = field(
        default_factory=lambda: _env(
            "WEB_USER_AGENT",
            "BaseballAgent/0.1 (research; mailto:baseball-agent@example.com)",
        )
    )
    feeds: list[str] = field(
        default_factory=lambda: [
            f.strip()
            for f in _env(
                "WEB_FEEDS",
                "https://www.mlb.com/feeds/news/rss.xml,https://blogs.fangraphs.com/feed/,https://www.cbssports.com/rss/headlines/mlb/,https://sports.yahoo.com/mlb/rss/",
            ).split(",")
            if f.strip()
        ]
    )
    # Broad-web discovery escalation. Real provider; used only when direct sources are thin.
    broad_search: bool = field(default_factory=lambda: _env("WEB_BROAD_SEARCH", "1") != "0")
    # Optional public-page renderer. Requires an installed Playwright Chromium runtime.
    browser_render: bool = field(default_factory=lambda: _env("WEB_BROWSER_RENDER", "0") == "1")


@dataclass(frozen=True)
class RuntimeConfig:
    runtime_dir: Path = field(default_factory=lambda: Path(_env("RUNTIME_DIR", str(PROJECT_ROOT / ".runtime"))))
    llm: LLMConfig = field(default_factory=LLMConfig)
    postgres: PostgresConfig = field(default_factory=PostgresConfig)
    parquet: ParquetConfig = field(default_factory=ParquetConfig)
    web: WebConfig = field(default_factory=WebConfig)
    max_rounds: int = field(default_factory=lambda: int(_env("MAX_ROUNDS", "4") or 4))
    max_needs: int = field(default_factory=lambda: int(_env("MAX_NEEDS", "8") or 8))

    def ensure_dirs(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)


_config: RuntimeConfig | None = None


def get_config() -> RuntimeConfig:
    global _config
    if _config is None:
        _config = RuntimeConfig()
    return _config
