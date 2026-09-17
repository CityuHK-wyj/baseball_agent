# Verified read-only execution

Status: DONE (live DB integration unverified)
Blocked by: None

Restore PostgreSQL and DuckDB reads behind a dialect-aware SQL AST allowlist, bounded results/timeouts, trusted table mapping and Parquet path sandbox. Enforce read-only PostgreSQL transactions with a dedicated non-admin role; forbid external functions, ATTACH, COPY, extension loading and multiple statements. Keep the legacy unrestricted loop disabled.

Public seams: query adapters return typed ToolResult, distinguish 0 rows from errors and redact provider failures. Integration tests use disposable synthetic Parquet and a dedicated test database, never mutate production analytics. Add policy, filesystem escape, malformed result, result-size and connection cleanup cases before implementing each behavior.

## Comments

- Delivered ADR 0004 + ADR 0006: `app/validation/sql_guard.py`, `app/tools/results.py`,
  `app/tools/execution.py`; adapters `app/tools/{postgres,duckdb}.py` rewired; `main.py`
  unguarded DuckDB read removed; `generate_pitch_heatmap` fails closed.
- Tests: `tests/test_sql_guard.py`, `tests/test_tool_execution.py` cover rejected SQL
  before connect, valid SELECT/CTE, multi-statement, mutation/DDL/admin, forbidden
  functions, schema-qualified table escape, DuckDB path escape, ATTACH/INSTALL/COPY,
  LIMIT/row bounding, retryable connection failure, non-retryable query failure and
  redaction of secrets in error summaries.
- Remaining (not blocking): run the same suite against a disposable real PostgreSQL
  and a synthetic Parquet fixture; confirm the deployed role is non-admin read-only.
