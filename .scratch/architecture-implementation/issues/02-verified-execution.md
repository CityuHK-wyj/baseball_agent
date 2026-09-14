# Verified read-only execution

Status: PARTIAL
Blocked by: None (sqlglot is now installable)

Restore PostgreSQL and DuckDB reads behind a dialect-aware SQL AST allowlist, bounded results/timeouts, trusted table mapping and Parquet path sandbox. Enforce read-only PostgreSQL transactions with a dedicated non-admin role; forbid external functions, ATTACH, COPY, extension loading and multiple statements. Keep the legacy unrestricted loop disabled.

Public seams: query adapters return typed ToolResult, distinguish 0 rows from errors and redact provider failures. Integration tests use disposable synthetic Parquet and a dedicated test database, never mutate production analytics. Add policy, filesystem escape, malformed result, result-size and connection cleanup cases before implementing each behavior.

## Comments

- Delivered: `app/validation/sql_guard.py` (ADR 0004) with `guard_read_only_sql` and
  `resolve_within_root`, plus `tests/test_sql_guard.py` covering mutation, DDL,
  administration, forbidden functions, table allowlists, file escape and globs.
- `sqlglot>=30.0.0` added to `requirements.txt`; the environment now installs it.
- Remaining: wire the guard to a live non-admin read-only PostgreSQL role and a DuckDB
  connection, bound results and timeouts, redact provider errors, and test against
  disposable synthetic fixtures. Adapters stay fail-closed until then.
