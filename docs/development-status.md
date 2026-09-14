# Development status

Status: IN_PROGRESS

Last updated by: DeepSeek Implementation Engineer session (milestone 2).

## Current phase

Milestone 2 — evaluation, planning and accepted-product vertical slice: **delivered
and tested with deterministic in-memory tools**. The loop is closed end to end
(Objective/Requirement → Plan → Route → Execute → Artifact → Validate/Judge →
Assessment → RequirementState → ObjectiveState → CompletionReport → ResponsePackage).

Not yet connected to live data. Persistence, checkpoints and Shared Context are not
started.

## Architecture status

Frozen. ADRs 0001–0005 accepted. No architecture conflicts were found. No frozen
boundary was violated; see "Architecture Deviations" below for the deliberate
in-memory substitution and the still-fail-closed adapters.

## Implemented

Domain definitions and state projections:

- `app/models/contracts.py`: `AnalysisObjective`, `ArtifactRequirement`,
  `ArtifactDescriptor`, `Entity`, `TimeRange`, `Constraint`, `RequirementState`,
  `ObjectiveState`, plus additive `optional_data_keys`, `evidence_purpose`,
  `min_row_count`.
- `app/models/requirements.py`: `RequirementCatalog` (immutable Initial Requirements,
  append-only PLANNER_ADDED supporting requirements, idempotent replay).

Artifacts and assessment (ADR 0002):

- `app/models/artifacts.py`: `Artifact`, `Provenance`, `DeterministicResult`,
  `HardFailure`, `SoftSignal`, `JudgeResult`, `ArtifactAssessment` (hard-failure veto
  in the contract), `ArtifactIndexEntry`, `AssessmentSummary`.
- `app/assessment/validator.py`: deterministic hard failures and soft signals.
- `app/assessment/judge.py`: `Judge` Protocol + `RuleBasedJudge` using
  `evidence_purpose`.
- `app/assessment/service.py`: `AssessmentService` (contextual assessment storage).
- `app/agent/registry.py`: `ArtifactRegistry` (identity, lineage, bounded index; no
  quality judgement).

State services (ADR 0005):

- `app/state/services.py`: pure `derive_requirement_state`, `derive_objective_state`,
  `optional_gaps`, `unmet_core_requirements`. Not agents; no I/O, no LLM.

Planning, routing, execution (ADR 0003):

- `app/models/planning.py`: `AgentTask`, `TaskAttempt`, `TaskExecution`,
  `PlanningDecision` (terminal contract), `RoutingDecision`, `StopReason`.
- `app/agent/planner.py`: `PlannerContext`, `Planner` Protocol, `RuleBasedPlanner`,
  `PlannerTerminalLatch`.
- `app/agent/routing.py`: `ToolCapability`, `Router` with the mandated precedence.
- `app/agent/executor.py`: `Tool` Protocol, `ToolResult`, `Executor` with bounded
  retries only for retryable errors.

Finalization:

- `app/models/reports.py`: `RequirementCompletion`, `ExecutionSummary`,
  `CompletionReport`, `AcceptedEvidence`, `ResponsePackage`.
- `app/agent/orchestrator.py`: `Orchestrator`, `RunResult`; legacy
  `run_all_channel_baseball_agent` still raises.
- `app/agent/response.py`: `build_response_package` from accepted assessments only.

Security:

- `app/validation/sql_guard.py`: `guard_read_only_sql`, `resolve_within_root`
  (ADR 0004). Parses with sqlglot; not yet wired to a live connection.
- Legacy analytics writers, the legacy LLM loop, the unvalidated SQL adapters and the
  archive/plot audit helpers remain fail-closed.

## In progress

Nothing is partially edited. Ticket 02 is PARTIAL by scope: guard done, live executor
pending.

## Not started

- Ticket 02 remainder: live read-only PostgreSQL/DuckDB executor, result bounding,
  timeouts, provider-error redaction, synthetic integration fixtures.
- Ticket 05: Operational PostgreSQL, payload store, checkpoints, resume/idempotency,
  Shared Context retrieval/projection.
- Semantic/Normalization layer: `app/semantic/*` and `app/conversation/service.py` are
  still placeholders. No LLM Planner/Judge/Response is wired; only deterministic
  implementations exist.
- Metric Registry / Source Mapping contracts.

## Current branch

`agent/deepseek-implementation`, created from `codex/architecture-implementation`
HEAD so milestone 1 history is preserved. Rationale: the user requested this branch
name; the repository previously had no `agent/*` rule.

## Latest meaningful commit

Milestone 2 feature tip: `aad6ec3` on `agent/deepseek-implementation` (documentation
checkpoint commits follow it; use `git log --oneline` for the exact HEAD). Feature
commit series on top of milestone 1 (`6ccb2ad`):

- `2879215` feat: add immutable artifact contracts and contextual assessment
- `f9be393` feat: add requirement and objective state services
- `723798e` feat: add planning, routing and bounded execution
- `d83cd2b` feat: add orchestrator finalization and accepted-product response
- `05b12a5` feat: add dialect-aware read-only sql ast guard
- `206a576` docs: record milestone 2 domain model, adrs and handoff
- `aad6ec3` chore: vendor agent skill definitions and lockfile

Nothing was pushed; this environment has no GitHub credential.

## Test command

`python3 -m unittest discover -s tests -v`

## Tests passing

66 tests before the final category-constraint regression; 67 tests now, all passing
(`Ran 67 tests ... OK`). Distribution:

- test_config 2, test_domain 5, test_safety 4, test_secret_scan 4
- test_artifacts 11, test_state 7
- test_planner 7, test_routing 6, test_executor 5, test_orchestrator 8
- test_sql_guard 8

`python3 -m compileall` passes. `python3 scripts/secret_scan.py` passes (exit 0).

## Tests failing

None.

## Known bugs

- `app/tools/postgres.py` and `app/tools/duckdb.py` deliberately return
  `BLOCKED_BY_POLICY`; this is a containment state, not a bug, but it means no real
  analytics query can run yet.
- `main.py` still contains a raw DuckDB read of the local Parquet archive outside the
  guard. It is not imported by the runtime and only reads, but it is an uncontrolled
  entry point that ticket 02/containment should remove or route through the guard.

## Technical debt

- No live-source integration; the whole loop is proven only with deterministic tools.
- `RuleBasedPlanner` is intentionally simple (one task per unmet core requirement); an
  LLM Planner is a future seam, not implemented.
- `RuleBasedJudge` is deterministic; no LLM Judge seam is exercised yet.
- `Router` optimizes only by cost; coverage/freshness/previous-failure signals are
  modeled in `ToolCapability` but not scored.
- No logging/observability layer.
- `RunResult` exposes internal diagnostics; only `ResponsePackage` is the Response
  Agent contract. Nothing currently enforces that at the type boundary beyond
  convention and tests.

## Architecture deviations

- The Orchestrator runs in memory; there is no `Checkpoint` implementation yet. This
  is documented in ADR 0005, not a silent divergence.
- `evidence_purpose` and `min_row_count` were added to `ArtifactRequirement`, and
  `optional_data_keys` to `ArtifactDescriptor`. These are additive, defaulted fields
  required for contextual judging. They do not change Initial Requirement semantics.
- No conflict with any confirmed blog decision was discovered. Blog schemas beyond
  those implemented remain designs, not evidence.

## ADRs added

- `docs/adr/0002-contextual-artifact-assessment.md`
- `docs/adr/0003-planner-terminal-latch.md`
- `docs/adr/0004-read-only-sql-ast-guard.md`
- `docs/adr/0005-state-services-and-deferred-persistence.md`

(ADR 0001 pre-existed from milestone 1.)

## Database / migration status

No operational database, no migrations, no schema. No analytics database or Parquet
file was read, written or modified in this session. `docker-compose.yml` still only
defines the analytics PostgreSQL; an Operational PostgreSQL is not added yet.

## Security audit status

- `scripts/secret_scan.py` current-tree scan passes (exit 0).
- Known historical exposure from milestone 1 remains documented but unchanged:
  `SECRET_REDACTED` in legacy files and local history. This session did not add or
  remove credentials and did not rewrite history.
- No live `DEEPSEEK_API_KEY`/`POSTGRES_PASSWORD` was used; no paid API call was made.
- Read-only guard added and tested; adapters remain fail-closed.
- User action still required: rotate/revoke the credentials flagged in milestone 1.

## Current blockers

- **Push blocked**: this environment has no GitHub credential (`git ls-remote origin`
  fails with "could not read Username"). Commits are local only. The next agent with
  credentials must push `agent/deepseek-implementation` and never push the old
  credential-bearing ancestry.
- Live execution blocked on a running read-only PostgreSQL/DuckDB target.

## Exact next task

Ticket 02 remainder: wire `app/validation/sql_guard.py` into the read-only adapters.

Start from failing tests in a new `tests/test_tool_execution.py` (create it) that
assert:

1. `query_local_hot_db` validates through `guard_read_only_sql` and executes only
   allowlisted tables with a `SELECT` under a bounded `LIMIT`.
2. `query_local_cold_parquet` requires `file_root=settings.parquet_archive_path` and
   rejects paths outside it.
3. A `retryable` connection error is redacted and surfaced without connection details;
   0 rows is `EMPTY`, not an error.
4. A mutation or forbidden function never opens a connection.

Relevant modules: `app/validation/sql_guard.py`, `app/tools/postgres.py`,
`app/tools/duckdb.py`, `app/config.py`, `app/agent/executor.py` (`ToolResult`).

Do not:

- restore unrestricted execution or the legacy LLM loop,
- relax the read-only boundary,
- push the historical credential-bearing ancestry.

## Recommended Codex review priorities

1. `ArtifactAssessment` hard-failure veto: contract validator and service coercion.
2. `PlannerTerminalLatch` fingerprint and the orchestrator `NO_PROGRESS` stop — look
   for any path that can re-invoke planning without a new artifact.
3. State derivation correctness (PARTIAL vs UNSATISFIED, COMPLETE optional gaps).
4. `ResponsePackage` leakage: confirm no rejected evidence/attempt history can enter it.
5. `guard_read_only_sql` bypasses: dialect edge cases, nested table functions, CTEs,
   `exp.DDL`/`exp.DML` subclass coverage, path normalization.
6. Router precedence: confirm preference never overrides policy or a user hard source
   constraint.
