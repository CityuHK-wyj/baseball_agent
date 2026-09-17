# Security

## Read-only analytics

PostgreSQL and DuckDB/Parquet are **read-only** for the agent runtime. Modification is a
system-administrator action and a user cannot authorise it in chat; such a request is
`BLOCKED_BY_POLICY`, not a clarification.

Enforcement is layered:

1. **AST guard** (`app/validation/sql_guard.py`) parses SQL with `sqlglot` and rejects
   anything but a single read-only `SELECT`/set-operation: DDL/DML, `COPY`, `ATTACH`,
   `INSTALL`/`LOAD`, `PRAGMA`, transaction control, forbidden functions, non-allowlisted
   tables (schema-qualified) and file paths outside an explicit root.
2. **Validation before connect** (`app/tools/execution.py`) — a rejected statement never
   opens a connection.
3. **Read-only transaction** — PostgreSQL runs inside `SET TRANSACTION READ ONLY` with a
   statement timeout; DuckDB is connected in-memory with extension auto-install/auto-load
   disabled.
4. **Bounded results** — LIMIT-less reads are wrapped and fetched with `fetchmany`.

## DuckDB filesystem sandbox

Every path literal passed to a file-reading function must resolve inside
`PARQUET_ARCHIVE_PATH`. `resolve_within_root` rejects `..`, absolute paths outside the
root, and glob prefixes that escape it.

## Secrets

- Credentials come **only** from environment variables.
- `.env` is git-ignored; `.env.example` contains names only.
- `scripts/secret_scan.py` is a credential tripwire. Run it before committing; it can run
  as a pre-commit hook (`.pre-commit-config.yaml`).
- Every error, log and metric string passes through
  `app/tools/results.py:redact_secrets`, which masks known values, credential URLs that
  embed a password, and `password=…`-style key/value pairs.

## Control plane vs data plane

The agent's own runtime data lives in a separate operational store
(`docs/usage/databases.md`). The analytics database is never written, and large artifact
payloads live in artifact storage, not in the operational database.

## Credential rotation

If a credential is ever committed, treat it as compromised: remove it from the working
tree, rotate/revoke it, and do not rewrite public history without explicit authorisation.

> SECURITY_ACTION_REQUIRED (project history): an older local branch ancestry contained an
> exposed provider token and database password. The published development branch was
> reconstructed from a clean root and contains none. The exposed credential should still
> be rotated/revoked.
