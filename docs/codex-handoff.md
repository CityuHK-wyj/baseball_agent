# Codex Handoff

This file lets the next agent continue without the prior chat. Read it after
`AGENTS.md`, `CONTEXT.md`, `docs/development-status.md` and the ADRs.

## Repository State

- branch: `agent/deepseek-implementation-safe` (history-reconstructed; the old
  `agent/deepseek-implementation` must not be pushed)
- root commit: clean import of the verified-safe milestone-1 tree (`6ccb2ad`)
- tests: `python3 -m unittest discover -s tests -v` → 109 passing
- secret scan: current tree exit 0; all commits reachable from the safe branch have
  0 real findings (verified by scanning every blob)
- remote: `https://github.com/CityuHK-wyj/baseball_agent.git`; **push blocked** — `gh`
  is not installed and no git credential is configured. The user must authenticate.

## What DeepSeek Implemented

Milestone 5 (this session) — Shared Context minimal slice (ADR 0008):

- `app/context/service.py`: `ContextRequest` → deterministic reference retrieval →
  `ContextPackage`; structural exclusion of attempts, routing, drafts, judge reasoning,
  rejected evidence, unused RAG and (for Response) superseded plans.
- Tests: `tests/context/test_context_service.py` (8).

Milestone 4 (this session) — persistence foundation (ADR 0007):

- `app/persistence/store.py`: `OperationalStore` Protocol + `SqliteOperationalStore`
  (versioned JSON objects, append-only checkpoints).
- `app/persistence/artifacts.py`: `ArtifactStorage` Protocol +
  `LocalFilesystemArtifactStorage` (path-sandboxed, SHA-256, immutable).
- `app/models/checkpoint.py`: `Checkpoint`. `app/persistence/recorder.py`: `RunRecorder`.
- `app/persistence/resume.py`: `ResumeService` (RUNNING→INTERRUPTED, artifact reuse).
- Tests: `tests/persistence/` (artifact storage, operational store, resume, flow).
- `TaskExecution.status` gained `RUNNING`.

Milestone 3 (previous session) — guarded read-only tool execution (ADR 0004, 0006):

- `app/tools/results.py`: `ToolResult` contract (SUCCESS / NO_DATA / POLICY_REJECTED /
  TECHNICAL_FAILURE) and `redact_secrets`.
- `app/tools/execution.py`: `PostgresReadOnlyExecutor` and `DuckDBReadOnlyExecutor`.
  Guard runs before connect; PostgreSQL uses `SET TRANSACTION READ ONLY` +
  `SET LOCAL statement_timeout`; LIMIT-less reads are wrapped and `fetchmany`-bounded.
- Rewired `app/tools/postgres.py`, `app/tools/duckdb.py`; removed the unguarded DuckDB
  read from `main.py`; `generate_pitch_heatmap` fails closed.
- `TaskAttempt` gained `error_type` and `safe_error_summary`.
- `guard_read_only_sql` now uses schema-qualified table identity.
- Tests: `tests/test_tool_execution.py` (14) + existing `tests/test_sql_guard.py` (8).

Milestones 1–2 (previous sessions, still passing):

- Artifact domain: immutable `Artifact`, `DeterministicResult` (hard/soft),
  `JudgeResult`, contextual `ArtifactAssessment` with a hard-failure veto.
- `ArtifactRegistry` (identity/lineage/index, no quality).
- `RuleBasedJudge` using `ArtifactRequirement.evidence_purpose`.
- Pure state services: `derive_requirement_state`, `derive_objective_state`,
  `optional_gaps`, `unmet_core_requirements`.
- Planning: `PlannerContext`, `RuleBasedPlanner` (PLAN/REPLAN/STOP_PLANNING),
  `PlannerTerminalLatch`.
- Routing: `ToolCapability` + `Router` with mandated precedence.
- Execution: `Tool` Protocol, `ToolResult`, `Executor` (bounded retries; EMPTY is not
  retried).
- Orchestration: `Orchestrator.run` → `RunResult` with `CompletionReport` and a
  `ResponsePackage` built only from accepted assessments.
- Security: `guard_read_only_sql` + `resolve_within_root` (sqlglot AST guard).
- ADRs 0002–0005 and updated `CONTEXT.md`, `README.md`, spec and tickets.

## What Was Intentionally Not Implemented

- Wiring `RunRecorder`/`ResumeService` into the `Orchestrator`; persisting tool payload
  bytes (today `ToolResult` carries artifact metadata only).
- Wiring `ContextService` into the Planner/Response boundaries.
- Shared Context cross-run context isolation tests and a Metric Registry source.
- An Operational PostgreSQL implementation of `OperationalStore` (SQLite is the local,
  replaceable first version).
- A live integration test against a real analytical database/Parquet fixture.
- LLM Planner / LLM Judge / Response generation. Only deterministic implementations
  exist; the Protocol seams are there but untested against a model.
- Semantic/Normalization layer (`app/semantic/*`, `app/conversation/service.py`).
- Metric Registry / Source Mapping.
- Logging/observability.

## Architecture Decisions Used

- ADR 0001 (pre-existing): definition/state separation, immutable Initial Requirements,
  read-only analytics, deferred persistence.
- ADR 0002: contextual assessment; hard failures non-overridable.
- ADR 0003: Planner terminal latch; orchestrator loop and stop reasons.
- ADR 0004: read-only SQL AST guard, not string matching.
- ADR 0005: state services are pure functions; in-memory slice; persistence deferred.

## Files Codex Should Read First

1. `AGENTS.md`, `CONTEXT.md`
2. `docs/development-status.md` (this session's exact evidence + next task)
3. `docs/adr/0008-shared-context-retrieval.md`, `0007-operational-stores-and-checkpoints.md`, `0006-guarded-tool-execution.md`, `0002`–`0005`
4. `.scratch/architecture-implementation/spec.md` and `issues/02..05`
5. `app/context/service.py`, `app/persistence/{store,artifacts,recorder,resume}.py`, `app/models/checkpoint.py`
6. `app/tools/{results,execution,postgres,duckdb}.py`, `app/validation/sql_guard.py`
7. `app/models/{contracts,artifacts,planning,reports}.py`
8. `app/agent/{planner,routing,executor,orchestrator,response,registry}.py`
9. `app/assessment/{validator,judge,service}.py`, `app/state/services.py`
10. `tests/persistence/`, `tests/test_tool_execution.py`, `tests/test_sql_guard.py`

## Important Tests

- `test_artifacts.py::ArtifactAssessmentTests::test_same_artifact_is_acceptable_for_existence_and_weak_for_inference`
  — the contextual-assessment invariant.
- `test_artifacts.py::ArtifactAssessmentTests::test_hard_failure_forces_reject_even_with_a_strong_judge`
  — hard veto at the contract level.
- `test_planner.py::PlannerTerminalLatchTests` — terminal latch semantics.
- `test_orchestrator.py::test_zero_row_artifact_is_rejected_and_never_reaches_response`
  — no-progress stop + rejected evidence excluded from the response.
- `test_orchestrator.py::test_response_package_excludes_history_and_rejected_products`
  — Response Agent isolation.
- `test_sql_guard.py` — mutation/admin/function/table/path rejections.

## Known Weak Areas

- Loop termination is proven only for the deterministic Planner; an LLM Planner could
  return tasks that never produce new artifact ids (still caught by `NO_PROGRESS`).
- `RuleBasedJudge` heuristics (`major >= 2` → REJECT, etc.) are policy choices with no
  external validation.
- `Router._optimize` only sorts by cost; coverage/freshness are unmodeled.
- `TaskExecution` status is not used by the Planner yet (only requirement states are).
- No persistence means `RunResult` cannot be resumed; treat all progress as lost on
  restart.
- `resolve_within_root` handles simple globs by resolving the literal prefix; exotic
  globbing or symlinked roots are not covered.

## Potential Architecture Violations to Review

- `RunResult` bundles internal diagnostics and the response package. Confirm no caller
  passes `RunResult` to a Response Agent instead of `ResponsePackage`.
- `Orchestrator` computes `recoverable`/`policy_blocked` gaps using `Router`. This is
  scheduling, not source-domain judgment, but confirm it is not drifting into Router
  responsibility.
- `RuleBasedPlanner` decides stop reasons; confirm no domain/metric interpretation
  leaked in.
- `AssessmentService` builds the summary string. Confirm it stays a deterministic
  projection and never becomes a second Judge.

## Security Concerns

- Historical credentials remain in the OLD local branch ancestry. It must never be
  pushed. The safe branch (`agent/deepseek-implementation-safe`) was reconstructed
  from a clean root; scan all reachable commits → 0 findings.
- `guard_read_only_sql` + executors are wired but not live-tested against a real
  engine. DuckDB extension/ATTACH denial depends on the node denylist; add dialect
  spellings before trusting it.
- Redaction is centralized but not wired to a logging sink; any new error path must
  route through `redact_secrets`.
- SECURITY_ACTION_REQUIRED: rotate/revoke the previously exposed credential.

## Performance Concerns

- Not benchmarked. The loop is step-bounded by `max_rounds` and `budget` only.
- `ArtifactRegistry._content` serializes the whole artifact on every re-registration;
  fine now, revisit with large metadata.

## Persistence Concerns

- Operational store, artifact storage, checkpoint and resume services exist and are
  tested, but are NOT wired into the Orchestrator yet. A run is not actually recoverable
  end to end; the pieces are proven in isolation and via a service-level flow test.
- `SqliteOperationalStore` is single-writer and not concurrency-tested.
- Tool payload bytes are not yet persisted from the execution layer.

## Context / RAG Concerns

- The minimal `ContextService` slice exists and is tested, but is NOT wired into the
  Planner or Response builder. It projects nothing yet in a real run.
- No Metric Registry, Schema Registry or RAG source is implemented; `StaticContextSource`
  is the only source. No embeddings or pgvector, deliberately.
- Exclusion policy (`_ALWAYS_EXCLUDED`, `_RESPONSE_ONLY_EXCLUDED`) is enforced in the
  service; confirm no caller bypasses it when wiring.

## Suggested Adversarial Tests

- Planner returns a task whose tool returns the same artifact id forever → `NO_PROGRESS`.
- Judge returns STRONG with a hard failure present → contract `ValidationError`.
- `ResponsePackage` after a mixed accept/reject run contains only accepted evidence.
- Router with a preference for a policy-blocked source → falls back, never blocked.
- SQL: `WITH x AS (SELECT ...) INSERT ...`; nested `read_parquet` in a subquery;
  `COPY` inside a CTE; mixed-case/comment-obfuscated `DROP`; `;` inside a string
  literal; `SELECT ... FROM read_parquet(?)` with a parameter; `other_schema.table`
  against an unqualified allowlist.
- Tool execution: a rejected query must leave an `ExplodingConnect` with 0 calls;
  connection error summary must never contain the configured password.
- Requirement state after re-assessing the same artifact twice → version does not
  spuriously advance.

## Current Open Questions

- Should `TaskExecution` outcomes feed `PlannerContext` explicitly?
- What is the operational schema and checkpoint coordinate (ticket 05)?
- Are SQL `BASEBALL_DATABASE_URL` and the current discrete Settings fields both wanted?
- Is `RuleBasedPlanner` sufficient for v1, or is an LLM Planner required next?

## Exact Continuation Point

Wire persistence into the `Orchestrator` TDD, per `docs/development-status.md` →
"Exact next task": add an optional `RunRecorder` to the Orchestrator, persist payload
bytes from the tool layer, record artifacts (payload first) + assessments + states +
executions, take checkpoints at `PLAN_ACCEPTED`/`ARTIFACT_ASSESSED`/`PLANNER_TERMINAL`/
`FINALIZATION`, and test a full run → checkpoint → resume. Then wire `ContextService`
into the Planner/Response boundaries. Do not build a single giant AgentState dump.
