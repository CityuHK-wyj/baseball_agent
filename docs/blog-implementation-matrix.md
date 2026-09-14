# Blog implementation matrix

Maps every confirmed blog decision/requirement to its implementation status in this
repository. Source: `content/knowledge/{current-state,decisions,project-state,sources}`
and posts 01–09 of `CityuHK-wyj/cityuhk-wyj.github.io` as of 2026-09-14.

Status values:

- **IMPLEMENTED** — code plus tests exist on `agent/deepseek-implementation-safe`.
- **PARTIAL** — some code exists; a named gap remains.
- **MISSING** — confirmed requirement with no implementation yet.
- **NOT_REQUIRED** — deliberately not implemented; reason recorded.
- **DEFERRED** — open question consciously postponed; reason recorded.

This file is maintained as implementation proceeds. It is the evidence for
"which blog designs are actually implemented", not a wish list.

## A. Core domain contracts (O001, D005, D013, D014, D036)

| Requirement | Status | Implementation | Tests | Notes |
| --- | --- | --- | --- | --- |
| `AnalysisObjective` definition | IMPLEMENTED | `app/models/contracts.py` | `tests/test_domain.py` | id, raw_query, type, subtype, constraints |
| `ObjectiveState` separate from definition | IMPLEMENTED | `app/models/contracts.py`, `app/state/services.py` | `tests/test_state.py` | Created with definition |
| `ArtifactRequirement` definition | IMPLEMENTED | `app/models/contracts.py` | `tests/test_domain.py` | |
| `RequirementState` separate from definition | IMPLEMENTED | `app/models/contracts.py`, `app/state/services.py` | `tests/test_state.py` | |
| Shared `ArtifactDescriptor` (D036) | IMPLEMENTED | `app/models/contracts.py` | `tests/test_artifacts.py` | type/entities/keys/time/constraints/grain/population |
| Canonical `Entity` (D004) | IMPLEMENTED | `app/models/contracts.py`, `app/models/entities.py`, `app/semantic/entity_resolver.py` | `tests/semantic/test_entity_resolver.py` | Canonical key + alias/nickname resolution |
| Typed `Constraint` (D014) | IMPLEMENTED | `app/models/contracts.py` | `tests/test_domain.py`, `tests/semantic/test_constraints.py` | Numeric/Category; origin + authority |
| Constraint authority levels (D026) | IMPLEMENTED | `app/models/contracts.py`, `app/semantic/constraints.py` | `tests/semantic/test_constraints.py` | SYSTEM_POLICY > USER_CONSTRAINT > USER_PREFERENCE > INFERRED_DEFAULT |
| `AgentTask` / `TaskExecution` / `TaskAttempt` (D006, D007) | IMPLEMENTED | `app/models/planning.py` | `tests/test_executor.py` | |
| `Artifact` immutable + provenance + lineage (D010, D037) | IMPLEMENTED | `app/models/artifacts.py` | `tests/test_artifacts.py` | |
| `ArtifactAssessment` contextual (D048, D059) | IMPLEMENTED | `app/models/artifacts.py`, `app/assessment/` | `tests/test_artifacts.py` | |
| `AgentReport` envelope (P005) | IMPLEMENTED | `app/models/report.py` | `tests/test_agent_report.py` | Reference envelope; results stay separate |
| `Checkpoint` coordinate (D054) | IMPLEMENTED | `app/models/checkpoint.py` | `tests/persistence/` | |
| `ContextPackage` (O002) | IMPLEMENTED | `app/context/service.py` | `tests/context/` | |

## B. Interaction / Normalization (D023, D024, D026, D045)

| Requirement | Status | Implementation | Tests | Notes |
| --- | --- | --- | --- | --- |
| Semantic Layer decomposes objectives (D023) | PARTIAL | `app/semantic/normalizer.py`, `app/semantic/objective_extractor.py` | `tests/semantic/test_normalizer.py` | Deterministic extractor; LLM extractor pending |
| Controlled objective type + open subtype (D024) | IMPLEMENTED | `app/models/contracts.py`, `app/semantic/objective_extractor.py` | `tests/semantic/test_objective_extractor.py` | Type + subtype + base_priority |
| Implicit constraint marked as inferred (D026) | IMPLEMENTED | `app/models/contracts.py` origin/authority | `tests/semantic/test_constraints.py` | CONTEXT_INFERRED → INFERRED_DEFAULT |
| Clarification with options when ambiguous (D045) | IMPLEMENTED | `app/models/clarification.py`, `app/semantic/entity_resolver.py` | `tests/semantic/test_entity_resolver.py` | Recommendation, never silent choice |
| Entity resolution + alias/nickname (D004) | IMPLEMENTED | `app/semantic/entity_resolver.py` | `tests/semantic/test_entity_resolver.py` | In-memory dictionary; persistent dictionary pending |
| Evidence/Artifact/Metric transformation (post 09 §1) | MISSING | — | — | Evidence extraction pending |

## C. Requirement Decomposer (D034, D062)

| Requirement | Status | Implementation | Tests | Notes |
| --- | --- | --- | --- | --- |
| Decomposer before Planner | IMPLEMENTED | `app/semantic/requirement_decomposer.py` | `tests/semantic/test_requirement_decomposer.py` | Deterministic; LLM decomposer pending |
| Semantic-atomic requirements | IMPLEMENTED | `RuleBasedRequirementDecomposer` | `tests/semantic/test_requirement_decomposer.py` | One requirement per semantic need, not per metric |
| Initial Requirement immutable (D062) | IMPLEMENTED | `app/models/requirements.py` | `tests/test_domain.py` | |
| Planner-added supporting requirement (D062) | IMPLEMENTED | `app/models/requirements.py` | `tests/test_domain.py` | |
| QualificationRule separate from SampleAdequacyRule (D038) | IMPLEMENTED | `app/models/contracts.py`, `app/assessment/adequacy.py` | `tests/test_adequacy.py` | |
| League progress vs local coverage (D039) | IMPLEMENTED | `LeagueStateSnapshot`, `app/assessment/adequacy.py` | `tests/test_adequacy.py` | Local lag produces a limitation |

## D. Planner (D035, D040, D061, D064, D065)

| Requirement | Status | Implementation | Tests | Notes |
| --- | --- | --- | --- | --- |
| PLAN / REPLAN / STOP_PLANNING (D061) | IMPLEMENTED | `app/agent/planner.py` | `tests/test_planner.py` | |
| `planner_terminal` + reason (D065) | IMPLEMENTED | `app/agent/planner.py` | `tests/test_planner.py` | |
| Terminal latch (D065) | IMPLEMENTED | `PlannerTerminalLatch` | `tests/test_planner.py` | |
| Planner sees RequirementState + artifact index + assessment summary (D064) | IMPLEMENTED | `PlannerContext` | `tests/context/test_context_wiring.py` | |
| Planner keeps semantic level (D015) | IMPLEMENTED | `PlannerContext` has no physical schema | — | |
| `base_criticality` stable (D035) | IMPLEMENTED | frozen contract | `tests/test_domain.py` | |
| LLM Planner behind protocol (§55) | IMPLEMENTED | `app/llm/planner.py` | `tests/llm/test_planner.py` | Validated output; deterministic fallback; not yet orchestrator-wired |

## E. Router / Source Mapping (D015, D016, D020, D021, D040)

| Requirement | Status | Implementation | Tests | Notes |
| --- | --- | --- | --- | --- |
| Narrow routing, precedence (D040) | IMPLEMENTED | `app/agent/routing.py` | `tests/test_routing.py` | |
| Source preference is soft | IMPLEMENTED | `app/agent/routing.py` | `tests/test_routing.py` | |
| `MetricDefinition` + `SourceMapping` (D020, D021) | IMPLEMENTED | `app/models/metrics.py`, `app/semantic/metric_registry.py` | `tests/test_metrics.py` | DIRECT/CALCULATED modeled and consumed by the resolver |
| Source Mapping execution (§21) | IMPLEMENTED | `app/agent/source_mapping.py`, `Router.route(execution_route=...)` | `tests/test_source_mapping.py` | Orchestrator wiring pending |
| Schema Registry semantic→physical (D015) | PARTIAL | `app/semantic/schema_registry.py` | `tests/test_schema_registry.py` | Lookup only; no planner/tool consumption |

## F. Data & Tool layer (D002, D009, D016, D047)

| Requirement | Status | Implementation | Tests | Notes |
| --- | --- | --- | --- | --- |
| Program-validated SQL (D002) | IMPLEMENTED | `app/validation/sql_guard.py` | `tests/test_sql_guard.py` | |
| Read-only analytics (D047) | IMPLEMENTED | `app/tools/execution.py` | `tests/test_tool_execution.py` | Guard-before-connect, read-only txn |
| 0 rows vs tool failure (D009) | IMPLEMENTED | `app/tools/results.py` | `tests/test_tool_execution.py` | |
| retryable vs recoverable (D011) | IMPLEMENTED | `app/tools/results.py`, `Executor` | `tests/test_executor.py` | |
| RawWebResult → Evidence extraction (D016, P004) | MISSING | `app/tools/web_api.py` returns raw JSON | — | No Evidence Extractor |
| Feature Engine → Metric Artifact with lineage (D037) | IMPLEMENTED | `app/features/metrics.py` | `tests/test_feature_engine.py` | Deterministic computations; FEATURE artifact with lineage |
| Coverage manifest for Router (§02) | MISSING | — | — | |

## G. Evaluation & Sufficiency (D018, D027, D028, D029, D049, D050, D060)

| Requirement | Status | Implementation | Tests | Notes |
| --- | --- | --- | --- | --- |
| Deterministic hard/soft validation (D060) | IMPLEMENTED | `app/assessment/validator.py` | `tests/test_artifacts.py` | |
| Hard failure not overridable (D060) | IMPLEMENTED | contract + service | `tests/test_artifacts.py` | |
| Judge assigns discrete levels (D028) | IMPLEMENTED | `app/assessment/judge.py` | `tests/test_artifacts.py` | Deterministic only |
| Judge short summary (D059) | IMPLEMENTED | `AssessmentService` | `tests/test_artifacts.py` | |
| Requirement State Service (D058) | IMPLEMENTED | `app/state/services.py` | `tests/test_state.py` | Pure functions |
| Objective State Service (D058) | IMPLEMENTED | `app/state/services.py` | `tests/test_state.py` | |
| Critical gate before weighted coverage (D049) | PARTIAL | `derive_objective_state` core gate | `tests/test_state.py` | No weighted coverage |
| COMPLETE retains optional gaps (D050) | IMPLEMENTED | `optional_gaps` | `tests/test_state.py` | |
| LIMITED / FAILED semantics (D030) | PARTIAL | `derive_objective_state` | `tests/test_state.py` | "useful bounded result" not modeled |
| LLM Judge behind protocol (§30) | IMPLEMENTED | `app/llm/judge.py` | `tests/llm/test_judge.py` | Hard failure bypasses the provider |
| RequirementState rich projection (§14) | IMPLEMENTED | `RequirementState` + `derive_requirement_state` | `tests/test_state.py` | artifact/limitation/unresolved/blocking refs, recoverable |

## H. Orchestration & State (D041, D043, D044, D053)

| Requirement | Status | Implementation | Tests | Notes |
| --- | --- | --- | --- | --- |
| Orchestrator is manager (D041) | IMPLEMENTED | `app/agent/orchestrator.py` | `tests/test_orchestrator.py` | |
| Multiple state domains, refs not copies (D043) | PARTIAL | states are small projections | `tests/test_state.py` | No explicit Query/Planning/Routing/Execution/Permission/Budget state objects |
| Local ownership + reviewed transition (D044) | IMPLEMENTED | `app/models/report.py`, `app/agent/review.py` | `tests/test_report_review.py` | Cross-domain proposals require review |
| State transition reason/version/timestamps (§41) | IMPLEMENTED | `app/models/transition.py`, `StateTransitionLog` | `tests/test_state_transition.py` | Continuity + monotonic version; no silent mutation |
| `AgentReport` review order (§37, §38) | IMPLEMENTED | `ReportReviewer.review_all` | `tests/test_report_review.py` | Dependency order; cycles stay DEFERRED |
| Checkpoint timing (D054) | IMPLEMENTED | `RunRecorder.checkpoint` | `tests/persistence/test_orchestrator_persistence.py` | 4 positions; WAITING_FOR_USER not yet used |

## I. Persistence (D054, D055, D056, O005)

| Requirement | Status | Implementation | Tests | Notes |
| --- | --- | --- | --- | --- |
| Separate operational store | IMPLEMENTED | `SqliteOperationalStore` | `tests/persistence/test_operational_store.py` | SQLite is local/dev |
| Operational PostgreSQL (D055) | PARTIAL | `PostgresOperationalStore` (injected connection) | `tests/persistence/test_postgres_store.py` | UNVERIFIED_LIVE; no real database used |
| Artifact payload storage (D056) | IMPLEMENTED | `LocalFilesystemArtifactStorage` | `tests/persistence/test_artifact_storage.py` | |
| Checkpoint coordinate (D054) | IMPLEMENTED | `Checkpoint` | `tests/persistence/test_resume.py` | |
| Resume RUNNING → INTERRUPTED | IMPLEMENTED | `ResumeService` | `tests/persistence/test_resume.py` | |
| Artifact reuse, no duplicate execution | IMPLEMENTED | `Orchestrator.run(restored=...)` | `tests/persistence/test_orchestrator_persistence.py` | |
| Retention/compaction policy (O005) | DEFERRED | — | — | No retention need yet at this scale |

## J. Response & Completion (D051, D052, D053)

| Requirement | Status | Implementation | Tests | Notes |
| --- | --- | --- | --- | --- |
| ResponsePackage accepted-only (D051) | IMPLEMENTED | `app/agent/response.py` | `tests/context/test_context_wiring.py` | |
| CompletionReport internal record (D053) | IMPLEMENTED | `app/models/reports.py` | `tests/test_orchestrator.py` | |
| Critical shared knowledge retained (D052) | IMPLEMENTED | `ContextService` RESPONSE purpose | `tests/context/test_context_wiring.py` | |
| CompletionReport richer fields (§49) | PARTIAL | has most; missing permission events/follow-ups | — | |
| LLM Response behind protocol (§54) | IMPLEMENTED | `app/llm/response.py` | `tests/llm/test_response.py` | Accepted evidence only; deterministic fallback |

## K. Shared Knowledge & Context (D017, D022, D057)

| Requirement | Status | Implementation | Tests | Notes |
| --- | --- | --- | --- | --- |
| Retrieval not a sub-agent (D057) | IMPLEMENTED | `app/context/service.py` | `tests/context/test_context_service.py` | |
| Metric Registry deterministic (D022) | IMPLEMENTED | `app/semantic/metric_registry.py` | `tests/test_metrics.py` | |
| Schema Registry | IMPLEMENTED | `app/semantic/schema_registry.py` | `tests/test_schema_registry.py` | |
| System State ≠ LLM Context (D017) | IMPLEMENTED | `ContextService` projections | `tests/context/` | |
| Run-scoped isolation | IMPLEMENTED | `scope_run`/`run_id` | `tests/context/test_registry_and_isolation.py` | |
| Entity Dictionary | MISSING | — | — | |
| League/Reference context (D039) | MISSING | — | — | |
| RAG knowledge base | MISSING | `app/semantic/schema_rag.py` placeholder | — | |
| Context ranking/freshness policy (O006) | PARTIAL | freshness_rank only | `tests/context/test_context_service.py` | |

## L. Governance & Observability (§58, §59, O008)

| Requirement | Status | Implementation | Tests | Notes |
| --- | --- | --- | --- | --- |
| Read-only policy | IMPLEMENTED | `app/validation/policy.py`, guard | `tests/test_safety.py` | |
| Secret scanning (D060 scope) | IMPLEMENTED | `scripts/secret_scan.py` | `tests/test_secret_scan.py` | |
| Permission/cost policy levels (O008) | PARTIAL | Router cost filter | `tests/test_routing.py` | |
| Clarification/permission escalation (§42-43) | MISSING | — | — | |
| Observability structured metrics (§58) | IMPLEMENTED | `app/observability/metrics.py` | `tests/observability/test_metrics.py` | Every message redacted at the boundary |
| Evaluation metrics (§59) | IMPLEMENTED | `app/observability/evaluation.py` | `tests/observability/test_evaluation.py` | Completion/replan/retry/failure rates, steps |
| Prompt versioning (§57) | IMPLEMENTED | `app/llm/prompts.py` | `tests/llm/test_provider_and_prompts.py` | `PromptTemplate` id + version |
| Provider-agnostic config (§56) | IMPLEMENTED | `app/config.py`, `app/llm/openai_provider.py` | `tests/llm/test_openai_provider.py` | Per-agent `*_MODEL`; keys env-only |

## M. Deliverables (this task)

| Requirement | Status | Notes |
| --- | --- | --- |
| `docs/blog-implementation-matrix.md` | IMPLEMENTED | this file |
| Usage documentation (`docs/usage/`) | MISSING | |
| CLI entry point | MISSING | |
| End-to-end vertical flow test | PARTIAL | orchestrator E2E; no semantic layer |
| Baseball query integration fixtures | MISSING | |
| Adversarial/security test battery | PARTIAL | many exist; LLM/SQL/context gaps |

## Open questions (O001–O009)

| ID | Status | Resolution |
| --- | --- | --- |
| O001 core domain schemas | PARTIAL | Core contracts implemented; AgentReport/transition missing |
| O002 StateTransition/AgentReport/PlanningDecision/Checkpoint/ContextPackage | PARTIAL | PlanningDecision/Checkpoint/ContextPackage done; AgentReport + transitions missing |
| O003 state update / sufficiency algorithm | PARTIAL | Simple deterministic gate; weighted coverage not implemented |
| O004 Planner/Router ↔ Registry/Context interfaces | PARTIAL | Context wired; SourceMapping execution missing |
| O005 persistence schema/version/retention | PARTIAL | Versioned objects + checkpoints; Postgres + SQLite stores | `tests/persistence/` | Retention DEFERRED |
| O006 context retrieval/projection policy | PARTIAL | Deterministic; no semantic/full-text retrieval yet |
| O007 QualificationRule/SampleAdequacyRule/LeagueState | IMPLEMENTED | `app/models/contracts.py`, `app/assessment/adequacy.py` | `tests/test_adequacy.py` | Dynamic thresholds remain configurable |
| O008 permission/cost levels + escalation | PARTIAL | Cost filter only |
| O009 LangGraph adoption timing | DEFERRED | Testable state machine first; no adoption needed yet |
