# Troubleshooting

## Database connection failure

Symptom: `TECHNICAL_FAILURE` with a redacted summary, `retryable=True`.

- The executor treats connection-phase errors as retryable technical failures.
- Check `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`,
  `POSTGRES_PASSWORD`, and that the database is running.
- The error summary is redacted on purpose; look at your own connection settings rather
  than expecting the password in the message.

## `NO_DATA` (0 rows)

Symptom: `status=EMPTY`, `error_type=NO_DATA`, not an error.

Zero rows is a business observation, not a failure. The artifact is assessed; a zero-row
artifact is rejected (`ZERO_ROWS`), the requirement becomes `UNSATISFIED`, and the run
stops or reports a limitation. Check your filters before assuming a bug.

## Missing table / column

The AST guard rejects non-allowlisted tables; a real query error (missing column) surfaces
as a redacted `TECHNICAL_FAILURE` (`retryable=False`). The Schema Registry is the source of
truth for physical names; the Planner never guesses columns.

## `BLOCKED_BY_POLICY`

The statement was rejected before any connection: a write/DDL/admin statement, a forbidden
function, a non-allowlisted table, or a file path outside the sandbox. This is intended.
See [security.md](security.md).

## Parquet path invalid

Symptom: `POLICY_REJECTED` mentioning a path escaping the root.

File paths must be inside `PARQUET_ARCHIVE_PATH`. Use a relative or absolute path under
that root; `..` and symlink escapes are rejected.

## API key missing

Symptom: `ProviderError: No LLM credential is configured (set DEEPSEEK_API_KEY).`

The offline CLI needs no key. For LLM agents, set `DEEPSEEK_API_KEY`. The deterministic
Planner/Judge/Response are the fallback and need no key.

## LLM provider failure / timeout

`LLMPlanner`, `LLMJudge` and `LLMResponseComposer` fall back to their deterministic
implementations when the provider raises `ProviderError`/`ProviderTimeout` or returns
malformed output. If you supplied no fallback, the error propagates. Malformed structured
output is always rejected rather than trusted.

## Clarification loop

Symptom: `ask` prints a clarification question instead of a result.

The entity is ambiguous or unknown. Provide the mention explicitly:

```bash
python3 -m app.cli ask "How did the player perform?" --mention "Aaron Judge"
```

## Resume / checkpoint

```bash
python3 -m app.cli resume --run-id run-1
```

- `NO_CHECKPOINT` means nothing was persisted for that run id (did you pass `--persist`?).
- A `RUNNING` execution is reclassified `INTERRUPTED` on resume; it is never assumed to
  still be running.
- Reusable artifacts are reused; the same artifact is not re-fetched.

## SQLite lock

`SqliteOperationalStore` is single-writer. If you see "database is locked", another process
holds the operational store. Close it, or use the PostgreSQL store for concurrent access.

## Postgres migration

Schema is created idempotently on store construction (`CREATE TABLE IF NOT EXISTS`). There
is no migration tool yet; if you change the schema, apply the change manually and note it
in the handoff.

## Tests failing on `LOAD` parse warning

`sqlglot` prints a warning for `LOAD` before classifying it as a forbidden `Command`. The
behavior is correct; the warning is noise.
