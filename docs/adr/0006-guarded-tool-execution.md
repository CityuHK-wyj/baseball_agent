# Guarded tool execution with a stable, redacted result contract

Status: accepted

## Context

ADR 0004 added the read-only SQL AST guard but left it unwired. The development
brief requires real read-only PostgreSQL/DuckDB execution, validation strictly
*before* any connection, bounded results, retryable technical errors, redacted
error summaries, and removal of every unguarded analytical path. It also asks the
execution layer to distinguish `TECHNICAL_FAILURE`, `NO_DATA`, `POLICY_REJECTED`
and `SUCCESS` instead of raising raw exceptions into the agent.

## Decision

- Tool results are a single contract, `app/tools/results.py:ToolResult`, with
  `status`, `error_type`, `retryable`, `policy_blocked`, `safe_error_summary`,
  `row_count` and `execution_metadata`. `app/agent/executor.py` re-exports it.
- `redact_secrets` replaces known values, credential URLs and `password=`-style
  key/value pairs with `SECRET_REDACTED`. Connection and query errors are only ever
  surfaced through it.
- `app/tools/execution.py` provides `PostgresReadOnlyExecutor` and
  `DuckDBReadOnlyExecutor`. Both: (1) run `guard_read_only_sql` first and return
  `POLICY_REJECTED` without connecting on failure; (2) wrap LIMIT-less reads in a
  bounded subquery and `fetchmany(max_rows)`; (3) run PostgreSQL inside
  `SET TRANSACTION READ ONLY` with `SET LOCAL statement_timeout`; (4) connect with a
  short timeout; (5) treat connection-phase errors as retryable and query-phase
  errors as non-retryable.
- Connection factories are injectable, so no test touches a real database.
- `guard_read_only_sql` now keeps schema/database qualification in table identity,
  so an unqualified allowlist entry cannot be satisfied by an untrusted schema.
- The legacy JSON adapters (`query_local_hot_db`, `query_local_cold_parquet`) route
  through the executors and keep returning `BLOCKED_BY_POLICY` on rejection.
- `main.py` was rewritten: import has no side effects and its read goes through the
  guarded DuckDB executor. `generate_pitch_heatmap` fails closed. No unguarded
  analytical execution path remains.

## Alternatives

- Building the connection string from a `BASEBALL_DATABASE_URL` only: the existing
  `Settings` fields already read the same credential from the environment; a DSN is a
  possible later refinement, not a reason to leave execution unwired.
- Relying on a read-only database role alone: necessary but insufficient (DuckDB
  filesystem access, ATTACH, extension loading are not covered).
- Letting executors raise: rejected; it leaks connection details and forces the agent
  to interpret provider exceptions.

## Consequences

- Ticket 02 is implemented at the code level. No live PostgreSQL/DuckDB integration
  test has run: the environment has no analytical database or Parquet archive.
- `TaskAttempt` now carries `error_type` and `safe_error_summary`.
- The LIMIT wrapper changes the executed SQL for unbounded reads; the guard validates
  the original statement and the wrapper is generated, not model-authored.
