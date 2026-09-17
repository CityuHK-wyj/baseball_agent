# Read-only SQL enforced by AST guard, not string matching

Status: accepted

## Context

D047 and the user brief require PostgreSQL, DuckDB and Parquet to be strictly
read-only for the agent runtime, with no INSERT/UPDATE/DELETE/DDL, no filesystem
writes, no uncontrolled ATTACH, no extension loading and no arbitrary external
commands. The brief explicitly rejects string checking alone. The previous runtime
executed model-generated SQL without validation; those entry points were made to
fail closed in ADR 0001.

## Decision

- Add `app/validation/sql_guard.py`. `guard_read_only_sql(sql, dialect, allowed_tables,
  file_root, forbidden_functions)` parses with `sqlglot` at `ErrorLevel.RAISE` and:
  - rejects anything but exactly one read-only SELECT/set-operation/subquery,
  - rejects any `exp.DDL`/`exp.DML` node and a named denylist (DROP, ALTER,
    TRUNCATE, ATTACH, DETACH, INSTALL, PRAGMA, SET, GRANT, COPY, SELECT INTO,
    transaction control, and raw `Command` nodes),
  - rejects forbidden functions (arbitrary file read/write, dblink, pg_sleep,
    extension loading, shell/system/getenv),
  - enforces a table allowlist when supplied,
  - requires an explicit `file_root` for DuckDB file-reading functions and rejects
    any literal path (including glob prefixes and `..` traversal) that escapes it.
- The guard is a pure function returning a `GuardResult`; it does not connect to any
  database. The live adapters remain fail-closed.
- `sqlglot>=30` is added to `requirements.txt`.

## Alternatives

- Regex/keyword scanning: rejected by the brief; bypassable via comments, casing and
  equivalent syntax.
- Relying only on a read-only database role: necessary but insufficient; it does not
  stop DuckDB filesystem writes, ATTACH or extension loading.
- Shipping the guard plus live execution now: rejected. Wiring the guard to a real
  connection, bounded results/timeouts and a non-admin role needs disposable synthetic
  fixtures and its own tests (ticket `02-verified-execution.md`).

## Consequences

- Ticket 02 is unblocked (the missing dependency is now installable) but not complete:
  the executor, result bounding, timeouts and live read-only role are still pending.
- The guard is tested against mutation, administration, forbidden functions, table
  allowlists, path escape and glob-within-root cases.
- Denylists are policy surfaces and must be reviewed when new dialects or functions
  are introduced.
