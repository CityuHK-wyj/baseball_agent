"""Environment-backed configuration; importing does not connect to services."""

from dataclasses import dataclass, field
from pathlib import Path
import os

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# The single authoritative product-path model identifier. Every runtime cognition role
# resolves here unless a role-specific environment override is provided. The official
# DeepSeek API model id is ``deepseek-flash`` (a reasoning-capable Flash model). Legacy
# names such as ``deepseek-v4-flash`` are not used.
DEFAULT_RUNTIME_MODEL = "deepseek-flash"


def _runtime_model() -> str:
    """The one authoritative runtime model, honouring a single override chain."""
    return (os.getenv("RUNTIME_MODEL") or os.getenv("DEEPSEEK_MODEL")
            or DEFAULT_RUNTIME_MODEL)


def _role_model(env_name: str) -> str:
    """Role model = role override, else the shared runtime model."""
    return os.getenv(env_name) or _runtime_model()


@dataclass(frozen=True)
class Settings:
    deepseek_api_key: str | None = field(default_factory=lambda: os.getenv("DEEPSEEK_API_KEY"), repr=False)
    deepseek_base_url: str = field(default_factory=lambda: os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
    deepseek_model: str = field(default_factory=lambda: _runtime_model())
    # Authoritative product-path model shared by every runtime cognition role.
    runtime_model: str = field(default_factory=_runtime_model)
    # Per-role model selection; each falls back to the shared runtime model. Honouring a
    # role-specific env override keeps the configuration provider-agnostic.
    llm_planner_model: str = field(default_factory=lambda: _role_model("PLANNER_MODEL"))
    llm_judge_model: str = field(default_factory=lambda: _role_model("JUDGE_MODEL"))
    llm_response_model: str = field(default_factory=lambda: _role_model("RESPONSE_MODEL"))
    llm_semantic_model: str = field(default_factory=lambda: _role_model("SEMANTIC_MODEL"))
    # Legacy dual-semantic roles. They share the same provider and intended runtime
    # configuration, so they also fall back to the authoritative runtime model rather
    # than a separate hard-coded default.
    semantic_extractor_model: str = field(default_factory=lambda: (
        os.getenv("SEMANTIC_EXTRACTOR_MODEL") or os.getenv("SEMANTIC_MODEL")
        or _runtime_model()))
    semantic_reviewer_model: str = field(default_factory=lambda: (
        os.getenv("SEMANTIC_REVIEWER_MODEL") or os.getenv("SEMANTIC_MODEL")
        or _runtime_model()))
    llm_request_timeout_seconds: float = field(default_factory=lambda: float(os.getenv("LLM_TIMEOUT_SECONDS", "30")))
    # True end-to-end model-call deadline (request + provider wait + stream + retry).
    # Distinct from the per-operation SDK timeout: the provider enforces a wall clock.
    llm_deadline_seconds: float = field(default_factory=lambda: float(os.getenv("LLM_DEADLINE_SECONDS", "90")))
    # Stream completions so first-token latency is observable and a stalled stream can be
    # aborted against the wall-clock deadline.
    llm_stream: bool = field(default_factory=lambda: os.getenv("LLM_STREAM", "1").strip().lower() not in ("0", "false", "no"))
    # Reasoning effort for the runtime cognition roles. Structured roles default to no
    # hidden reasoning so bounded structured output is actually emitted within max_tokens;
    # a deployment may raise it (low/medium/high) at a latency cost.
    llm_reasoning_effort: str = field(default_factory=lambda: os.getenv("LLM_REASONING_EFFORT", "none"))
    # Role-specific output bounds. Reasoning tokens (when enabled) count against these.
    llm_semantic_max_tokens: int = field(default_factory=lambda: int(os.getenv("SEMANTIC_MAX_TOKENS", "1024")))
    llm_planner_max_tokens: int = field(default_factory=lambda: int(os.getenv("PLANNER_MAX_TOKENS", "2048")))
    llm_response_max_tokens: int = field(default_factory=lambda: int(os.getenv("RESPONSE_MAX_TOKENS", "1024")))
    llm_judge_max_tokens: int = field(default_factory=lambda: int(os.getenv("JUDGE_MAX_TOKENS", "512")))
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

    def runtime_llm_roles(self) -> dict:
        """The authoritative product-path role table (model, output bound, reasoning)."""
        return {
            "semantic": {"model": self.llm_semantic_model,
                         "max_tokens": self.llm_semantic_max_tokens},
            "planner": {"model": self.llm_planner_model,
                        "max_tokens": self.llm_planner_max_tokens},
            "response": {"model": self.llm_response_model,
                         "max_tokens": self.llm_response_max_tokens},
            "judge": {"model": self.llm_judge_model,
                      "max_tokens": self.llm_judge_max_tokens},
        }


settings = Settings()


