# Development status

Status: IN_PROGRESS

Last updated by: Shared Knowledge session (milestone 17 — persistent MLB domain knowledge
base). Earlier milestones preserved.

## Current phase

Milestone 3 — real safe analytical tool execution: **delivered (code level)**. SQL is
validated before any connection, PostgreSQL runs read-only with a statement timeout,
DuckDB paths are sandboxed, results are row-bounded and errors are redacted. No
unguarded analytical path remains.

Milestone 4 — persistence foundation: **delivered (service level)**. Operational store,
filesystem artifact storage, checkpoint coordinates and an idempotent resume slice.
Not yet wired into the Orchestrator.

Milestone 5 — Shared Context minimal slice: **delivered**. Deterministic reference
retrieval (`ContextRequest` → `ContextPackage`) with structural exclusion of failed,
rejected and superseded history. No vector search, embeddings or Retrieval Agent.

Milestone 6 — persistence wiring and context boundaries: **delivered**. The
Orchestrator records products in safe order, takes checkpoints at `PLAN_ACCEPTED`,
`ARTIFACT_ASSESSED`, `PLANNER_TERMINAL` and `FINALIZATION`, resumes from a checkpoint
reusing artifacts without re-execution, and passes bounded knowledge to the Planner and
to the Response only. The safe branch is published.

Milestone 7 — metric and schema registries with run-scoped context: **delivered**.
`MetricDefinition`/`SourceMapping` + a deterministic `MetricRegistry`, a
`SchemaRegistry` with `SchemaTable`, both exposed through `ContextSource`
(`MetricRegistrySource`, `SchemaRegistrySource`), and exact-match run scoping so one
run's private context cannot leak into another. No Retrieval Agent.

Milestone 8 — semantic normalization, requirement decomposition and adequacy:
**delivered**. Constraint authority, canonical entity resolution + clarification,
deterministic objective extraction, a Requirement Decomposer producing immutable
INITIAL requirements, QualificationRule/SampleAdequacyRule, and a LeagueStateSnapshot
separating official progress from local coverage. ADR 0011.

Milestone 9 — Feature Engine metric artifacts and Source Mapping execution:
**delivered**. `FeatureEngine` emits FEATURE artifacts with lineage; `SourceMappingResolver`
turns required data keys into DIRECT / CALCULATED / NO_MAPPING routes that the Router
consumes as a capability constraint. ADR 0012.

Milestone 10 — AgentReport, explicit state transitions and dependency-ordered review:
**delivered**. `AgentReport` reference envelope, `StateTransition` + `StateTransitionLog`
(continuity + monotonic version), and `ReportReviewer` that DEFERs reports with
unresolved prerequisites instead of applying arrival order. ADR 0013.

Milestone 11 — LLM implementations behind the Protocols: **delivered**.
Provider-agnostic `ModelProvider` + `FakeModelProvider`, environment-driven per-agent
models, versioned `PromptTemplate`s, and `LLMPlanner`/`LLMJudge`/`LLMResponseComposer`
with deterministic fallbacks. Structured output is validated; the Judge never overrides
a hard failure; the Planner cannot invent requirements. ADR 0014.

Milestone 12 — observability and evaluation: **delivered**. Redacted `RunEvent`/`RunMetrics`
and `RunSummary`/`RunEvaluation` with completion/replan/retry/failure rates. ADR 0015.

Milestone 13 — Operational PostgreSQL store: **delivered (code)**. `SqlOperationalStore`
shares all logic across dialects; `PostgresOperationalStore` accepts an injected DB-API
connection. UNVERIFIED_LIVE. ADR 0016.

Milestone 14 — pipeline, CLI, E2E and usage docs: **delivered**. `AnalysisPipeline`
composes the full flow, `SyntheticDataTool` makes it runnable offline, `app/cli.py` exposes
ask/resume/inspect/show-artifact/metrics, an end-to-end integration test covers the
accepted-product boundary, and `README.md` + `docs/usage/` + `docs/development/` document
install, configure, run, resume and extension. A cross-objective ResponsePackage leak was
found and fixed. ADR 0017.

Milestone 15 — Web evidence extraction: **delivered (stage)**. `RawWebResult` →
`EvidenceExtractor` → structured `Evidence` → unified EVIDENCE artifact with lineage;
deterministic and LLM extractors behind one Protocol. Not wired to a live web tool yet.
ADR 0018.

Milestone 16 — adversarial test battery: **delivered**. `tests/security/test_adversarial.py`
covers planner invariants, CTE-hidden SQL mutations, accepted-evidence exclusion, resume
integrity with a missing artifact, bounded planning, and context history isolation.

Milestone 17 — persistent Shared Knowledge base: **delivered**. `app/models/knowledge.py`
contracts; a versioned `KnowledgeStore` (SQLite dev + PostgreSQL `knowledge` schema) with
snapshots and relations; a source registry with an authority ladder; validate → stage →
activate ingestion with supersession; deterministic retrieval (canonical/alias/filter/
token/relations) with authority/freshness/temporal ranking; live refresh from the MLB Stats
API and the official OBR PDF; `KnowledgeContextSource` wired into the Orchestrator; entity
dictionary and metric registry projections; a `knowledge` CLI; and committed seed packs for
reference data, rules, glossary, players and community. ADR 0019.

Next phase: live DB/web wiring (UNVERIFIED_LIVE), a recheck of the unverified community
profiles, and a final architecture-compliance review by Codex.

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

Milestone 5 (ADR 0008):

- `app/context/service.py`: `ContextRequest`, `ContextItem`, `ContextPackage`,
  `ContextSource` Protocol, `StaticContextSource`, `ContextService`. Deterministic
  filtering/ranking with structural exclusion of attempts, routing, drafts, judge
  reasoning, rejected evidence, unused RAG and (for Response) superseded plans.

Milestone 6 (ADR 0009):

- `ToolResult`/`ExecutionOutcome` carry an optional payload; the Orchestrator writes it
  through `RunRecorder`.
- `Orchestrator` accepts optional `RunRecorder` and `ContextService`. Safe record order
  (payload → artifact metadata → assessment → requirement states → objective state →
  execution references → checkpoint); payload failure aborts before any state.
- Checkpoints at `PLAN_ACCEPTED`, `ARTIFACT_ASSESSED`, `PLANNER_TERMINAL`, `FINALIZATION`.
- `ResumeService.rehydrate` + `Orchestrator.run(..., restored=...)` reuse persisted
  artifacts and preserve a terminal Planner decision with zero re-execution.
- Planner receives bounded `purpose=PLANNER` context plus execution summary; Response
  receives accepted evidence and `purpose=RESPONSE` critical knowledge only.

Milestone 7 (ADR 0010):

- `app/models/metrics.py`: `MetricDefinition`, `SourceMapping`.
- `app/semantic/metric_registry.py`: `MetricRegistry` (exact lookup + deterministic
  token search; rejects duplicates and unknown mappings).
- `app/models/schema.py` + `app/semantic/schema_registry.py`: `SchemaTable` and
  `SchemaRegistry` (deterministic table/column lookup).
- `app/context/registry_source.py`: `MetricRegistrySource` (METRIC) and
  `SchemaRegistrySource` (SCHEMA) behind `ContextSource`.
- Run scoping: `ContextItem.scope_run` / `ContextRequest.run_id` with exact-match
  filtering; global knowledge visible to all runs.

Milestone 8 (ADR 0011):

- Constraint `origin` extended and `authority` added with precedence; `_Constraint`
  derives authority from origin. `app/semantic/constraints.py` normalizes with
  precedence.
- `app/models/entities.py` + `app/semantic/entity_resolver.py`: canonical entities,
  alias/nickname resolution, clarification on ambiguity.
- `app/models/clarification.py`, `app/models/semantic.py`: clarification and
  `SemanticResult` contracts.
- `app/semantic/objective_extractor.py` + `app/semantic/normalizer.py`:
  raw query → `AnalysisObjective[]` with entities/constraints.
- `app/semantic/requirement_decomposer.py`: Objective → semantic-atomic INITIAL
  `ArtifactRequirement[]`.
- `app/models/contracts.py`: `QualificationRule`, `SampleAdequacyRule`,
  `LeagueStateSnapshot`; `ArtifactRequirement` gains both rule fields;
  `RequirementState`/`ObjectiveState` enriched.
- `app/assessment/adequacy.py`: sample adequacy, qualification and league coverage
  signals; `validate_artifact` accepts an optional league state.

## In progress

Nothing is partially edited.

## Not started

- An Operational PostgreSQL implementation of `OperationalStore`; the local SQLite
  store is the current, replaceable implementation.
- A Source Mapping execution layer (using `SourceMapping` to actually execute reads)
  and a deterministic Feature Engine emitting metric Artifacts with lineage.
- An `AgentReport` envelope and explicit state-transition contracts (reason/trigger).
- A Web `RawWebResult` → Evidence Extractor path.
- LLM Planner/Judge/Response/Semantic implementations behind the existing Protocols,
  plus prompt versioning.
- Observability and evaluation metrics.
- Live integration test against a disposable PostgreSQL and synthetic Parquet.
- Embeddings / pgvector (deliberately deferred).

## Current branch

`agent/shared-knowledge`, branched from `agent/deepseek-implementation-safe` (6675ddd).
`agent/deepseek-implementation-safe` remains the baseline and is not overwritten. A WIP
security-hardening change found uncommitted on local `codex/review-hardening` was preserved
with `git stash push -u` (stash@{0}) and is not part of this branch.

## Latest meaningful commit

- Branch `agent/deepseek-implementation-safe` is published and tracks
  `origin/agent/deepseek-implementation-safe`.
- Latest pushed commit: `01fe20cf799c2ccbf627d018180ce0d3028a587d` (documentation
  commits created after this push are pushed again immediately).
- `main` is not touched; the old `agent/deepseek-implementation` branch is never pushed.

## Test command

`python3 -m unittest discover -s tests -v`

## Tests passing

297 tests, all passing (`Ran 297 tests ... OK`). Milestone 17 adds `tests/knowledge/`:
store (11), retrieval (8), ingestion (6), domains (48), CLI + refresh (7).

`python3 -m compileall` passes. `python3 scripts/secret_scan.py` passes (exit 0).

## Tests failing

None.

## Known bugs

- The tool executors and the Operational PostgreSQL store have not run against a real
  database or real Parquet. Marked UNVERIFIED_LIVE.
- `sqlglot` emits a parse warning for `LOAD` before classifying it as a forbidden
  `Command`; behavior is correct but the warning is noisy.
- `AnalysisPipeline` does not yet wire `SourceMappingResolver` per task; the Router still
  selects by capability. Source Mapping is tested in isolation.
- Fixed in milestone 14: `ResponsePackage`/`CompletionReport` leaked another objective's
  accepted evidence when services were shared; now scoped by `objective_ref`.

## Technical debt

- No live-source integration; the loop is proven only with deterministic tools and fakes.
- `RuleBasedPlanner` and `RuleBasedJudge` remain intentionally simple.
- `Router` optimizes only by cost; coverage/freshness are not scored.
- `RunMetrics` is not yet emitted by the Orchestrator, and there is no metrics sink.
- LIMIT wrapping changes the executed SQL for unbounded reads; verify against a real
  engine and decide whether to require an explicit LIMIT instead.
- `SourceMappingResolver` is not wired into the Orchestrator per task.
- A live Web tool is not wired through the Evidence Extractor.
- No RAG knowledge base, persistent Entity Dictionary, or pgvector (all deferred).

## Architecture deviations

- `main.py` was previously an unguarded raw DuckDB read; it is now guarded. Documented.
- History was reconstructed (see Current branch). No code or domain behavior changed.
- No conflict with any confirmed blog decision was discovered.

## ADRs added

- `docs/adr/0019-shared-knowledge-base.md` (milestone 17).
- `docs/adr/0006-guarded-tool-execution.md` (milestone 3).
- `docs/adr/0007-operational-stores-and-checkpoints.md` (milestone 4).
- `docs/adr/0008-shared-context-retrieval.md` (milestone 5).
- `docs/adr/0009-orchestrator-persistence-and-context-boundaries.md` (milestone 6).
- `docs/adr/0010-metric-registry-and-run-scoped-context.md` (milestone 7).
- `docs/adr/0011-semantic-normalization-and-decomposition.md` (milestone 8).
- `docs/adr/0012-feature-engine-and-source-mapping.md` (milestone 9).
- `docs/adr/0013-agent-report-and-state-transitions.md` (milestone 10).
- `docs/adr/0014-llm-protocols-and-prompts.md` (milestone 11).
- `docs/adr/0015-observability-and-evaluation.md` (milestone 12).
- `docs/adr/0016-operational-store-dialect.md` (milestone 13).
- `docs/adr/0017-pipeline-cli-and-usage-docs.md` (milestone 14).
- `docs/adr/0018-evidence-extraction.md` (milestone 15).
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

- No GitHub blocker: `gh auth` is configured and the safe branch is pushed.
- Live DB integration blocked on a running read-only PostgreSQL/DuckDB target.

## Exact next task

Milestone 18 — finish the knowledge refresh surface and the remaining runtime wiring, TDD.

1. Recheck the community source directory: re-run the reachability/activity sweep when the
   network is stable, and upgrade the `UNVERIFIED` profiles (or mark them inactive) with a
   fresh `last_checked`. Add a `knowledge refresh community --verify` path if useful.
2. Improve the live refresh: parse the OBR PDF's rule titles (not only rule numbers) and
   diff them against stored rule items, reporting added/renamed sections.
3. Wire `SourceMappingResolver` into the `Orchestrator` per task (`execution_route`), then
   wire the Web tool through the `EvidenceExtractor` into an `EVIDENCE` artifact.
4. Emit `RunMetrics`/`RunSummary` from the Orchestrator loop and expose them on `RunResult`.
5. Attempt a live read-only PostgreSQL/DuckDB integration test and a live
   `knowledge refresh teams|rules` run; otherwise keep UNVERIFIED_LIVE with a manual procedure.

Do not: change the frozen architecture; turn Shared Knowledge into a retrieval agent; let
community knowledge override official facts; print or persist credentials.

## Recommended Codex review priorities

1. Guard-before-connect: prove no executor path can connect on rejected SQL.
2. Redaction completeness in `redact_secrets` and every `ToolResult`, metric and LLM error path.
3. `guard_read_only_sql` bypasses: nested table functions, CTE-hidden mutations, dialect
   spellings, schema-qualified allowlist behavior, `LOAD`.
4. LIMIT-wrapper correctness on real PostgreSQL and DuckDB.
5. Persistence: artifact-before-state ordering, checkpoint version refs, idempotent
   resume, immutability of `LocalFilesystemArtifactStorage`, zero duplicate executions.
6. Context boundaries: no attempt/rejected/plan/judge-reasoning item reaches the Planner
   or Response; run-scoped isolation.
7. `PlannerTerminalLatch` and `NO_PROGRESS` termination; no re-invocation after terminal.
8. `ResponsePackage`/`CompletionReport` objective scoping (the milestone-14 leak class).
9. LLM structured output validation: Planner cannot invent/edit requirements; Judge
   cannot override a hard failure.
10. History safety: confirm no push includes the old credential-bearing ancestry.
