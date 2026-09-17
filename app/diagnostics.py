"""Non-destructive preflight checks.

``run_doctor`` verifies that the environment, the read-only analytics sources and the
semantic model provider are usable. It never prints or persists a credential, performs no
writes to analytics data and returns a concise PASS/WARN/FAIL report.

This is the one command a returning user can run before asking a real question.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from app.config import Settings, settings

CheckStatus = Literal["PASS", "WARN", "FAIL"]


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: CheckStatus
    detail: str


@dataclass(frozen=True)
class DoctorReport:
    checks: tuple[CheckResult, ...]

    @property
    def failed(self) -> bool:
        return any(item.status == "FAIL" for item in self.checks)

    @property
    def warned(self) -> bool:
        return any(item.status == "WARN" for item in self.checks)

    def render(self) -> str:
        lines = []
        for item in self.checks:
            lines.append(f"[{item.status}] {item.name}: {item.detail}")
        summary = "FAIL" if self.failed else ("WARN" if self.warned else "PASS")
        lines.append(f"RESULT: {summary}")
        return "\n".join(lines)


def _safe(detail: str, secrets: tuple[str, ...]) -> str:
    from app.tools.results import redact_secrets
    return redact_secrets(detail, tuple(secret for secret in secrets if secret))


def _check_config(config: Settings) -> CheckResult:
    models = f"extractor={config.semantic_extractor_model} reviewer={config.semantic_reviewer_model}"
    if config.deepseek_api_key:
        return CheckResult("config.llm", "PASS", f"credential present; {models}")
    return CheckResult("config.llm", "WARN",
                       f"no LLM credential; deterministic semantic path only; {models}")


def _check_postgres(config: Settings) -> CheckResult:
    secrets = (config.postgres_password,) if config.postgres_password else ()
    if config.postgres_user != "baseball_readonly":
        return CheckResult("postgres.readonly", "FAIL",
                           f"runtime user must be baseball_readonly, got {config.postgres_user!r}")
    if not config.postgres_password:
        return CheckResult("postgres.readonly", "WARN",
                           "POSTGRES_PASSWORD is not set; PostgreSQL analytics is unavailable")
    try:
        import psycopg2
        connection = psycopg2.connect(
            host=config.postgres_host, port=config.postgres_port, dbname=config.postgres_db,
            user=config.postgres_user, password=config.postgres_password, connect_timeout=5)
    except Exception as error:  # noqa: BLE001 - report, never crash
        return CheckResult("postgres.readonly", "FAIL",
                           _safe(f"connection failed: {type(error).__name__}: {error}", secrets))
    try:
        cursor = connection.cursor()
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute("SELECT current_user, current_setting('transaction_read_only')")
        user, read_only = cursor.fetchone()
        cursor.execute("SELECT count(*) FROM statcast_pitches")
        rows = cursor.fetchone()[0]
        cursor.close()
    except Exception as error:  # noqa: BLE001
        return CheckResult("postgres.readonly", "FAIL",
                           _safe(f"query failed: {type(error).__name__}: {error}", secrets))
    finally:
        try:
            connection.close()
        except Exception:  # noqa: BLE001
            pass
    status: CheckStatus = "PASS" if read_only == "on" else "FAIL"
    return CheckResult("postgres.readonly", status,
                       f"host={config.postgres_host}:{config.postgres_port} db={config.postgres_db} "
                       f"user={user} read_only={read_only} statcast_rows={rows}")


def _check_parquet(config: Settings, sample: Callable[[Path], int] | None = None) -> CheckResult:
    root = Path(config.parquet_archive_path)
    files = sorted(root.glob("*.parquet"))
    if not files:
        return CheckResult("parquet.archive", "FAIL", f"no parquet files under {root}")
    if sample is None:
        def sample(path: Path) -> int:
            import duckdb
            safe = str(path).replace("'", "''")
            connection = duckdb.connect(database=":memory:")
            try:
                return int(connection.execute(
                    f"SELECT count(*) FROM read_parquet('{safe}')").fetchone()[0])
            finally:
                connection.close()
    newest = files[-1]
    try:
        rows = sample(newest)
    except Exception as error:  # noqa: BLE001
        return CheckResult("parquet.archive", "FAIL",
                           f"{newest.name} unreadable: {type(error).__name__}")
    return CheckResult("parquet.archive", "PASS",
                       f"{len(files)} files; {newest.name} rows={rows}")


def _check_knowledge(config: Settings) -> CheckResult:
    try:
        from app.knowledge.store import SqliteKnowledgeStore
        path = Path(config.knowledge_store_path)
        store = SqliteKnowledgeStore(path)
        try:
            count = store.item_count()
        finally:
            store.close()
    except Exception as error:  # noqa: BLE001
        return CheckResult("knowledge.store", "FAIL", f"{type(error).__name__}: {error}")
    if count == 0:
        return CheckResult("knowledge.store", "WARN",
                           f"{path} is empty; it will be seeded on first use")
    return CheckResult("knowledge.store", "PASS", f"{count} items at {path}")


def _check_llm(config: Settings, provider) -> CheckResult:
    if provider is None:
        return CheckResult("llm.reachability", "WARN",
                           "no provider configured; skipped live model probe")
    models = sorted({config.semantic_extractor_model, config.semantic_reviewer_model})
    reached = []
    for model in models:
        try:
            response = provider.complete("Reply with the single word OK.", model=model,
                                         timeout=min(config.llm_request_timeout_seconds, 15.0))
            reached.append((model, bool(response.text.strip())))
        except Exception as error:  # noqa: BLE001 - report, never crash or leak
            return CheckResult("llm.reachability", "FAIL",
                               _safe(f"{model}: {type(error).__name__}", (config.deepseek_api_key or "",)))
    return CheckResult("llm.reachability", "PASS",
                       "reachable models: " + ", ".join(model for model, ok in reached if ok))


def run_doctor(config: Settings | None = None, *, provider=None,
               parquet_sample: Callable[[Path], int] | None = None,
               check_llm: bool = True) -> DoctorReport:
    config = config or settings
    if provider is None and check_llm and config.deepseek_api_key:
        from app.llm.openai_provider import OpenAICompatibleProvider
        provider = OpenAICompatibleProvider(config)
    checks = [
        _check_config(config),
        _check_postgres(config),
        _check_parquet(config, parquet_sample),
        _check_knowledge(config),
    ]
    checks.append(_check_llm(config, provider) if check_llm
                  else CheckResult("llm.reachability", "WARN", "skipped by request"))
    return DoctorReport(checks=tuple(checks))
