# v0.1 validation checkpoint

Status: **IN_PROGRESS — stable checkpoint**, 2026-09-16.

## Stores and entry points

| Store | Default location | Purpose |
| --- | --- | --- |
| Shared Knowledge | `.runtime/knowledge.db` | 605 seed items, provenance, versions, aliases; rebuilt from `knowledge/seed` and `knowledge/sources` |
| Analytics PostgreSQL | `127.0.0.1:5433/baseball_analytics` | Read-only recent Statcast (`baseball_readonly`); LIVE_VERIFIED for SELECT, writes denied |
| Analytics Parquet | `data_loader/parquet_archive` | Read-only historical files, 2015–2023 present |
| Operational store | `.runtime/operational.db` | Checkpoints, interactions, audit snapshots, reports, redacted `run_event` metrics |
| Artifact payloads | `.runtime/artifacts` | Persisted accepted-product payloads |

`KNOWLEDGE_STORE_PATH`, `OPERATIONAL_STORE_PATH`, `ARTIFACT_STORAGE_PATH`, and
`PARQUET_ARCHIVE_PATH` override local paths. PostgreSQL uses the `POSTGRES_*` settings.
Operational PostgreSQL requires a separately injected `PostgresOperationalStore`; it must
never use the analytics database. No operational PostgreSQL connection was supplied.

## Commands executed during this integration

These use the existing working `python3` environment. The repository `.venv` lacked
pydantic; a fresh dependency installation was not verified during this integration.

```bash
python3 -m app.cli ask "DFA是什么意思？" --persist --json
python3 -m app.cli ask "道奇属于哪个分区？" --json
python3 -m app.cli ask "qualified hitter 是什么？" --json
python3 -m app.cli knowledge status
python3 -m app.cli knowledge validate
python3 -m app.cli knowledge search "DFA"
python3 -m app.cli knowledge search "infield fly"
python3 -m app.cli knowledge show PLAYER:660271
python3 scripts/verify_v01_workflows.py
python3 -m unittest discover -s tests -q
python3 -m compileall -q app tests scripts
python3 scripts/secret_scan.py
python3 scripts/verify_v01_live.py
```

The workflow script creates an isolated temporary runtime, answers three real knowledge
questions, and runs a fixture clarification through `ask → answer → resume/inspect/metrics`
in separate CLI processes. Its analytical answer is explicitly synthetic. Temporary
fixtures do not alter the committed knowledge base.

## Clarification, permission and constraint revision

Run `ask` with `--persist --json` to obtain `run_ids` and request objects. IDs are unique;
do not assume `run-1`. The `answer` subcommand accepts the returned run and request IDs:
use `--choice` with the selected option ID, `--permission approve|reject`, or
`--revision accept|reject`. The workflow script exercises the concrete clarification CLI
command; the integration suite exercises paid Web approval/rejection and source revision.
`resume` inspects recovery state by default. `resume --execute` recovers a consumed
interaction or a persisted run; use `--demo` again for synthetic runs. The workflow script
also verifies this execution path in a new process. `answer` consumes a waiting interaction.

Programmatic equivalents are `resume_clarification`, `resume_permission`, and
`resume_constraint_revision`. Permission and revision requests expire after 15 minutes.
Unknown references, wrong runs and repeated answers fail before execution. Permission
approval rechecks current capability, tool/source/cost and system policy and applies only
to its objective. Constraint acceptance records `USER_CONFIRMED`; rejection retains the
original constraint. System policy is never revisable. Legacy permission records without
expiry fail closed.

## Live evidence and limitations

| Probe | Result |
| --- | --- |
| DuckDB guarded read of `mlb_statcast_2023.parquet` | `VERIFIED_LIVE`: five rows returned |
| Real analytics slice (two-strike, fastball >95 mph, upper zone, top-5 EV) | `LIVE_VERIFIED`: end-to-end over the Parquet archive with provenance, no synthetic fallback |
| High-zone exact batter-relative upper edge | `UNVERIFIED_LIVE`: archive and PostgreSQL lack `sz_top`/`sz_bot`; surfaced as clarification/limitation, never silently replaced with `zone IN (...)` |
| Analytics PostgreSQL | `LIVE_VERIFIED`: read-only SELECT works; CREATE/INSERT/UPDATE/DELETE/DROP all denied |
| MLB StatsAPI transport | Returned 30 teams once; subsequent attempts failed; transport verification does not prove live Web evidence extraction |
| Live WebEvidenceTool flow | `UNVERIFIED_LIVE`; default pipeline tested with an injected document fixture |
| Operational PostgreSQL | `UNVERIFIED_LIVE`: separate connection not configured |
| Last 30 days analytics | `UNVERIFIED_LIVE`: default semantic/date planning and a current provider still need integration |

Knowledge retrieval passes the question to ContextService and caps each runtime package
at eight items. Explicit dates are honored; a single year uses July 1 of that year.
Snapshots without historical validity are excluded from earlier dates. Multi-year or
within-season rule changes need a more explicit date interpretation. Seed coverage is
limited: the Hernandez ambiguity lifecycle is fixture-tested; the default player seed
does not include the Hernandez candidates.

Crash recovery persists immutable run/initial definitions and atomically claims an
execution intent before calling a tool. `AnalysisPipeline.resume_run(run_id)` restores
each objective separately, reuses durable executions/artifacts, and does not consume the
answer again. Completed objectives perform no new executions or duplicate assessment.
Payloads remain in artifact storage; execution outcomes contain references only.

If a process dies while a tool may have executed but no completed execution was persisted,
recovery raises `EXECUTION_UNCERTAIN` and never automatically retries. An external call
and a local commit cannot be made exactly-once without provider cooperation. This is a
deliberate at-most-once boundary, not a success claim. Expired or revoked permission fails
closed on recovery. Tests include actual `os._exit` and repeated fresh-process restarts,
plus failures before assessment and before the outcome index is committed.
Date planning supports inclusive `近30天` / `last 30 days`, a single calendar year,
and one/two ISO dates. Dates are frozen before waiting and retained on same-run resume.
The Python builder accepts `today=` for a deterministic clock; normal use defaults to
the host's current date. Multiple date windows fail closed, not silently interpreted.
Complex analytics planning and the live gaps above still prevent a v0.1 completion claim.

Latest `python3 scripts/verify_v01_live.py`: PostgreSQL authentication requires a password;
historical Parquet bounded reads and MLB teams transport pass. High-zone analytics lacks
sz_bot/sz_top. Transport success is not current-data or Web Evidence validation.

The real analytics vertical slice (typed constraints → read-only Parquet adapter → ranked
answer) is exercised by `tests/integration/test_analytics_integration.py` and requires no
synthetic data. The PostgreSQL adapter is live-verified with `POSTGRES_PASSWORD` in the
environment; it resolves batter names via `player_dictionary`. See
[docs/development/analytics-capability-matrix.md](../development/analytics-capability-matrix.md)
for the verified schema, semantic capability matrix and the reported `sz_top`/`sz_bot`
ingestion gap.
