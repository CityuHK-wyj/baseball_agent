# Codex Handoff

This file lets the next agent continue without the prior chat. Read it after
`AGENTS.md`, `CONTEXT.md`, `docs/development-status.md` and the ADRs.

## Repository State

- branch: `agent/deepseek-implementation-safe` (history-reconstructed; the old
  `agent/deepseek-implementation` must not be pushed)
- root commit: clean import of the verified-safe milestone-1 tree (`6ccb2ad`)
- tests: `python3 -m unittest discover -s tests -v` → 81 passing
- secret scan: current tree exit 0; all commits reachable from the safe branch have
  0 real findings (verified by scanning every blob)
- remote: `https://github.com/CityuHK-wyj/baseball_agent.git`; **push blocked** — `gh`
  is not installed and no git credential is configured. The user must authenticate.

## What DeepSeek Implemented

Milestone 3 (this session) — guarded read-only tool execution (ADR 0004, 0006):

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

- Operational PostgreSQL, payload store, checkpoints, resume, idempotent replay and
  Shared Context projection (ticket 05) — next up.
- A live integration test against a real analytical database/Parquet fixture.
- LLM Planner / LLM Judge / Response generation. Only deterministic implementations
  exist; the Protocol seams are there but untested against a model.
- Semantic/Normalization layer (`app/semantic/*`, `app/conversation/service.py`).
- Metric Registry / Source Mapping.
- Logging/observability.
- Supporting-requirement proposals from the Planner.

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
3. `docs/adr/0006-guarded-tool-execution.md`, `0002-*.md`, `0003-*.md`, `0004-*.md`, `0005-*.md`
4. `.scratch/architecture-implementation/spec.md` and `issues/02..05`
5. `app/tools/{results,execution,postgres,duckdb}.py`, `app/validation/sql_guard.py`
6. `app/models/{contracts,artifacts,planning,reports}.py`
7. `app/agent/{planner,routing,executor,orchestrator,response,registry}.py`
8. `app/assessment/{validator,judge,service}.py`, `app/state/services.py`
9. `tests/{test_tool_execution,test_sql_guard,test_artifacts,test_state,test_planner,test_routing,test_executor,test_orchestrator}.py`

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

- None implemented. `Artifact.payload_ref` is a coordinate with no backing store.
- Checkpoint/resume, INTERRUPTED handling and idempotency are ticket 05 and are the
  exact continuation point.

## Context / RAG Concerns

- No retrieval, ranking, freshness or projection exists. `PlannerContext` already
  carries bounded `artifact_index` and `assessment_summaries` — the seam for
  "persist broadly, retrieve narrowly". Milestone 1's `app/semantic/schema_rag.py` is a
  placeholder.

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

Implement ticket 05's persistence foundation TDD, per `docs/development-status.md` →
"Exact next task": `app/persistence/artifacts.py` (`ArtifactStorage` +
`LocalFilesystemArtifactStorage`), `app/persistence/store.py` (`OperationalStore` +
local SQLite implementation), `app/models/checkpoint.py` (`Checkpoint`), and
`app/persistence/resume.py` (interrupted-run reclassification and artifact reuse),
with `tests/persistence/`. Do not build a single giant AgentState dump.
