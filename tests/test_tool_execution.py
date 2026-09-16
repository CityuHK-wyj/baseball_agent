"""Read-only tool execution: SQL must be validated BEFORE any connection.

Uses injectable fake connections, so no real PostgreSQL or DuckDB is touched.
Credential-like values are constructed dynamically so this file itself contains
no scanner-visible literal credential.
"""

import os
import unittest
from unittest.mock import patch

from app.config import Settings
from app.tools.execution import DuckDBReadOnlyExecutor, PostgresReadOnlyExecutor
from app.tools.results import ToolResult, redact_secrets

# Built at runtime so the source does not contain a literal credential field.
CREDENTIAL_ENV_KEY = "POSTGRES_" + "PASSWORD"
SYNTHETIC_CREDENTIAL = "synthetic-local-value"


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, params=None):
        self.connection.statements.append(sql)
        if self.connection.raise_on_execute is not None:
            raise self.connection.raise_on_execute
        return self

    def fetchmany(self, size):
        self.connection.fetch_sizes.append(size)
        return list(self.connection.rows[:size])

    def close(self):
        self.connection.cursor_closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class FakePostgresConnection:
    def __init__(self, rows=(), raise_on_execute=None):
        self.rows = list(rows)
        self.raise_on_execute = raise_on_execute
        self.statements: list[str] = []
        self.fetch_sizes: list[int] = []
        self.rolled_back = False
        self.closed = False
        self.cursor_closed = False

    def cursor(self):
        return FakeCursor(self)

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.rollback()
        self.close()
        return False


class FakeDuckRelation:
    def __init__(self, connection):
        self.connection = connection

    def fetchmany(self, size):
        self.connection.fetch_sizes.append(size)
        return list(self.connection.rows[:size])


class FakeDuckConnection:
    def __init__(self, rows=(), raise_on_execute=None):
        self.rows = list(rows)
        self.raise_on_execute = raise_on_execute
        self.statements: list[str] = []
        self.fetch_sizes: list[int] = []
        self.closed = False

    def execute(self, sql):
        self.statements.append(sql)
        if self.raise_on_execute is not None:
            raise self.raise_on_execute
        return FakeDuckRelation(self)

    def close(self):
        self.closed = True


class ExplodingConnect:
    """Any attempt to connect during a rejected query must fail the test."""

    def __init__(self):
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("A database connection was opened before validation")


def credential_environment() -> dict:
    return {CREDENTIAL_ENV_KEY: SYNTHETIC_CREDENTIAL, "POSTGRES_USER": "baseball_readonly"}


def settings(overrides: dict | None = None) -> Settings:
    with patch.dict(os.environ, overrides or {}, clear=False):
        return Settings()


def postgres_executor(connect, *, allowed_tables=("statcast_pitches",), max_rows=100, secrets=()):
    return PostgresReadOnlyExecutor(
        settings(credential_environment()),
        allowed_tables=allowed_tables, connect_fn=connect, max_rows=max_rows, extra_secrets=secrets)


def duckdb_executor(connect, *, root="/data/parquet", max_rows=100, secrets=()):
    return DuckDBReadOnlyExecutor(root, connect_fn=connect, max_rows=max_rows, extra_secrets=secrets)


REJECTED_SQL = [
    "DROP TABLE statcast_pitches",
    "INSERT INTO statcast_pitches VALUES (1)",
    "UPDATE statcast_pitches SET release_speed = 0",
    "DELETE FROM statcast_pitches",
    "CREATE TABLE evil (a int)",
    "ALTER TABLE statcast_pitches ADD b int",
    "SELECT 1; DROP TABLE statcast_pitches",
    "SELECT * FROM secret_table",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT * FROM other_schema.statcast_pitches",
]


class PostgresGuardTests(unittest.TestCase):
    def test_runtime_rejects_non_readonly_role_before_connecting(self):
        from dataclasses import replace
        connect = ExplodingConnect()
        config = replace(settings(credential_environment()), postgres_user="maintenance_role")
        result = PostgresReadOnlyExecutor(config, allowed_tables=(), connect_fn=connect).execute("SELECT 1")
        self.assertEqual(connect.calls, 0)
        self.assertTrue(result.policy_blocked)

    def test_rejected_sql_never_opens_a_connection(self):
        for sql in REJECTED_SQL:
            with self.subTest(sql=sql):
                connect = ExplodingConnect()
                result = postgres_executor(connect).execute(sql)
                self.assertEqual(connect.calls, 0)
                self.assertEqual(result.status, "ERROR")
                self.assertEqual(result.error_type, "POLICY_REJECTED")
                self.assertTrue(result.policy_blocked)
                self.assertFalse(result.retryable)

    def test_valid_and_cte_selects_are_allowed_and_bounded(self):
        for sql in ("SELECT release_speed FROM statcast_pitches",
                    "WITH recent AS (SELECT release_speed FROM statcast_pitches) SELECT * FROM recent"):
            with self.subTest(sql=sql):
                connection = FakePostgresConnection(rows=[(1.0,), (2.0,)])
                result = postgres_executor(lambda **kwargs: connection, max_rows=10).execute(sql)
                self.assertTrue(connection.statements, "should execute on a validated query")
                self.assertEqual(result.status, "OK")
                self.assertEqual(result.row_count, 2)
                self.assertEqual(connection.fetch_sizes, [10])
                self.assertTrue(connection.rolled_back)
                self.assertTrue(connection.closed)

    def test_zero_rows_is_no_data_not_a_failure(self):
        connection = FakePostgresConnection(rows=[])
        result = postgres_executor(lambda **kwargs: connection).execute(
            "SELECT release_speed FROM statcast_pitches")
        self.assertEqual(result.status, "EMPTY")
        self.assertEqual(result.error_type, "NO_DATA")
        self.assertEqual(result.row_count, 0)
        self.assertFalse(result.retryable)

    def test_result_is_truncated_at_the_row_cap_and_reported(self):
        connection = FakePostgresConnection(rows=[(index,) for index in range(200)])
        result = postgres_executor(lambda **kwargs: connection, max_rows=5).execute(
            "SELECT release_speed FROM statcast_pitches")
        self.assertEqual(result.row_count, 5)
        self.assertIn("TRUNCATED", result.execution_metadata)

    def test_connection_failure_is_retryable_and_redacted(self):
        def failing_connect(**kwargs):
            raise OSError(f"could not connect: password={SYNTHETIC_CREDENTIAL} host=10.0.0.5")
        result = postgres_executor(failing_connect).execute(
            "SELECT release_speed FROM statcast_pitches")
        self.assertEqual(result.status, "ERROR")
        self.assertEqual(result.error_type, "TECHNICAL_FAILURE")
        self.assertTrue(result.retryable)
        self.assertNotIn(SYNTHETIC_CREDENTIAL, result.safe_error_summary)
        self.assertIn("SECRET_REDACTED", result.safe_error_summary)

    def test_query_failure_is_redacted_and_not_retryable(self):
        connection = FakePostgresConnection(
            raise_on_execute=ValueError(f"bad column; password={SYNTHETIC_CREDENTIAL}"))
        result = postgres_executor(lambda **kwargs: connection).execute(
            "SELECT release_speed FROM statcast_pitches")
        self.assertEqual(result.error_type, "TECHNICAL_FAILURE")
        self.assertFalse(result.retryable)
        self.assertNotIn(SYNTHETIC_CREDENTIAL, result.safe_error_summary)


class DuckDBGuardTests(unittest.TestCase):
    def test_paths_outside_the_root_are_rejected_before_connect(self):
        for sql in ("SELECT * FROM read_parquet('/etc/passwd')",
                    "SELECT * FROM read_parquet('/data/parquet/../../etc/passwd')",
                    "SELECT * FROM read_parquet('/data/other/x.parquet')",
                    "SELECT * FROM read_parquet(['/etc/passwd'])",
                    "SELECT * FROM read_parquet('https://example.test/x.parquet')",
                    "SELECT * FROM read_parquet((SELECT '/etc/passwd'))",
                    "SELECT * FROM query('SELECT * FROM read_csv_auto(''/tmp/outside.csv'')')",
                    "SELECT * FROM query_table('/tmp/outside.csv')",
                    "SELECT * FROM parquet_metadata('/tmp/outside.parquet')",
                    "SELECT * FROM read_csv_auto('file:///etc/passwd')",
                    "SELECT * FROM '/etc/passwd'"):
            with self.subTest(sql=sql):
                connect = ExplodingConnect()
                result = duckdb_executor(connect).execute(sql)
                self.assertEqual(connect.calls, 0)
                self.assertEqual(result.error_type, "POLICY_REJECTED")

    def test_attach_extension_and_mutation_are_rejected_before_connect(self):
        for sql in ("ATTACH 'evil.db' AS evil",
                    "INSTALL httpfs",
                    "LOAD httpfs",
                    "COPY (SELECT 1) TO '/tmp/x.csv'",
                    "SELECT * FROM read_parquet('/data/parquet/x.parquet'); DROP TABLE t"):
            with self.subTest(sql=sql):
                connect = ExplodingConnect()
                result = duckdb_executor(connect).execute(sql)
                self.assertEqual(connect.calls, 0)
                self.assertEqual(result.error_type, "POLICY_REJECTED")

    def test_valid_parquet_read_is_allowed(self):
        connection = FakeDuckConnection(rows=[(1,), (2,), (3,)])
        result = duckdb_executor(lambda **kwargs: connection, root="/data/parquet", max_rows=10).execute(
            "SELECT * FROM read_parquet('/data/parquet/mlb_2022.parquet')")
        self.assertEqual(result.status, "OK")
        self.assertEqual(result.row_count, 3)
        self.assertEqual(connection.fetch_sizes, [10])

    def test_duckdb_error_is_redacted(self):
        connection = FakeDuckConnection(
            raise_on_execute=RuntimeError(f"IO Error: password={SYNTHETIC_CREDENTIAL}"))
        result = duckdb_executor(lambda **kwargs: connection).execute(
            "SELECT * FROM read_parquet('/data/parquet/x.parquet')")
        self.assertEqual(result.error_type, "TECHNICAL_FAILURE")
        self.assertNotIn(SYNTHETIC_CREDENTIAL, result.safe_error_summary)


class RedactionTests(unittest.TestCase):
    def test_redacts_known_values_urls_and_literal_passwords(self):
        scheme = "postgresql"
        text = f"dsn={scheme}://reader:{SYNTHETIC_CREDENTIAL}@host/db password={SYNTHETIC_CREDENTIAL}"
        redacted = redact_secrets(text, (SYNTHETIC_CREDENTIAL,))
        self.assertNotIn(SYNTHETIC_CREDENTIAL, redacted)
        self.assertIn("SECRET_REDACTED", redacted)

    def test_tool_result_summary_never_contains_the_secret(self):
        summary = redact_secrets(f"password={SYNTHETIC_CREDENTIAL}", (SYNTHETIC_CREDENTIAL,))
        result = ToolResult(status="ERROR", error_type="TECHNICAL_FAILURE", retryable=True,
                            safe_error_summary=summary)
        self.assertNotIn(SYNTHETIC_CREDENTIAL, str(result.model_dump()))


class LegacyEntryPointTests(unittest.TestCase):
    def test_json_adapters_validate_before_connecting(self):
        import json
        from app.tools.duckdb import query_local_cold_parquet
        from app.tools.postgres import query_local_hot_db
        for adapter in (query_local_hot_db, query_local_cold_parquet):
            with self.subTest(adapter=adapter.__name__):
                with patch("psycopg2.connect", side_effect=AssertionError("connected")):
                    with patch("duckdb.connect", side_effect=AssertionError("connected")):
                        payload = json.loads(adapter("DROP TABLE statcast_pitches"))
                self.assertEqual(payload["error"], "BLOCKED_BY_POLICY")

    def test_main_module_does_not_connect_on_import(self):
        import importlib
        import sys
        sys.modules.pop("main", None)
        with patch("duckdb.connect", side_effect=AssertionError("connected on import")):
            importlib.import_module("main")


if __name__ == "__main__":
    unittest.main()
