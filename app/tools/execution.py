"""Guarded read-only execution against PostgreSQL and DuckDB.

Defense in depth: SQL is validated by the AST guard *before* any connection is
opened, queries run in a read-only transaction with a statement timeout, results
are row-bounded, and every error summary is redacted.

Connection factories are injectable so the behavior is testable without a real
database. Analytics resources are never written.
"""

from pathlib import Path
from typing import Callable

from app.config import Settings
from app.tools.results import ToolResult, redact_secrets
from app.validation.sql_guard import guard_read_only_sql


def _wrap_with_limit(sql: str, has_limit: bool, max_rows: int) -> tuple[str, tuple[str, ...]]:
    if has_limit:
        return sql, ()
    return f"SELECT * FROM ({sql}) AS guarded_query LIMIT {max_rows + 1}", ("LIMIT_ENFORCED",)


class PostgresReadOnlyExecutor:
    def __init__(self, config: Settings, *, allowed_tables: tuple[str, ...],
                 connect_fn: Callable[..., object] | None = None, max_rows: int = 1000,
                 statement_timeout_ms: int = 10_000,
                 extra_secrets: tuple[str, ...] = ()) -> None:
        if max_rows < 1:
            raise ValueError("max_rows must be positive")
        self._config = config
        self._allowed_tables = allowed_tables
        self._max_rows = max_rows
        self._statement_timeout_ms = statement_timeout_ms
        self._secrets = tuple(extra_secrets) + ((config.postgres_password,) if config.postgres_password else ())
        if connect_fn is None:
            import psycopg2
            connect_fn = psycopg2.connect
        self._connect_fn = connect_fn

    def _redact(self, error: BaseException) -> str:
        return redact_secrets(f"{type(error).__name__}: {error}", self._secrets)

    def _connect(self):
        return self._connect_fn(
            host=self._config.postgres_host, port=self._config.postgres_port,
            dbname=self._config.postgres_db, user=self._config.postgres_user,
            password=self._config.postgres_password, connect_timeout=5)

    def execute(self, sql: str) -> ToolResult:
        return self.execute_with_rows(sql)[0]

    def execute_with_rows(self, sql: str) -> tuple[ToolResult, tuple]:
        if self._config.postgres_user != "baseball_readonly":
            return ToolResult.policy("Analytics runtime requires the baseball_readonly role."), ()
        guard = guard_read_only_sql(sql, dialect="postgres", allowed_tables=self._allowed_tables)
        if not guard.allowed:
            return ToolResult.policy(guard.reason), ()
        bounded_sql, metadata = _wrap_with_limit(sql, guard.has_limit, self._max_rows)
        try:
            connection = self._connect()
        except Exception as error:  # noqa: BLE001 - connection phase is retryable
            return ToolResult.failure(self._redact(error), retryable=True,
                                      error_code=type(error).__name__), ()
        try:
            with connection:
                cursor = connection.cursor()
                try:
                    cursor.execute("SET TRANSACTION READ ONLY")
                    cursor.execute(f"SET LOCAL statement_timeout = {int(self._statement_timeout_ms)}")
                    cursor.execute(bounded_sql)
                    rows = tuple(cursor.fetchmany(self._max_rows))
                finally:
                    cursor.close()
        except Exception as error:  # noqa: BLE001 - query phase is not auto-retried
            return ToolResult.failure(self._redact(error), retryable=False,
                                      error_code=type(error).__name__), ()
        finally:
            try:
                connection.close()
            except Exception:  # noqa: BLE001 - cleanup must not mask the result
                pass
        return self._success(rows, metadata)

    def _success(self, rows: tuple, metadata: tuple[str, ...]) -> tuple[ToolResult, tuple]:
        if not rows:
            return ToolResult.no_data(), ()
        metadata = metadata + (("TRUNCATED",) if len(rows) >= self._max_rows else ())
        return ToolResult.ok(len(rows), metadata), rows


class DuckDBReadOnlyExecutor:
    def __init__(self, file_root: str | Path, *, connect_fn: Callable[..., object] | None = None,
                 max_rows: int = 1000, extra_secrets: tuple[str, ...] = ()) -> None:
        if max_rows < 1:
            raise ValueError("max_rows must be positive")
        self._file_root = Path(file_root)
        self._max_rows = max_rows
        self._secrets = tuple(extra_secrets)
        if connect_fn is None:
            import duckdb
            connect_fn = duckdb.connect
        self._connect_fn = connect_fn

    def _redact(self, error: BaseException) -> str:
        return redact_secrets(f"{type(error).__name__}: {error}", self._secrets)

    def _connect(self):
        return self._connect_fn(database=":memory:", config={
            "autoinstall_known_extensions": False,
            "autoload_known_extensions": False,
        })

    def execute(self, sql: str) -> ToolResult:
        return self.execute_with_rows(sql)[0]

    def execute_with_rows(self, sql: str) -> tuple[ToolResult, tuple]:
        guard = guard_read_only_sql(sql, dialect="duckdb", file_root=self._file_root)
        if not guard.allowed:
            return ToolResult.policy(guard.reason), ()
        bounded_sql, metadata = _wrap_with_limit(sql, guard.has_limit, self._max_rows)
        try:
            connection = self._connect()
        except Exception as error:  # noqa: BLE001 - connection phase is retryable
            return ToolResult.failure(self._redact(error), retryable=True,
                                      error_code=type(error).__name__), ()
        try:
            rows = tuple(connection.execute(bounded_sql).fetchmany(self._max_rows))
        except Exception as error:  # noqa: BLE001 - query phase is not auto-retried
            return ToolResult.failure(self._redact(error), retryable=False,
                                      error_code=type(error).__name__), ()
        finally:
            try:
                connection.close()
            except Exception:  # noqa: BLE001 - cleanup must not mask the result
                pass
        return self._success(rows, metadata)

    def _success(self, rows: tuple, metadata: tuple[str, ...]) -> tuple[ToolResult, tuple]:
        if not rows:
            return ToolResult.no_data(), ()
        metadata = metadata + (("TRUNCATED",) if len(rows) >= self._max_rows else ())
        return ToolResult.ok(len(rows), metadata), rows
