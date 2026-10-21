# Domain Concept Registry — Architecture Baseline v1

Scope: production code at `1d8cb3ad0ec786b48c82de830c6ad68b229c1511`
(tree `3cca1a0c28a2e377920e5516d23bb898f8e92178`). This is a focused semantic
identity review, not a new behavior/security audit. Documentation-only corrections
follow it; no Python symbols, wire names or persisted values are changed.

## Reading rules

- A domain label is not necessarily a Python class. **Requirement** means
  `UserRequirement`; **Evidence** names a governed support process, not an `Evidence`
  model. There is no production `Requirement`, `Intent`, `Message` or
  `PropositionCoverage` class. Do not invent aliases for these labels.
- Ownership means decision ownership, not necessarily the file declaring a model.
  Most shared models below are declared in [`models.py`](../../app/contracts/models.py).
- Similar fields do not establish identity. Inspect construction, consumers,
  authority, lifetime and serialization before merging anything.
- The Judge alone approves final factual Claims. Composer labels and Conformance
  never supply original factual authority.

## Canonical vocabulary

| Concept | Canonical name (exact Python symbol) | Owner | Meaning | Related but distinct concepts |
|---|---|---|---|---|
| Semantic result | `SemanticInterpretation` | `SemanticInterpreter` / semantic layer | Preserved query, interpretation proposals, entities, clauses, hints, references | `ConversationMessage`, `SendMessage` |
| Conversation message | `ConversationMessage` | `ConversationManager` | User/assistant text, time and Goal association | Semantic interpretation, API message command |
| Analytical request clause | `AnalysisRequest` | Semantic layer | Interpreted analytical task; optional role, reporting/operation contracts and source span | `EvidenceRequest`, `PlannerNeed` |
| Evidence request clause | `EvidenceRequest` | Semantic layer | Interpreted research obligation clause with meaning origin | Evidence material, `AnalysisRequest` |
| Requested evidence role | `EvidenceIntent` | Interpreter proposal; Requirement grounding | Confirmed state, reported/firsthand account or prediction and information class | `EvidenceAssessment`, execution permission |
| Intent / analytical intent | `Goal.raw_query`, `UserRequirement.original_intent`; analytics intent dictionaries | Semantic owner; analytical lane binds obligations | Original requested meaning versus capability-local normalized input; AnalyticalIntent is a conceptual label, not a Python class | `AnalyticalQuery`, requested analytical contract, planning objective |
| User obligation | `UserRequirement` | `RequirementBuilder` | Required/optional user meaning, original intent, scope and grounded contract | `PlannerNeed`, `RequirementAssessment` |
| Obligation ancestry | `RequirementProvenance` | Semantic/clarification backend | Bound message/digest/span, context/parents or validated clarification | Semantic entailment, `OriginLineage` |
| Answer obligation | `AnswerProposition` | Proposition builder; Judge covers it | Requirement-derived atomic subject/relation/scope output slot | Work graph, `Claim` |
| Proposition coverage | `CoverageState`, `AnswerProposition.coverage` | Judge | SATISFIED/PARTIAL/UNSUPPORTED/CONTRADICTED/NOT_ATTEMPTED/BLOCKED | `RequirementStatus`, `GoalStatus` |
| Work item | `PlannerNeed` | Planner; runtime derives criticality | Capability/objective/dependencies and served Requirement IDs | User obligation, binding |
| Execution binding | `InputBinding`, `BindingResult` | `RuntimeBinder` | Concrete artifact/export selection plus typed failures | `PlannerNeed`, `ToolOutcome` |
| Execution material | `Artifact`, `Export` | Capabilities | Rows/content/exports, requested/actual scope, provenance, lineage and tool status | Judge-approved evidence/Claims |
| Acquired resource | `RetrievedDocument` | Web acquisition | Concrete ContentArtifact: resource identity, retrieval and representations | `Artifact`, search hit, approved evidence |
| Resource view | `Representation` | Acquisition/resolution | Native or derived content with parent/hash/method and locator kind | `EvidenceSpan`, source authority |
| Evidence fragment | `EvidenceSpan` | Evidence processing | Replayable text offsets or structured locator within a representation | `EvidenceFinding`, `Claim` |
| Evidence meaning proposal | `EvidenceAssessment`, `SourceRoleAssessment` | Evidence assessor | What a fragment asserts and the source's role; no approval | `EvidenceCompatibilityResult`, finding |
| Span verification | `EvidenceFinding` | Evidence-producing lane; Judge admits it | One span versus one proposition, gates, signatures and support state | Final factual `Claim` |
| Artifact admissibility | `ArtifactAssessment` | Judge-local evidence processing | Narrow artifact/proposition eligibility, native/derived/reporting/subject proofs | `RequirementAssessment`, `CandidateAssessment` |
| Analytical candidate admission | `CandidateAssessment` | Semantic candidate validator | Admitted/coverage/accounted and uncovered source spans | Artifact eligibility, Judge coverage |
| Requirement conclusion | `RequirementAssessment` | Judge | Authority, aggregated coverage/status, support, gaps/conflicts and inspection IDs | `GoalStatus`, candidate assessment |
| Communicable assertion | `Claim` | Judge | Approved assertion with epistemic label, scope, Requirement/proposition links and support | `EvidenceFinding`, Composer statement |
| Useful non-asserted material | `QualifiedEvidence` | Judge | Exact fragment plus structural limitation; never satisfies coverage | `Claim`, approved factual evidence |
| Governed result | `AnswerProjection` | Judge | Detached recursively sealed Claims, assessments, coverage/status, qualifications and origins | `AnswerPackage`, `ComposerDraft` |
| Presentation input | `AnswerPackage` | `build_answer_package` / response | Immutable navigation snapshot plus Requirements, approved facts and citation mapping | Projection, draft, API `AnswerView` |
| Presentation navigation | `AnswerFact`, `RequirementAnswer`, `EvidenceContext` | Response | Marker-linked factual text, coverage navigation and bounded read-only context | Original `Claim`, original Requirement |
| Untrusted generated answer | `ComposerDraft`, `ComposedStatement` | `ResponseComposer` | Proposed text and statement labels/citations/supports | Approved answer, factual Claim |
| Representation review | `ResponseConformanceAssessment`, `StatementConformance` | Independent response reviewer | Package/draft-bound semantic representation proposals | Factual verification, final conformance policy |
| Presentation verdict | `ConformanceResult` | `conform_composition` / verification | Repaired text and representation violations over package/draft | `AnalyticalConformanceResult` |
| Analytical verdict | `AnalyticalConformanceResult` | Analytical conformance | Requested contract versus execution attestation: status/differences/coverage | Presentation conformance, Judge approval |
| Enumeration compatibility | `EnumerationCompatibilityResult` | `check_compatibility` | Whether complete populations may combine for a `GlobalOperation` | Evidence eligibility, derived factual proof |
| Evidence compatibility | `EvidenceCompatibilityResult` | `assess_compatibility` | Relation/assertion/temporal/source-role eligibility against a proposition | Enumeration compatibility |
| Content resolution outcome | `ResolutionResult` | `AgenticContentResolver` | Status/trace and optional derived representation or candidate URI | Canonical entity resolution, factual support |
| Program safety result | `ValidationResult` | `python_compute.validate_program` | AST checks, reason and imports | Operation admission, factual grounding |
| Requested derived meaning | `OperationContract`, `OperandObligation` | Semantic Requirement grounding | Finite operation, ordered operand scopes/parent outputs and parameters | `GovernedOperation`, `GlobalOperation` |
| Executed derived proposal | `GovernedOperation`, `OperandCell` | Tool proposes; `OperationAdmission` validates | Exact artifact/export/row/field operands and requested operation correspondence | Requested contract, arbitrary Python output |
| Execution context | `ExecutionContext`, `SystemClock` | Engine | Per-Goal state, tools, clock, artifacts and diagnostics | Conversation, historical label RuntimeContext |
| Stable shared knowledge | `KnowledgeItem`, `KnowledgeStore` | Knowledge subsystem | ACTIVE semantic context versus separately reviewed candidates/mutable facts | Mutable evidence, nonexistent SharedKnowledgeItem class |
| Public presentation | `AnswerView`, `PresentationArtifact`, `RequirementView` | API projection | Inert, filtered browser DTOs, not internal models | `AnswerPackage`, raw `Artifact` |
| Lifecycle policy | `Orchestrator`, `OrchestratorDecision` | Runtime | Bounded FINAL/REGENERATE_COMPOSER/REPLAN/FALLBACK transitions | Planner work selection, Judge truth |

Exact non-model locations: [`binder.py`](../../app/runtime/binder.py),
[`analytical_candidate.py`](../../app/semantic/analytical_candidate.py),
[`answer_contract.py`](../../app/response/answer_contract.py),
[`composition_contract.py`](../../app/response/composition_contract.py),
[`composition_conformance.py`](../../app/verification/composition_conformance.py),
[`conformance.py`](../../app/capabilities/analytics/conformance.py),
[`enumeration.py`](../../app/verification/enumeration.py),
[`evidence_semantics.py`](../../app/verification/evidence_semantics.py),
[`agentic_resolution.py`](../../app/web/agentic_resolution.py),
[`python_compute.py`](../../app/capabilities/python_compute.py),
[`operations.py`](../../app/contracts/operations.py),
[`context.py`](../../app/runtime/context.py), [`store.py`](../../app/knowledge/store.py),
[`contracts.py`](../../app/api/contracts.py), [`orchestrator.py`](../../app/runtime/orchestrator.py).

## Identity audit: construction, consumption and lifetime

`P` below means internal Pydantic JSON/dict encoding; it does **not** mean a public
OpenAPI schema. `D` means transient dataclass. The persistence store saves
`ConversationState` JSON (messages, Goals, Requirements, pending clarification and
accepted context), not a durable artifact/projection store.

| Symbols under review | Construction → actual consumers | Fields / lifecycle / authority / serialization |
|---|---|---|
| `SemanticInterpretation` / `ConversationMessage` / API `SendMessage` | `SemanticInterpreter.interpret` → `RequirementBuilder.build`, Planner; conversation `commit` → SQLite; API `send` → `Engine.ask` | Interpretation retains raw query and semantic clauses (turn-local P); conversation stores role/text/time/goal (persisted P); command is validated text ingress (public P). None is evidence. |
| `AnalysisRequest` / `EvidenceRequest` / `EvidenceIntent` | Interpreter `_from_json` → Requirement builder, proposition builder, Planner | Both clauses retain text/objective/ref/user_span/meaning_origin; analysis additionally carries analytical role and operation/reporting structures. Intent describes requested evidence mode/class. Proposal lifetime (P), not execution/fact authority. |
| `UserRequirement` / provenance / proposition | `RequirementBuilder.build`, `bind_current_user_provenance`, `build_propositions` → authority resolver, Planner, analytical lane and Judge | Requirement owns requested meaning; frozen provenance records exact ancestry; proposition owns answer slot, dependencies and coverage. Goals/Requirements/propositions persist (P); Judge detaches its evaluated snapshot. Ancestry alone is not entailment. |
| `RequirementAssessment` / coverage / `GoalStatus` | Judge intermediate states → `_requirement_assessments`, final reaggregation → Orchestrator/package/API | Per-Requirement status+coverage/support differs from per-proposition coverage and Goal-level enum. Sealed in projection (P), never Planner authority. Package's old-projection fallback calls the **same** `requirement_coverage` helper. |
| `PlannerNeed` / `BindingResult` | `Planner._validate`, Engine prerequisites/converters → Binder; `RuntimeBinder.bind` → `Engine._execute_pending` | Work graph state (P) versus selected `InputBinding`s, unresolved/rejected/failures (D); turn/replan-local. Neither changes user completion semantics. |
| `Artifact` / `EvidenceFinding` / `Claim` | Tool → `Judge.evaluate`; `verify_span` → persisted artifact finding collections → Judge `_all_evidence_findings` / `_claims` | Material+scope+exports/tool status; span/proposition/signature/gates; approved text/support/scope/epistemic. Different stages (P); runtime artifact store is in memory. Tool `ACCEPTED` is not Judge approval. |
| `ArtifactAssessment` / `CandidateAssessment` | Judge calls `assess_artifacts` → `_claims`/projection; `assess_candidate` → `candidate_proves`, authority/source review | Artifact ID, eligible propositions and inspectable proofs (P; sealed at Judge exit) versus admitted and accounted/uncovered spans (frozen D; pre-execution). Both evaluate, but different inputs, decision owners and authority. |
| `QualifiedEvidence` / `Claim` | Judge `_collect_qualified_evidence` versus `_claims` → package, Composer and Conformance | Fragment+precise limitation versus answerable assertion+support. Both P; sealed with projection. Qualified material cannot change coverage/status. |
| Projection / package / draft / presentation verdict | Judge → `build_answer_package` → `ResponseComposer.compose_draft` → `conform_composition` → Orchestrator | Governed result (sealed P); navigation+full governed snapshot+SHA-256 package identity (deep-frozen D); untrusted text/statements (P); repaired text/violations (D). No reverse authority conversion. |
| Analytical / presentation conformance | Analytics `compare(requested, attest(...))` → tool receipt, Judge receipt recheck; `conform_composition(draft, package, assessment)` → Orchestrator | Frozen status/differences/coverage (P, embedded receipt) versus text/violations (D). Neither is an original truth source; only analytical conformance inspects requested/executed analytical semantics. |
| Enumeration / evidence compatibility | `check_compatibility` → `PythonComputeTool.execute`; `assess_compatibility` → evidence verifier/Judge source review | ok/code/detail/shared versus verdict/reasons/orthogonal compatibility flags (D). Population composability and proposition eligibility are not interchangeable. |
| Resolution / program validation | `execute_program` / resolver `resolve` → Web `_retrieve`; compute `validate_program` → `run_program` | status/trace/representation/URI versus ok/reason/imported_modules (D). Content access versus AST safety; neither mints evidence/Claims. Traces may be encoded in acquired artifacts. |
| `DateRange` / IR `DateWindow` | Canonical builder → `AnalyticalQuery`; `attest` and tool `_scope_date` → `Scope`/`EnumerationContract` | Same nullable start/end calendar value, but domain scope/receipt (governable P) versus IR candidate (IR `_Base` P). Independent requested/executed representations; conversion explicitly copies endpoints. |
| API command/event text models | API ingress `SendMessage`; `Run.emit` validates `AcceptedPayload` / `DeltaPayload` → SSE / frontend protocol | Identical text-shaped payloads, different direction, validation constraints, event discriminators and lifecycles. Public P, not duplicate domain authority. |

## Classified findings and disposition

| ID / classification | Code evidence and conclusion | Disposition |
|---|---|---|
| SI-01 — SAME_CONCEPT_DIFFERENT_NAMES | Requirement = `UserRequirement`; RuntimeContext = current `ExecutionContext`; ContentArtifact = `RetrievedDocument` (class docstring, ADR 0031); SharedKnowledgeItem = `KnowledgeItem`. No corresponding second classes/import aliases exist. | Canonical symbols above; correct current docs, keep historical labels in original ADRs. No production rename. |
| SI-02 — DIFFERENT_CONCEPTS_SIMILAR_NAMES | The two conformance constructors/callers above check different inputs and emit different contracts. `CandidateAssessment` checks source consumption before execution; `ArtifactAssessment` is Judge-local eligibility after collection. | Retain all four; name owner whenever discussing assessment/conformance. |
| SI-03 — INTENTIONAL_LAYER_REPRESENTATIONS | `build_answer_package` copies governed data, adds navigation/citations and freezes it; API `present` filters into `AnswerView`/`PresentationArtifact`; `_scope_date` converts IR `DateWindow` to domain `DateRange`. AST field-shape inventory also found command/event text DTOs with equal fields. | Keep boundaries. Similar value shapes are not evidence for a type merger or an unnecessary round trip. No repeated equivalent authority conversion found in inspected production paths. |
| SI-04 — STALE_DOCUMENTATION | Previous root architecture named nonexistent `analytics/intent.py` and `verification/sufficiency.py`. Previous domain-model document described a planned EvidenceItem/EvidenceCatalog and wrong ArtifactAssessment owner/fields. SPEC §12.7 still assigned a post-presentation transition to nonexistent `Judge.reconcile_expression`; `Engine.ask` instead uses immutable Judge output and Orchestrator repair. | Correct current docs, replace root architecture with a navigation entry. Preserve ADR/audit history. Registry supersedes invented implementation labels. |
| SI-05 — POTENTIAL_SEMANTIC_DRIFT | `Artifact.status='ACCEPTED'` is tool declaration (Binder accepts usable exports), not final evidence admission (`Judge.evaluate` rechecks). `AnswerView.status` includes reserved `PARTIAL`, whereas `GoalStatus` does not. `QualifiedEvidence` is useful **but insufficient**, not approved factual evidence. | Explicit warnings in authority/data docs; preserve serialized vocabulary and frontend compatibility. A future rename requires a scoped migration, not this cycle. |
| SI-06 — POTENTIAL_SEMANTIC_DRIFT | `GoalResearchBudget` / `effective_budget` exist, but `ExecutionContext` has no shared budget handle; Web reports a default budget and cache disabled. Registry family metadata coexists with seven Planner-visible specs (`CapabilityRegistry.planner_view`). Browser configuration exists but `render_public_page` returns `JS_RENDER_REQUIRED` without networking. | Document implementation versus accepted target. Do not claim enforced Goal-wide quotas, full caching, active browser acquisition, or a two-tool-only runtime. Track in status/roadmap; no silent behavioral repair. |
| SI-07 — INTENTIONAL_LAYER_REPRESENTATIONS | `StatementKind` is Composer's untrusted label (includes UNTYPED); `StatementEpistemicRole` is review classification (includes UNKNOWN). `ResponseConformanceAssessment` binds package/draft identities; `ConformanceResult` carries deterministic representation verdict. | Retain despite overlapping enum values. Never use Composer's label as reviewer approval. |
| SI-08 — ACTUAL_DUPLICATE_CONTRACT | AST inventory found **no repeated production class names**. The only identical annotated field shapes were DateRange/DateWindow and the API text DTOs, traced in SI-03. Focused constructor/import/caller review found no confirmed duplicate authoritative contract in the priority groups. | No merge justified. This negative finding is bounded to inspected code, not proof that every repository abstraction is optimal. |

### Deferred production naming / compatibility work

No confirmed production inconsistency requires a rename in this cycle. Preserve the
[existing deferred rename map](NAMING_REFACTOR_MAP.md#deferred-renames), including
`CandidateAssessment`, `ResolutionResult` and `BindingResult`: their module contexts
are sufficient and they are transient, so optional qualification would be safe internal
cleanup after caller review, not a behavior repair. `app/runtime/context.py` still
opens with the historical RuntimeContext label in its module docstring; the actual
class/import/callers (`Engine`, capability execution) use `ExecutionContext`. A later
comment-only correction is safe internal cleanup, with no API/SQLite compatibility
impact; it is deferred to keep this commit documentation-files-only.

If `Artifact.status` or kind vocabulary is ever renamed, the exact surface includes
`Artifact`, `RuntimeBinder.bind`, `Engine._execute_pending`, `assess_artifacts`,
`Judge.evaluate`, `PythonComputeTool`, `ArtifactProjectionTool` and API `present`.
Canonical identity must remain **tool material availability**, not approved evidence.
Kinds and statuses occur in artifact/export/receipt JSON and fixtures; API presentation
has its own accepted/partial values. Such a change has **behavioral and persistence
compatibility risk** and needs an explicit adapter/migration review. Do not do it here.

No material unresolved **identity-contract collision** was confirmed. Functional
backlog and accepted-target gaps remain; this is not a claim of full semantic coverage.
See [implementation status](../engineering/IMPLEMENTATION_STATUS.md).
