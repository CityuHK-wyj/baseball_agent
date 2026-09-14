# Development status

Status: IN_PROGRESS

Last updated by: DeepSeek Implementation Engineer session (milestone 3 — guarded tool
execution). Milestones 1–2 delivered earlier and preserved.

## Current phase

Milestone 3 — real safe analytical tool execution: **delivered (code level)**. SQL is
validated before any connection, PostgreSQL runs read-only with a statement timeout,
DuckDB paths are sandboxed, results are row-bounded and errors are redacted. No
unguarded analytical path remains.

Milestone 4 — persistence foundation: **delivered (service level)**. Operational store,
filesystem artifact storage, checkpoint coordinates and an idempotent resume slice.
Not yet wired into the Orchestrator.

Next phase: wire persistence into the Orchestrator, then Shared Context projection.

## Architecture status

Frozen. ADRs 0001–0006 accepted. No architecture conflicts found. No frozen boundary
was violated.

## Implemented

Milestone 1–2 (unchanged, still passing): immutable Objective/Requirement definitions
and separate state projections; contextual Artifact assessment with a hard-failure
veto; PLAN/REPLAN/STOP_PLANNING with a terminal latch; capability-based routing;
bounded execution retries; CompletionReport/ResponsePackage from accepted products.

Milestone 3 (ADR 0004, ADR 0006):

- `app/validation/sql_guard.py`: `guard_read_only_sql`, `resolve_within_root`. Table
  identity keeps schema/database qualification.
- `app/tools/results.py`: `ToolResult` (status / error_type / retryable / policy_blocked
  / safe_error_summary / row_count / execution_metadata) and `redact_secrets`.
- `app/tools/execution.py`: `PostgresReadOnlyExecutor`, `DuckDBReadOnlyExecutor`.
  Guard-before-connect, `SET TRANSACTION READ ONLY`, `SET LOCAL statement_timeout`,
  bounded LIMIT wrapper + `fetchmany`, injectable connection factories.
- `app/tools/postgres.py`, `app/tools/duckdb.py`: adapters execute only after
  validation and return `BLOCKED_BY_POLICY` on rejection.
- `app/agent/executor.py`: re-exports the shared `ToolResult`; `TaskAttempt` gains
  `error_type` and `safe_error_summary`.
- `main.py`: import-safe, guarded DuckDB summary; raw unguarded read removed.
- `app/features/engine.generate_pitch_heatmap`: fails closed.

Milestone 4 (ADR 0007):

- `app/persistence/store.py`: `OperationalStore` Protocol + `SqliteOperationalStore`
  (versioned JSON objects, append-only checkpoints).
- `app/persistence/artifacts.py`: `ArtifactStorage` Protocol +
  `LocalFilesystemArtifactStorage` (path-sandboxed, SHA-256, immutable references).
- `app/models/checkpoint.py`: `Checkpoint` with recovery positions and state-version refs.
- `app/persistence/recorder.py`: `RunRecorder` writes payloads before states, records
  assessments/executions/reports and takes checkpoints.
- `app/persistence/resume.py`: `ResumeService` reclassifies interrupted executions and
  reuses persisted artifacts.

## In progress

Nothing is partially edited.

## Not started

- Wiring `RunRecorder`/`ResumeService` into the `Orchestrator`; persisting tool payload
  bytes (today `ToolResult` carries artifact metadata, not bytes).
- Shared Context retrieval/projection and cross-run context isolation tests.
- Semantic/Normalization layer (`app/semantic/*`, `app/conversation/service.py`).
- Metric Registry / Source Mapping.
- LLM Planner/Judge/Response implementations behind the existing Protocol seams.
- Live integration test against a disposable PostgreSQL and synthetic Parquet.
- Operational PostgreSQL implementation of `OperationalStore`.

## Current branch

`agent/deepseek-implementation-safe`, a **history-reconstructed** branch. Its root is a
clean import of the verified-safe milestone-1 tree (`6ccb2ad`); the milestone-2 commit
series was replayed on top. This was necessary because the original local ancestry
(`9f2f44b` / `cf8e2d3`) contains exposed credentials. The old branch
`agent/deepseek-implementation` is retained locally but must not be pushed.

## Latest meaningful commit

See `git log --oneline`. Milestone-3 feature summary: guarded read-only tool execution
with redacted result contract (commit message `feat: enforce guarded read-only tool
execution`).

## Test command

`python3 -m unittest discover -s tests -v`

## Tests passing

101 tests, all passing (`Ran 101 tests ... OK`). Milestone 3: `test_tool_execution` 14.
Milestone 4: `tests/persistence` 10 (artifact storage 5, operational store 5) plus the
resume and flow tests (5+3). Earlier modules: test_config 2, test_domain 5, test_safety
4, test_secret_scan 4, test_artifacts 11, test_state 7, test_planner 7, test_routing 6,
test_executor 5, test_orchestrator 8, test_sql_guard 8.

`python3 -m compileall` passes. `python3 scripts/secret_scan.py` passes (exit 0).

## Tests failing

None.

## Known bugs

- The tool executors have not run against a real database. Connection-phase retryability
  and the exact DuckDB LIMIT-wrapper behavior are validated only with fakes.
- `sqlglot` emits a parse warning for `LOAD` before classifying it as a forbidden
  `Command`; behavior is correct but the warning is noisy.

## Technical debt

- No live-source integration; the loop is proven only with deterministic tools and fakes.
- `RuleBasedPlanner` and `RuleBasedJudge` remain intentionally simple.
- `Router` optimizes only by cost.
- No logging/observability; redaction is centralized in `redact_secrets` but not yet
  wired to a logging sink.
- LIMIT wrapping changes the executed SQL for unbounded reads; verify against a real
  engine and decide whether to require an explicit LIMIT instead.

## Architecture deviations

- `main.py` was previously an unguarded raw DuckDB read; it is now guarded. Documented.
- History was reconstructed (see Current branch). No code or domain behavior changed.
- No conflict with any confirmed blog decision was discovered.

## ADRs added

- `docs/adr/0006-guarded-tool-execution.md` (milestone 3).
- `docs/adr/0007-operational-stores-and-checkpoints.md` (milestone 4).
- 0001–0005 from earlier sessions.

## Database / migration status

No operational database or migrations. No analytics database or Parquet file was read,
written or modified. The verified read-only executor is wired but not live-tested.

## Security audit status

- Current working tree scan passes (exit 0).
- **History reconstruction performed:** the safe branch has 0 real findings across all
  reachable commits (verified by scanning every blob in `git rev-list`). The old local
  ancestry still contains exposed credentials and must never be pushed.
- `102c9939/tests/test_config.py:10` was a scanner false positive (synthetic values in
  a dict literal); the current tree no longer triggers it.
- No live credential was used; no paid API call was made.
- **SECURITY_ACTION_REQUIRED: Rotate/revoke previously exposed credential.** A real
  provider token and database password were committed in the old local history. They
  were never printed, and history was not force-rewritten, but rotation is still
  required.

## Current blockers

- **GitHub push blocked:** `gh` is not installed and no git credential is configured
  (`git ls-remote origin` fails). The safe branch is ready; authentication must be
  completed by the user (`gh auth login`, HTTPS + browser).
- Live DB integration blocked on a running read-only PostgreSQL/DuckDB target.

## Exact next task

Wire persistence into the `Orchestrator`, TDD, without changing domain contracts.

1. Give tools a way to supply payload bytes (extend `ToolResult` with an optional
   `payload: bytes | None`, or have the executor build the `Artifact` plus payload).
2. Add an optional `recorder: RunRecorder | None` to `Orchestrator.__init__`; when
   present, record artifacts (payload first), assessments, requirement/objective
   states and executions, and take checkpoints at `PLAN_ACCEPTED`, `ARTIFACT_ASSESSED`,
   `PLANNER_TERMINAL` and `FINALIZATION`.
3. Add `tests/persistence/test_orchestrator_persistence.py`: a full run persists a
   checkpoint; `ResumeService.build_plan` finds it, reuses artifacts and preserves a
   terminal decision; no rejected evidence is persisted as accepted.
4. Then start the Shared Context minimal slice: `ContextRequest` → deterministic
   reference retrieval → `ContextPackage`, excluding failed attempts.

Do not: build a single giant AgentState dump; put runtime tables in the baseball
analytics database; inline large payloads in the operational store.

## Recommended Codex review priorities

1. Guard-before-connect: prove no executor path can connect on rejected SQL.
2. Redaction completeness in `redact_secrets` and every `ToolResult` error path.
3. `guard_read_only_sql` bypasses: nested table functions, CTE shadowing, dialect
   spellings, schema-qualified allowlist behavior.
4. LIMIT-wrapper correctness on real PostgreSQL and DuckDB.
5. Persistence: artifact-before-state ordering, checkpoint version refs, idempotent
   resume, immutability of `LocalFilesystemArtifactStorage`.
6. `PlannerTerminalLatch` and `NO_PROGRESS` termination.
7. `ResponsePackage` leakage of rejected evidence/history.
8. History safety: confirm no push includes the old ancestry.
