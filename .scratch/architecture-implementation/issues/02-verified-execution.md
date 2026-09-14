# Verified read-only execution

Status: OPEN
Blocked by: 01-safe-foundation.md

Restore PostgreSQL and DuckDB reads behind a dialect-aware SQL AST allowlist, bounded results/timeouts, trusted table mapping and Parquet path sandbox. Enforce read-only PostgreSQL transactions with a dedicated non-admin role; forbid external functions, ATTACH, COPY, extension loading and multiple statements. Keep the legacy unrestricted loop disabled.

Public seams: query adapters return typed ToolResult, distinguish 0 rows from errors and redact provider failures. Integration tests use disposable synthetic Parquet and a dedicated test database, never mutate production analytics. Add policy, filesystem escape, malformed result, result-size and connection cleanup cases before implementing each behavior.
