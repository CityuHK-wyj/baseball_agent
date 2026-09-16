# Codex Handoff

## Real data layer checkpoint — 2026-09-16 (post-migration + Parquet rebuild)

Branch `pi/analytics-integration`; 380 tests pass, compileall and secret scan pass.
The ingestion gap is resolved for BOTH sources: `sz_top`/`sz_bot`/`p_throws` are retained.
PostgreSQL 2024–2026 reloaded (2,196,186 rows, 2024-03-15..2026-09-14) and the historical
Parquet archive rebuilt (6,168,817 rows, 2015-04-05..2023-11-01). Batter-relative upper
edge (`plate_z >= sz_top - 0.25 ft`) is `EXACTLY_SUPPORTED` on both; historical
batter-relative E2E is `LIVE_VERIFIED`. `player_dictionary` backfilled to 100% batter-name
coverage. Zone orientation corrected (upper third = zones 1-3). Routing is coverage-aware,
ranking aggregation (AVG/MAX) explicit, and year-vs-year / recent-vs-previous windows run
as separate frozen objectives. `baseball_readonly` remains strictly read-only.

Deferred (by scope freeze): `current period vs same period last year` multi-window,
generalized temporal NLP, and any non-analytics product features.

## Date planning checkpoint — 2026-09-16

343 tests pass. Relative day windows (inclusive), single calendar years and ISO date
ranges are frozen during normalization and propagated to core requirements. Optional
historical baselines do not inherit the recent window. Recovery retains the original
window. Ambiguous multi-window queries fail closed; date clarification UI is not done.
Live probes ran in the requested order: PostgreSQL now reaches authentication but needs
a password, Parquet bounded read passes, high-zone query lacks sz_bot/sz_top, MLB teams
transport passes. Complete current-data/Web Evidence is still UNVERIFIED_LIVE.
Exact next task: typed complex analytics planning and read-only adapter wiring; do not
silently reinterpret high-zone semantics to fit available columns.

## Crash recovery checkpoint — 2026-09-16

341 tests pass, including actual process exit/restart. `resume_run` / CLI `resume --execute`
recover persisted intent, reuse durable executions and restore objective-scoped assessments
and state. Immutable run/initial definitions survive redecomposition and restart. Atomic
execution claims prevent duplicate tool calls; uncertain in-flight calls fail closed with
`EXECUTION_UNCERTAIN`. These cannot be safely retried without external reconciliation.
Compileall, secret scan and the updated separate-process CLI workflow pass.

Exact next task: analytics/date planning and configured read-only data execution. Keep
PostgreSQL, complete high-zone analytics and current Web evidence UNVERIFIED_LIVE until
the requested ordered live probes actually succeed. Architecture and protected branches
remain unchanged. The previous missing post-consumption recovery path is now implemented.

## Prior integration checkpoint — 2026-09-16

- Active branch: `astra/v0.1-integration`, based on runtime `b68ccf7`, merged knowledge
  `7d44ef44ec88113a9306aab03881197dc7a115cf`; no main merge.
- Status: **IN_PROGRESS — stable checkpoint**; 335 tests pass, compileall and secret scan
  pass, and `python3 scripts/verify_v01_workflows.py` passes.
- Completed: request expiry/scope/replay protection, source constraint revision,
  default composition and CLI answer, canonical knowledge projections, bounded
  Planner/Judge/Response knowledge, durable metrics, default Web provider seam,
  cross-run Planner isolation, per-objective reports, persisted payload references.
- Live: local Parquet bounded read verified; MLB teams API transport succeeded once but
  subsequent requests failed. PostgreSQL refused connection, no operational PG configured;
  high-zone and current-data analytics remain UNVERIFIED_LIVE.
- Exact next task: persist the run's immutable initial definitions and execution intent,
  then add process-crash recovery after interaction consumption without duplicate execution.
  Generic multi-objective resume must select the right objective instead of the first row.
- Then wire configured analytics providers and temporal planning; the high-zone archive
  query lacks sz_bot/sz_top, and the player seed has no Hernandez candidates (fixtures do).
- Preserve both source branches, `codex/review-hardening` and `stash@{0}`. Do not add RAG or
  change the frozen architecture. `python3` works; the repo `.venv` lacks pydantic.
- Read [v0.1 validation](usage/v01-validation.md) and
  [composition notes](development/v01-integration.md) for exact paths, seams and commands.

## Historical handoff (superseded by the checkpoint above)

## Runtime audit update (2026-09-15)

- audit branch: `gpt56/runtime-audit-hardening`, latest pushed audit checkpoint `5e84f01`
- verification: 254 tests passing; `compileall app` and current-tree secret scan pass
- fixed findings: SourceMapping runtime and mapped-tool enforcement, cross-objective evidence
  isolation, web evidence runtime seam, all-field metric redaction and emission, DuckDB path
  bypasses, grounded LLM evidence, closed LLM Planner output schema, and persisted terminal
  reopening after a new permitted source. Clarification now persists a `WAITING_FOR_USER`
  interaction and resumes the same run with a `USER_CONFIRMED` constraint.
- remaining: live integrations are UNVERIFIED_LIVE; permission and constraint-renegotiation
  workflows are not fully implemented. Paid/high-cost permission is now checkpointed, scoped
  to one tool, and cannot override system policy; constraint renegotiation remains open. See
  `docs/gpt56-runtime-audit.md`.

This file lets the next agent continue without the prior chat. Read it after
`AGENTS.md`, `CONTEXT.md`, `docs/development-status.md` and the ADRs.

## Repository State

- branch: `agent/shared-knowledge` (branched from `agent/deepseek-implementation-safe`);
  the baseline branch remains intact and is not overwritten
- note: an uncommitted SQL-hardening WIP on local `codex/review-hardening` (1 failing test)
  was preserved via `git stash push -u` (stash@{0}) and is not part of this branch
- tests: `python3 -m unittest discover -s tests -v` → 299 passing
- secret scan: current tree exit 0
- remote: `https://github.com/CityuHK-wyj/baseball_agent.git`

## What This Session Implemented

Milestone 17 — persistent Shared Knowledge base (ADR 0019):

- `app/models/knowledge.py`: `KnowledgeSource`, `KnowledgeItem`, `KnowledgeRelation`,
  `KnowledgeSnapshot`, `KnowledgeQuery`, `KnowledgeMatch`, `KnowledgeDiff` with authority,
  status, effective dates, `as_of`, freshness policy, verification status and version.
- `app/knowledge/store.py`: versioned `KnowledgeStore` — SQLite dev tables plus a
  `PostgresKnowledgeStore` that writes a dedicated `knowledge` schema (never
  `baseball_analytics`). Item history, relations and snapshots included.
- `app/knowledge/{freshness,registry,retrieval,ingestion,refresh,fetch,loader,service,entities}.py`:
  freshness policies, source registry with an authority ladder, deterministic retrieval,
  validate → stage `COLLECTED` → activate with supersession, live refresh from the MLB
  Stats API and the official OBR PDF, a `KnowledgeBase` facade, and entity/metric projections.
- `app/context/knowledge_source.py`: `KnowledgeContextSource` behind the existing
  `ContextSource` seam; `KNOWLEDGE` added to `Orchestrator.KNOWLEDGE_KINDS`.
- `app/cli.py`: `knowledge status|sources|search|show|refresh|validate`.
- `knowledge/sources/*.json` + `knowledge/seed/*.json`: committed source manifests and
  packs — reference (30 teams, 30 ballparks, divisions, leagues), rules (2026 OBR structure
  + concepts + transaction rules), glossary (metrics/Statcast/pitch/discipline/scouting),
  players (118 profiles + awards), context (postseason structure + historical eras) and
  community (51 creators).
- Tests: `tests/knowledge/` (store, retrieval, ingestion, domains, CLI + refresh).
- Docs: `docs/adr/0019-shared-knowledge-base.md`, `docs/development/shared-knowledge.md`,
  `docs/usage/knowledge-base.md`, README section, matrix section K2.

Live verification performed 2026-09-15: MLB Stats API (30 teams, divisions, venues) and
`2026-official-baseball-rules.pdf`. A community reachability sweep verified a subset of
sources; the rest are marked `UNVERIFIED`.

## Prior Session (DeepSeek)

Milestone 15 — Web evidence extraction (ADR 0018):

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
