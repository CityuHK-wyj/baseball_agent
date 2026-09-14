# Codex Handoff

This file lets the next agent continue without the prior chat. Read it after
`AGENTS.md`, `CONTEXT.md`, `docs/development-status.md` and the ADRs.

## Repository State

- branch: `agent/deepseek-implementation-safe` (history-reconstructed; the old
  `agent/deepseek-implementation` must not be pushed)
- root commit: clean import of the verified-safe milestone-1 tree (`6ccb2ad`)
- tests: `python3 -m unittest discover -s tests -v` → 242 passing
- secret scan: current tree exit 0; all commits reachable from the safe branch have
  0 real findings (verified by scanning every blob)
- remote: `https://github.com/CityuHK-wyj/baseball_agent.git`; published as
  `origin/agent/deepseek-implementation-safe`
- latest pushed commit: `01fe20cf799c2ccbf627d018180ce0d3028a587d`; `main` is untouched and
  the old `agent/deepseek-implementation` branch is never pushed

## What DeepSeek Implemented

Milestone 15 (this session) — Web evidence extraction (ADR 0018):

- `app/models/evidence.py`, `app/semantic/evidence.py` (deterministic extractor +
  `evidence_to_artifact`), `app/llm/evidence.py` (+ `EVIDENCE_PROMPT`).
- Tests: `tests/test_evidence.py` (4), `tests/llm/test_evidence.py` (4).

Milestone 14 (previous session) — pipeline, CLI, E2E and usage docs (ADR 0017):

- `app/tools/synthetic.py` (`SyntheticDataTool`), `app/pipeline.py` (`AnalysisPipeline`),
  `app/cli.py`, `tests/integration/test_end_to_end.py`.
- `README.md` rewritten; `docs/usage/*` and `docs/development/*` added.
- Fixed a cross-objective accepted-evidence leak in `ResponsePackage`/`CompletionReport`.

Milestone 13 (previous session) — Operational PostgreSQL store (ADR 0016):

- `app/persistence/store.py`: `SqlOperationalStore` base + `SqliteOperationalStore` +
  `PostgresOperationalStore` (injected connection). UNVERIFIED_LIVE.
- Tests: `tests/persistence/test_postgres_store.py` (7).

Milestone 12 (previous session) — observability + evaluation (ADR 0015):

- `app/observability/metrics.py` (redacted `RunEvent`/`RunMetrics`),
  `app/observability/evaluation.py` (`RunSummary`/`RunEvaluation`).
- Tests: `tests/observability/` (7).

Milestone 11 (previous session) — LLM Protocols + prompts (ADR 0014):

- `app/llm/provider.py` (ModelProvider, FakeModelProvider), `app/llm/prompts.py`
  (versioned PromptTemplate), `app/llm/parsing.py`, `app/llm/openai_provider.py`,
  `app/llm/planner.py`, `app/llm/judge.py`, `app/llm/response.py`.
- Per-agent `*_MODEL` configuration; API keys env-only.
- Tests: `tests/llm/` (21).

Milestone 10 (previous session) — AgentReport, state transitions, review order (ADR 0013):

- `app/models/report.py` (`AgentReport`, `DOMAIN_OWNERSHIP`), `app/models/transition.py`
  (`StateTransition`), `app/agent/review.py` (`ReportReviewer`, `ReviewOutcome`,
  `StateTransitionLog`).
- Tests: `tests/test_state_transition.py` (5), `tests/test_agent_report.py` (3),
  `tests/test_report_review.py` (6).

Milestone 9 (previous session) — Feature Engine + Source Mapping execution (ADR 0012):

- `app/features/metrics.py`: `FeatureEngine` → FEATURE artifact with lineage/provenance.
- `app/agent/source_mapping.py`: `SourceMappingResolver` → DIRECT / CALCULATED / NO_MAPPING.
- `Router.route(..., execution_route=...)` consumes the route as a capability constraint.
- Tests: `tests/test_feature_engine.py` (5), `tests/test_source_mapping.py` (7).

Milestone 8 (previous session) — semantic normalization, decomposition and adequacy (ADR 0011):

- Constraint `origin` extended + `authority` with precedence; `app/semantic/constraints.py`.
- `app/models/entities.py` + `app/semantic/entity_resolver.py`: canonical entities,
  aliases/nicknames, clarification on ambiguity.
- `app/models/clarification.py`, `app/models/semantic.py`, `app/semantic/normalizer.py`,
  `app/semantic/objective_extractor.py`.
- `app/semantic/requirement_decomposer.py`: Objective → semantic-atomic INITIAL
  `ArtifactRequirement[]`.
- `QualificationRule`, `SampleAdequacyRule`, `LeagueStateSnapshot`; `app/assessment/adequacy.py`.
- Tests: `tests/semantic/` (24) + `tests/test_adequacy.py` (5).

Milestone 7 (previous session) — metric/schema registries and run-scoped context (ADR 0010):

- `app/models/metrics.py`: `MetricDefinition`, `SourceMapping`.
- `app/semantic/metric_registry.py`: `MetricRegistry` (exact lookup + deterministic
  token search). `app/models/schema.py` + `app/semantic/schema_registry.py`:
  `SchemaTable`, `SchemaRegistry`.
- `app/context/registry_source.py`: `MetricRegistrySource` (METRIC) and
  `SchemaRegistrySource` (SCHEMA) behind `ContextSource`.
- Run scoping: `ContextItem.scope_run` / `ContextRequest.run_id` exact-match isolation.
- Tests: `tests/test_metrics.py` (4), `tests/test_schema_registry.py` (4),
  `tests/context/test_registry_and_isolation.py` (5), plus run-id plumbing in
  `tests/context/test_context_wiring.py`.

Milestone 6 (previous session) — persistence wiring and context boundaries (ADR 0009):

- `ToolResult`/`ExecutionOutcome` carry an optional payload; `Orchestrator` accepts
  optional `RunRecorder` + `ContextService` and records in safe order (payload →
  artifact → assessment → states → execution refs → checkpoint).
- Checkpoints at `PLAN_ACCEPTED`, `ARTIFACT_ASSESSED`, `PLANNER_TERMINAL`, `FINALIZATION`.
- `ResumeService.rehydrate` + `Orchestrator.run(..., restored=...)`: artifacts reused,
  terminal decision preserved, zero duplicate executions.
- Planner gets a bounded `purpose=PLANNER` context package + execution summary; Response
  gets accepted evidence and `purpose=RESPONSE` critical knowledge only.
- Tests: `tests/persistence/test_orchestrator_persistence.py` (5),
  `tests/context/test_context_wiring.py` (3).

Milestone 5 (previous session) — Shared Context minimal slice (ADR 0008):

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

- An Operational PostgreSQL implementation of `OperationalStore`; SQLite is the local,
  replaceable first version.
- A Source Mapping execution layer (the `SourceMapping` contract exists but the Router
  does not consume it yet).
- Embeddings/pgvector, deliberately deferred.
- A live integration test against a real analytical database/Parquet fixture.
- LLM Planner / LLM Judge / Response generation. Only deterministic implementations
  exist; the Protocol seams are there but untested against a model.
- LLM Planner / LLM Judge / Response generation / LLM semantic extraction. Only
  deterministic implementations exist; the Protocol seams are there but untested
  against a model.
- Metric Registry / Schema Registry execution wiring and Source Mapping execution.
- Logging/observability, evaluation metrics, prompt versioning.
- Cross-run context isolation at the Orchestrator level (service-level covered).

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
3. `docs/adr/0012-feature-engine-and-source-mapping.md`, `0011-semantic-normalization-and-decomposition.md`, `0010-metric-registry-and-run-scoped-context.md`, `0009`, `0008`, `0007`, `0006`, `0002`–`0005`
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

- Persistence is wired: payload-before-state ordering, checkpoints at four positions,
  and resume that reuses artifacts with zero duplicate executions (deterministic tools).
- `SqliteOperationalStore` is single-writer and not concurrency-tested.
- `record_requirement_states` rewrites every requirement each round; version bumps are
  meaningful but the round-trip cost grows with requirement count.
- Checkpoint `state_version_refs` reference requirement/objective state versions only,
  not artifact versions or assessment versions.

## Context / RAG Concerns

- `ContextService` is wired into both boundaries: Planner gets `purpose=PLANNER`
  bounded knowledge + summaries; Response gets accepted evidence and `purpose=RESPONSE`
  critical knowledge only.
- `MetricRegistrySource`, `SchemaRegistrySource` and `StaticContextSource` are the
  sources; no RAG source yet. No embeddings or pgvector, deliberately.
- Run scoping (`scope_run`/`run_id`) gives exact-match isolation; it is not an
  authorization boundary.
- Exclusion policy (`_ALWAYS_EXCLUDED`, `_RESPONSE_ONLY_EXCLUDED`) is enforced in the
  service; the wiring tests assert attempts/rejected/plan never reach either boundary.
- No cross-run context isolation test yet.

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

Milestone 17 — wire the remaining stages into the runtime, per
`docs/development-status.md` → "Exact next task":

1. Wire `SourceMappingResolver` into the `Orchestrator` (per-task `ExecutionRoute` passed
   to `Router.route(execution_route=...)`) and test DIRECT/CALCULATED/NO_MAPPING end to end.
2. Wire the Web tool through `EvidenceExtractor` into an `EVIDENCE` artifact (fake fetcher).
3. Emit `RunMetrics`/`RunSummary` from the Orchestrator loop into `RunResult`.
4. Attempt a live read-only PostgreSQL/DuckDB integration test; otherwise keep it
   UNVERIFIED_LIVE with a documented manual procedure.

Then a final Codex architecture-compliance review. Do not change the frozen architecture,
let the Planner touch physical mappings, or print/persist credentials.
