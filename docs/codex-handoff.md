# Codex Handoff

This file lets the next agent continue without the prior chat. Read it after
`AGENTS.md`, `CONTEXT.md`, `docs/development-status.md` and the ADRs.

## Repository State

- branch: `agent/deepseek-implementation`
- base commit: `6ccb2ad` (`codex/architecture-implementation` HEAD, milestone 1)
- working tree: clean after the checkpoint commit (verify with `git status`)
- tests: `python3 -m unittest discover -s tests -v` → 66 passing
- remote: `https://github.com/CityuHK-wyj/baseball_agent.git`; **push blocked locally**
  (no GitHub credential in this environment). Do not push the historical
  credential-bearing ancestry.

## What DeepSeek Implemented

Milestone 2 vertical slice, deterministic and fully tested:

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

- Live PostgreSQL/DuckDB execution. Adapters remain fail-closed (ticket 02).
- LLM Planner / LLM Judge / Response generation. Only deterministic implementations
  exist; the Protocol seams are there but untested against a model.
- Operational PostgreSQL, payload store, checkpoints, resume, idempotent replay and
  Shared Context projection (ticket 05).
- Semantic/Normalization layer (`app/semantic/*`, `app/conversation/service.py`).
- Metric Registry / Source Mapping.
- Logging/observability.
- Supporting-requirement proposals from the Planner (the catalog supports them; the
  rule Planner never proposes one).

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
3. `docs/adr/0002-*.md`, `0003-*.md`, `0004-*.md`, `0005-*.md`
4. `.scratch/architecture-implementation/spec.md` and `issues/02..05`
5. `app/models/{contracts,artifacts,planning,reports}.py`
6. `app/agent/{planner,routing,executor,orchestrator,response,registry}.py`
7. `app/assessment/{validator,judge,service}.py`, `app/state/services.py`
8. `app/validation/sql_guard.py`
9. `tests/{test_artifacts,test_state,test_planner,test_routing,test_executor,test_orchestrator,test_sql_guard}.py`

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

- Historical credentials remain in local history (milestone 1). Rotate them; do not
  publish that ancestry.
- `main.py` bypasses the guard with a raw DuckDB read. Remove or route it through the
  guard.
- The guard is not wired to any connection, so its live enforcement is unproven.
- DuckDB extension loading / ATTACH denial depends on the node denylist; add tests for
  new dialect spellings before trusting it.

## Performance Concerns

- Not benchmarked. The loop is step-bounded by `max_rounds` and `budget` only.
- `ArtifactRegistry._content` serializes the whole artifact on every re-registration;
  fine now, revisit with large metadata.

## Persistence Concerns

- None implemented. `Artifact.payload_ref` is a coordinate with no backing store.
- Checkpoint/resume, INTERRUPTED handling and idempotency are ticket 05.

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
  literal; `SELECT ... FROM read_parquet(?)` with a parameter.
- Requirement state after re-assessing the same artifact twice → version does not
  spuriously advance.

## Current Open Questions

- Should `TaskExecution` outcomes feed `PlannerContext` explicitly?
- What is the operational schema and checkpoint coordinate (ticket 05)?
- Is `RuleBasedPlanner` sufficient for v1, or is an LLM Planner required next?
- Which Shared Context sources are authoritative for freshness/league progress?

## Exact Continuation Point

Implement ticket 02's remainder: wire `guard_read_only_sql` into
`app/tools/postgres.py` and `app/tools/duckdb.py` behind a new read-only executor and
`tests/test_tool_execution.py`, per `docs/development-status.md` → "Exact next task".
Then commit, and only then start ticket 05.
