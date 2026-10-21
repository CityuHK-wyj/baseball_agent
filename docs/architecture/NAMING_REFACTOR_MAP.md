# Naming Refactor Map

Naming-refactor starting baseline: `a3134399d8e8ade2282f83228f383ab15b1f8792`
(`m2.12r4` preserved). Resulting documentation baseline:
`1d8cb3ad0ec786b48c82de830c6ad68b229c1511`.
Scope of the completed refactor: internal Python identifiers and current architecture
documentation only. The tables below are the retained refactor record, not another
rename authorization. Architecture Baseline v1's documentation-only follow-up is the
[Domain Concept Registry](DOMAIN_CONCEPT_REGISTRY.md); it records intentional boundaries,
stale-document corrections and deferred compatibility work without production renames.

Status legend: **completed** = applied in this cycle; **deferred** = reviewed and
intentionally left unchanged.

## Inventory summary

The inventory found four recurrence patterns:

- semantic interpreter output was named like a transport message;
- untrusted Composer output was named `*Result`;
- two different conformance concepts both used `ConformanceResult`, and the
  presentation verdict was named like a process (`CompositionConformance`);
- `RequirementState` survived as an internal alias for the canonical
  `RequirementAssessment`, and two verification modules both defined
  `CompatibilityResult`.

No renamed type is a database table/column, public JSON field, persisted enum value,
serialized discriminator, configuration key, environment variable or provider
interface.

## Completed renames

| Former name | Canonical current name | Owning layer | Reason | Affected references | Compatibility risk |
|---|---|---|---|---|---|
| `SemanticMessage` | `SemanticInterpretation` | Semantic / `app/contracts/models.py` | It is the interpreter's output, not a chat/transport message; avoids confusion with `ConversationMessage` and matches the Interpretation vocabulary. | 7 app files, 36 test files, 8 tool files; 213 total occurrences incl. docs | Low: Python-internal, not in API schemas, persistence or frontend. Normative docs updated; historical audit/phase reports keep the old name. |
| `ComposerResult` | `ComposerDraft` | Response / `app/response/composition_contract.py` | The value is untrusted generated answer text, not a governed result; aligns with the accepted `ComposerDraft` domain concept and `compose_draft`. | 3 app files, 1 direct test import; docs (`GOVERNED_EVIDENCE_DOMAIN_MODEL`, draft ADR 03) | Low: transient model, never persisted or exposed. |
| `parse_composer_result()` | `parse_composer_draft()` | Response | Function verb/type must match the draft's trust level. | 2 app files, 8 test files | Low: internal function. |
| `composition_identity()` | `draft_identity()` | Response / Conformance | The digest is the draft identity bound to an assessment, not a package/composition authority. | 2 app files, 1 test fixture | Low: internal function. |
| `StatementEpistemic` | `StatementEpistemicRole` | Response / `composition_contract.py` | Noun suffix states the responsibility (an epistemic role enum) instead of an adjective. | 2 app files | Low: enum class name is not serialized; field values are unchanged. |
| `CompositionConformance` | `ConformanceResult` | Verification / `composition_conformance.py` | It is the representation verdict/result, not a judge process; matches the accepted domain-model name and `Result` suffix. | 1 app file | Low: internal return type; class name never persisted. |
| `ConformanceResult` (analytics) | `AnalyticalConformanceResult` | Analytics / `analytics/conformance.py` | Removes the cross-layer name collision and names the owning domain (requested-vs-executed analytical contract). | 1 app file | Low: class name not serialized; receipt key `conformance` and its fields are unchanged. |
| `RequirementState` alias | `RequirementAssessment` | Verification / Judge | Removes an unnecessary compatibility alias and uses the canonical Judge-owned assessment type everywhere. | 2 app files; no test/tool code | Low: alias removal only; class fields and behavior unchanged. Historical ADR 0021/ADR draft 02 and audit/phase reports retain the old name. |
| `CompatibilityResult` (enumeration) | `EnumerationCompatibilityResult` | Verification / `enumeration.py` | Removes the only duplicate production class name and names the operation-enumeration domain. | 1 app file | Low: module-internal dataclass. |
| `CompatibilityResult` (evidence semantics) | `EvidenceCompatibilityResult` | Verification / `evidence_semantics.py` | Same duplicate-name removal; names evidence eligibility. | 1 app file | Low: module-internal dataclass. |

### Coherence-only local renames

These do not change public names or behavior; they align local variables with the
renamed types:

- `app/semantic/interpreter.py`: local `message` → `interpretation`.
- `app/response/composer.py`: local `result` → `draft`.
- `app/verification/composition_conformance.py` and
  `app/response/composition_contract.py`: parameter/local `result` → `draft`.

## Deferred renames

| Current name | Candidate | Reason for deferral |
|---|---|---|
| `DYNAMIC_FACT` (and other Artifact kinds such as `DATABASE_RESULT`, `DERIVED_MEASURE`, `WEB_EVIDENCE`, `KNOWLEDGE_ITEM`) | e.g. `DYNAMIC_FACTUAL_RECORD` | Serialized Artifact kind/discriminator used by Judge and persisted results. Renaming requires a data migration and would change a governed execution value. |
| `AnalysisRequest`, `EvidenceRequest` | `AnalyticalRequest`, ... | Names are already precise semantic request clauses. A grammar-only rename touches a large schema surface with no authority-boundary gain. |
| `ResolutionResult` (`app/web/agentic_resolution.py`) | `ContentResolutionResult` | Module context (`agentic_resolution`) already disambiguates it from entity resolution; no duplicate class name remains. |
| `CandidateAssessment` (`app/semantic/analytical_candidate.py`) | `AnalyticalCandidateAssessment` | Module context is sufficient; no competing `CandidateAssessment` exists. |
| `BindingResult` (`app/runtime/binder.py`) | `BindingOutcome` | `Result` is an allowed validation-result suffix; the value is not ambiguous. |
| `compare()` (`app/capabilities/analytics/conformance.py`) | `compare_contracts()` | Module-qualified at every call site; renaming would churn many tests/tools for low architectural value. |
| Module/directory restructuring | — | Out of scope; module/package ownership already follows the architecture. |
| API JSON fields, DB columns/tables, persisted enums/discriminators, config/env keys, provider interfaces | — | Prohibited by the compatibility rules. |
| Historical `docs/audit/`, `docs/PHASE*`, `docs/FINAL_REPORT.md` references | — | Historical records, intentionally not rewritten. |

## Compatibility evidence

- No database schema, migration, configuration key or environment variable changed.
- No public API JSON field changed. `tools/export_frontend_openapi.py` output is
  byte-identical to the baseline worktree (`paths=5`, `schemas=35`).
- No frontend TypeScript type changed; frontend unit tests and production build pass.
- `DYNAMIC_FACT` and all persisted Artifact kinds are unchanged.
- Renamed types are transient Python identifiers; their class names are not
  serialized. No data migration is required.

## Validation performed

| Check | Result |
|---|---|
| Baseline full offline backend suite | 4184 passed, 63 skipped |
| After-refactor full offline backend suite | 4184 passed, 63 skipped |
| Focused composer/conformance/analytics regressions | 1464 passed |
| Focused judge/requirement/governance regressions | 185 passed + 69 passed |
| Frontend unit tests (`vitest run`) | 2 files, 10 tests passed |
| Frontend production build (`tsc -b && vite build`) | Passed (pre-existing chunk-size warning only) |
| OpenAPI export diff vs baseline | Identical |
| Live smoke: WSA-06 composer + conformance E2E | 4 passed |
| Live smoke: one Engine analytics turn (`test_chinese_single_season_batting`) | 1 passed |

Live validation was run because runtime import identifiers and the
Composer/Conformance path changed. It was intentionally limited to the two focused
smokes above, not the full live integration suite. **Full live integration was not
rerun after Naming Refactor.** These are historical naming-cycle results; current
documentation-only validation is listed in
[Testing and Validation](../engineering/TESTING_AND_VALIDATION.md).
