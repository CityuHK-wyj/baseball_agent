# Naming Conventions

Status: active. Naming-refactor starting baseline:
`a3134399d8e8ade2282f83228f383ab15b1f8792`.
Architecture Baseline v1 verifies the resulting implementation at
`1d8cb3ad0ec786b48c82de830c6ad68b229c1511`; see the
[Domain Concept Registry](DOMAIN_CONCEPT_REGISTRY.md) for exact symbols and identity findings.

These conventions describe the Python implementation and the current architecture
documents. They do not authorize behavior changes or changes to public/persisted
contracts.

## Two rules

1. **Module/package = architectural ownership.** A type lives where the decision it
   represents is owned: `app/semantic`, `app/contracts`, `app/planner`,
   `app/runtime`, `app/capabilities`, `app/verification`, `app/response`, `app/api`.
2. **Type suffix = domain responsibility.** The suffix says what the value *is* and
   what authority it does **not** carry. A suffix never promotes a value into
   authority it did not already have.

Names must make the authority boundary readable: proposal vs. obligation, candidate
vs. approved evidence, assessment vs. decision, draft vs. projection.

## Vocabulary

| Concept | Meaning | Representative implementation types |
|---|---|---|
| Intent | User intent | `Goal.raw_query`, `UserRequirement.original_intent` |
| Interpretation | Semantic interpretation output | `SemanticInterpretation`, `RelationSemantics`, `AnalysisRequest`, `EvidenceRequest` |
| Requirement | User-facing obligation | `UserRequirement`, `RequirementProvenance` |
| Need | Planner work requirement | `PlannerNeed`, `NeedStatus` |
| Reference | Entity or object pointer | `Reference`, `EntityReference` |
| Export | Typed artifact payload and consumer contract | `Export` |
| Provenance | Authority/source ancestry | `RequirementProvenance`, `OriginLineage`, `PropositionOrigins` |
| Binding | Resolved execution binding | `InputBinding`, `BindingResult`, `RuntimeBinder` |
| Artifact | Execution-produced material | `Artifact`, `ArtifactAssessment` |
| Evidence | Candidate factual support | `EvidenceSpan`, `EvidenceFinding`, `QualifiedEvidence` |
| Assessment | Evaluation of evidence or obligations | `EvidenceAssessment`, `ArtifactAssessment`, `RequirementAssessment` |
| Claim | Judge-approved factual assertion | `Claim` |
| Projection | Finalized governed state | `AnswerProjection` |
| Package | Read-only presentation inputs | `AnswerPackage` |
| Draft | Untrusted generated answer | `ComposerDraft`, `ComposedStatement` |
| Result | Validation or computation result | `ConformanceResult`, `AnalyticalConformanceResult`, `GuardResult` |
| Decision | Lifecycle or conformance decision | `OrchestratorDecision`, `SemanticConformanceDecision` |
| Adapter | External-system integration | `SearchProvider`, `TavilySearchBackend`, `WikipediaSource` |
| Service | Cross-component service with clear ownership | Reserved; do not add casually |

### Authority notes

- `SemanticInterpretation` and `RelationSemantics` are proposals. They can direct
  cognition but cannot satisfy a Requirement or create factual truth.
- `PlannerNeed` has no completion authority; only `UserRequirement` defines what the
  user needs (ADR 0003).
- `Artifact`, `Export`, `EvidenceSpan` and `EvidenceFinding` are candidates for
  Judge admission, not approved facts. `Artifact.status=ACCEPTED` is tool-declared
  availability, not factual approval. `QualifiedEvidence` is Judge-selected useful
  but insufficient material; it retains its limitation and cannot establish coverage.
- `RequirementAssessment` is the Judge-owned per-Requirement conclusion.
  `AnswerProjection` is the immutable governed result. `AnswerPackage` is its
  read-only presentation view.
- `ComposerDraft` is untrusted model output. `ConformanceResult` is a representation
  verdict over `(AnswerPackage, ComposerDraft)`, never a re-Judge.
- `RequirementProvenance` and `OriginLineage` prove ancestry only, never semantic
  entailment.

## Suffix conventions

| Suffix | Responsibility | Must not be treated as |
|---|---|---|
| `*Interpretation` | Semantic proposal; open-world reading | Requirement, evidence or authority |
| `*Request` | User/interpreter request clause | Planner work item or proof |
| `*Requirement` | User-owned completion obligation | Planner work item |
| `*Need` | Planner work item | User obligation |
| `*Reference` | Pointer to an entity/object | Evidence or support |
| `*Provenance` | Source/ancestry record | Semantic correctness proof |
| `*Binding` | Resolved execution input | New obligation |
| `*Artifact` | Tool/execution output | Approved evidence |
| `*Evidence` | Candidate support material | Approved Claim |
| `*Assessment` | Bounded evaluation with a named owner | Truth or final factual authority |
| `*Claim` | Judge-approved assertion | Composer proposal |
| `*Projection` | Immutable governed state | Mutable presentation cache |
| `*Package` | Read-only presentation input | Mutable authority |
| `*Draft` | Untrusted generated output | Approved answer |
| `*Result` | Validation/computation output | Authority by naming alone |
| `*Decision` | Lifecycle/conformance choice | Factual proof |
| `*Contract` | Governed obligation/operation structure | Evidence |
| `*Receipt` | Grounding/execution record | Truth proof |
| `*Violation` | Deterministic conformance finding | Evidence judgment |
| `*Error` | Exception/validation failure | Normal control flow |

## Specific rules

- **`Message`** is reserved for conversation/transport payloads
  (`ConversationMessage`, API events). The semantic interpreter output is
  `SemanticInterpretation`.
- **`Result`** is not used for untrusted model output; use `Draft`.
- **`Assessment`** names an evaluation whose owner is explicit. Only
  `RequirementAssessment` is the Judge's Requirement conclusion; response
  assessments describe representation only.
- **`Conformance`** is split by domain. Analytical requested-vs-executed checking
  returns `AnalyticalConformanceResult`; post-composition representation checking
  returns `ConformanceResult` in `app/verification/composition_conformance.py`.
- **Aliases** for internal-only types are avoided. The former `RequirementState`
  alias was removed in favor of the canonical `RequirementAssessment`.
- **Domain labels are not extra classes.** Requirement = `UserRequirement`,
  ContentArtifact = `RetrievedDocument`, Shared Knowledge item = `KnowledgeItem`,
  runtime context = `ExecutionContext`. Proposition coverage uses `CoverageState`
  on `AnswerProposition`; there is no `PropositionCoverage` class.
- **Representation boundaries are intentional.** `AnswerView`/`PresentationArtifact`
  are API DTOs, not aliases for `AnswerPackage`/`Artifact`. IR `DateWindow` and
  domain `DateRange` may carry the same calendar endpoints without sharing lifecycle
  or decision ownership.
- **No cross-layer promotion by name.** Renaming a type never moves a decision to a
  new owner.

## Rename procedure

1. Inventory actual symbols and usages, including imports, annotations, API
   schemas, persisted records, frontend types, configuration, tests and tools.
2. Reject any rename that changes public JSON fields, database columns/tables,
   persisted enum values/discriminators, configuration keys, environment variables
   or stable provider interfaces.
3. Apply token/AST-aware identifier changes only; do not run unrestricted
   search-and-replace across the repository.
4. Update imports, type annotations, tests, tools and current architecture docs.
5. Run focused tests, then the full offline suite and frontend checks. Run a live
   smoke test only when execution wiring or frontend/backend integration changed.
6. Record completed and deferred renames in
   `docs/architecture/NAMING_REFACTOR_MAP.md`.
