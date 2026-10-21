# Specification (MVP + evolving contracts)

Architecture Baseline v1 implementation reference:
`1d8cb3ad0ec786b48c82de830c6ad68b229c1511`.
Historical milestone sections also contain accepted future designs, especially M3
budget/cache behavior. They are not blanket implementation claims. Read
[Implementation Status](engineering/IMPLEMENTATION_STATUS.md),
[Architecture Overview](architecture/ARCHITECTURE_OVERVIEW.md) and the
[Domain Concept Registry](architecture/DOMAIN_CONCEPT_REGISTRY.md) for verified
current behavior and exact Python names. Requirement means `UserRequirement`;
ContentArtifact means `RetrievedDocument`; runtime context is `ExecutionContext`.

## 1. Runtime entry points

```
python3 -m app.cli chat                       # interactive Chinese/English chat
python3 -m app.cli dev --trace-level verbose  # same runtime + observability
python3 -m app.cli ask "问题"                  # single turn (scriptable)
python3 -m app.cli doctor                     # live source availability
```

All entry points call `app.runtime.engine.Engine.ask(session_id, text)`.

## 2. Contracts (pydantic, `app/contracts/`)

### 2.1 Reference
`Reference(ref_type, ref_id, source, span, metadata)`.
Types: `USER_MESSAGE, USER_SPAN, CONVERSATION_MESSAGE, ARTIFACT, ARTIFACT_FIELD,
ARTIFACT_EXPORT, WEB_EVIDENCE, KNOWLEDGE_ITEM, SQL, DERIVED_COMPUTATION, TOOL_REQUEST,
TOOL_RESULT, CLARIFICATION_ANSWER, ASSUMPTION, GOAL, NEED, CLAIM`.

### 2.2 SemanticInterpretation
```jsonc
{
  "raw_query": "2025年季后赛大谷翔平表现如何",
  "language": "zh",
  "understanding": "open text of what the user wants",
  "entities": [{"mention": "大谷翔平", "type_hint": "player", "ref": "user-span-0"}],
  "time_expressions": [{"text": "2025年", "kind": "season"}],
  "analysis_requests": [{"text": "季后赛表现如何", "objective": "overall postseason performance"}],
  "evidence_requests": [],
  "constraints": [{"kind": "season", "value": 2025, "source_ref": "user-span-2"}],
  "population_hints": ["POSTSEASON"],
  "ambiguities": [],
  "user_assertions": [],
  "references": [...]
}
```
`structured_hints` never overrides `raw_query`; unknown concepts are preserved as text.

### 2.3 Goal / UserRequirement
`UserRequirement(id, kind, statement, required, source, references, supersedes)`.
Kinds: `ENTITY, SEASON, TIME, GAME_POPULATION, MEASURE, ANALYSIS, EVIDENCE, CONSTRAINT`.
`required=True` means material to completion. Requirements are created by the
Semantic/Requirement layer, not by the Planner.

### 2.4 PlannerNeed
`PlannerNeed(id, objective, capability, depends_on[], input_requirements[], desired_output,
required_evidence, constraints{}, status, origin, repair_of, reuse_of)`.
No field may contain a physical table/column, SQL text, or an Artifact id. `criticality`
is derived by the runtime.

### 2.5 Artifact
`Artifact(id, kind, structured_data, text_content, exports[], provenance, references[],
requested_scope, actual_scope, lineage[], confidence, status)`.
`Export(id, type, payload, contract{})`.

### 2.6 ToolOutcome
`ToolOutcome(status, code, message, retryable, need_id, tool_request_id)`.
Statuses: `SUCCESS, NO_DATA, FAILED, BLOCKED`. Codes are stable
(`UNKNOWN_ENTITY, IDENTITY_AMBIGUOUS, INPUT_UNRESOLVED, PSEUDO_REFERENCE, UNKNOWN_FIELD,
UNSUPPORTED_OPERATION, SCOPE_MISMATCH, SOURCE_UNAVAILABLE, BUDGET_EXCEEDED, INVALID_IR,
VALID_EMPTY, INTERNAL_FAILURE`).

### 2.7 Scope
`Scope(entities[], season, game_types[], game_population, date_range, measure, source,
source_coverage)`. `compare_scope(requested, actual) -> ScopeComparison(gaps[])`.

### 2.8 Verification
`RequirementAssessment(requirement_id, status, authority_status, coverage,
support_artifact_ids[], supporting_exports[], support_refs[], gaps[], coverage_gaps[],
conflicts[], inspection_ids[], note)`. The Judge owns this per-Requirement conclusion;
ancestry itself remains on `UserRequirement.provenance`.
`GoalStatus = COMPLETE | LIMITED | FAILED | WAITING_FOR_USER`.
`AnswerProjection(goal_status, claims[], limitations[], unresolved_need_ids[], clarification)`.
`Claim(text, support_refs[], confidence, scope)`.

## 3. Planner output contract

```jsonc
{
  "needs": [
    {"id": "n1", "objective": "resolve 大谷翔平 to a canonical player id",
     "capability": "entity_resolution",
     "depends_on": [], "input_requirements": [], "desired_output": "PLAYER_ID_SET",
     "required_evidence": "canonical identity", "constraints": {},
     "criticality_hint": "core"}
  ],
  "note": "optional short reasoning summary, no chain of thought"
}
```
Validation: unknown capability -> need rejected with `UNSUPPORTED_CAPABILITY`; depends_on
to unknown need -> `DEPENDENCY_REJECTED`; pseudo reference -> rejected.

## 4. Database analytical lane

`AnalyticalIntent` (open) -> bounded SchemaCatalog -> IR candidate ->
`AnalyticalQuery` (Safe IR) -> validation -> compile -> guard -> execute.

Safe IR operations: `Filter, EntitySetFilter, DateFilter, PopulationFilter, GroupBy,
Aggregate(COUNT|COUNT_NON_NULL|COUNT_IF|AVG|SUM|MIN|MAX), DerivedExpression
(ADD|SUB|MUL|DIV|SAFE_DIV|PCT|DIFF|RATE), Sort, Limit, Qualification`.
Conditions: `COMPARE, BETWEEN, IN, NULL, AND, OR, NOT`.

Compiler output is a single `SELECT` (no subquery DML), parameter-free with literals
validated by type/enum. The guard parses with `sqlglot` and rejects any non-read statement,
comment, multi-statement, or forbidden keyword.

Result envelope:
```jsonc
{"artifact_id": "...", "schema": [{"name": "...", "type": "..."}], "rows": [...],
 "row_count": 3, "requested_scope": {...}, "actual_scope": {...},
 "provenance": {...}, "sql": "...", "ir": {...}, "lineage": [...], "exports": [...]}
```

## 5. Web lane
`WebRequest(objective, query, hints{}, required_evidence, references[], max_fetches)`.
Pipeline: build queries -> search backend -> dedupe -> SSRF check -> fetch page body ->
extract text/spans -> `WEB_EVIDENCE` artifact. Errors classify as
`SOURCE_UNAVAILABLE / FETCH_BLOCKED / POLICY_BLOCKED / BUDGET_EXCEEDED / NO_RESULTS`.

## 6. Multi-turn protocol
`ConversationState(active_goal_id, goals{}, pending_clarification, recent_entities,
accepted_context, message_log[])`.
Turn classification is decided by the conversation layer (LLM assist + deterministic
guards). A clarification answer is matched to `pending_clarification.goal_id` and the Goal
is resumed with the answer bound to the original requirement.

## 7. Evidence routing
`route_evidence(requirement, sources)` returns the candidate capability classes. It never
depends on the calendar year alone.

## 8. Acceptance for the MVP
- Chinese question -> Chinese grounded answer via the real runtime.
- Simple player identity, single-season stats, postseason stats, one real web research.
- Independent multi-turn question and one real follow-up.
- No canned answers, no synthetic data.

## 9. Measure verification and binding contract (Phase 3)

### 9.1 Measure semantics
`app/capabilities/analytics/measures.py` owns the canonical vocabulary: measure key,
label, definitions, zh/en aliases and derivability. It recognises base quantities and
derived expressions structurally from the validated Safe IR, so a measure is never
attested from a SQL column name or a model description alone.

- `UserRequirement(kind=MEASURE)` value: `{key, label, derivable, matched, note}`.
- `Scope.measures: list[str]` carries the machine-checkable canonical keys.
- A requested measure that is not derivable is a typed failure (`UNSUPPORTED_MEASURE`),
  never silently replaced by a similar metric.
- A `DATABASE_RESULT` carries `structured_data.actual_measures` (per output column:
  `column, key, kind, verified, source, definition`), `measure_keys` and
  `requested_measure_keys`; the export contract carries `measures`.

### 9.2 Binding contract
`RuntimeBinder.bind(need, needs, artifacts, *, requested_scope=None)` returns
`BindingResult(bindings, unresolved, rejected, failures)`. `failures` are typed
`BindingFailure(need_id, dependency_id, code, detail, channel, required, retryable)`.
Codes: `ARTIFACT_NOT_ACCEPTED`, `EXPORT_TYPE_INCOMPATIBLE`, `EMPTY_ENTITY_SET`,
`ENTITY_INCOMPATIBLE`, `SCOPE_INCOMPATIBLE`, `UNRESOLVED_REQUIRED_INPUT`. A required
failure blocks a CORE Need and cancels a SUPPORTING one; it is never executed anyway.

`Outcome` codes at the runtime boundary map the binder codes into the taxonomy
`UNRESOLVED_REQUIRED_INPUT` / `SCOPE_MISMATCH` (plus the capability's own
`UNSUPPORTED_MEASURE` / `MEASURE_MISMATCH`).

### 9.3 Failure taxonomy
`UNKNOWN_ENTITY`, `AMBIGUOUS_ENTITY`, `UNRESOLVED_REQUIRED_INPUT`,
`UNSUPPORTED_MEASURE`, `MEASURE_MISMATCH`, `SCOPE_MISMATCH`, `QUERY_EXECUTION_FAILURE`.
`NO_RESULTS` is reserved for an evidence search that genuinely returned nothing.

### 9.4 Verification invariants (acceptance)
- A wrong measure can never satisfy a `MEASURE` requirement, and a `DATABASE_RESULT` claim
  is suppressed while that artifact cannot attest the requested measure.
- An unresolved or ambiguous entity can never produce an unfiltered (league-wide) query.
- A scope-incompatible artifact is rejected at binding, not silently used.
- A non-integer season is a typed `SCOPE_MISMATCH`, not a crash.
- An aggregate over an empty population is `VALID_EMPTY` (`empty_population=True`), not a
  zero-valued metric, and it confirms no scope.

## 10. Cognition vs. Evidence (Phase 4)

The corrected principle is: **constrain factual commitment and privileged execution, not
reasoning or evidence discovery.**

### 10.1 Epistemic categories
`EpistemicStatus = SUPPORTED | INFERRED | BACKGROUND | HYPOTHESIS | UNKNOWN`.

- `Hypothesis(statement, origin, basis, confidence, entities, requirement_id, ref)` is a
  candidate explanation/entity/direction (`prior | user | context | search_snippet |
  knowledge`). It may guide the Planner and discovery capabilities. It is never evidence
  and is never emitted as `SUPPORTED`.
- Evidence carries `source`, provenance, scope, verification state, supporting content and
  a `Reference`.
- A `Claim` carries `epistemic`, `support_refs`, `requirement_ids`, `caveat`. A `SUPPORTED`
  claim has at least one real reference; a `BACKGROUND` claim has none.

### 10.2 Identity levels
`IdentityLevel = CANDIDATE | RESOLVED | CANONICAL`.

- `entity_resolution` always emits an identity artifact with `resolved`, `candidates`,
  `unresolved`, `ambiguous`, `identity` and both an `ENTITY_MAPPING` export (always) and a
  `PLAYER_ID_SET` export (empty when no canonical id exists).
- `local_analytics` and `dynamic_fact` require `CANONICAL` (a typed id) and enforce it
  through the Binder. `web_research` and `shared_knowledge` accept candidate names,
  aliases and plain descriptions.
- A dictionary miss is a local fact, not an understanding failure.

### 10.3 Requirement completion
`RequirementCompletion = GROUNDED | VALUE | SCOPE | IDENTITY | EXPLANATION`.

- `SEASON/TIME/GAME_POPULATION -> SCOPE` (any grounded artifact may pin the scope).
- `ENTITY -> IDENTITY` (candidate is enough for research; canonical is enforced by the
  Binder at execution).
- `MEASURE -> VALUE` (strict, machine-checked).
- `ANALYSIS -> GROUNDED`, or `EXPLANATION` for explanatory/historical questions.
- On an explanatory question a measure alias found in the wording is **supporting**
  (`required=False`) unless the user explicitly asked for a value or the interpreter
  emitted an explicit measure constraint. Definition questions never create measures.
- High-risk topics (numbers, current status, injury/transaction, doping/scandal,
  controversy, allegations) force `EXPLANATION` requirements to require evidence and
disable background.

### 10.4 Planner recovery
A failed Need yields a typed reason (binding code, capability code, retryability,
classification). The Planner receives it and may replace the capability with another
permitted path (e.g. non-derivable local metric -> `web_research`). Recovery is bounded by
`RuntimeConfig.max_rounds` (capped at 5) and stops when core requirements are satisfied or
no new work is proposed. A binding failure is local to its Need; it never globally fails
the Goal by itself.

### 10.5 Web research taxonomy
`web_research` accepts `objective`, `entity_names`, `candidate_names`, `user_wording`,
`target_information`, `mentions`, `search_terms`. It distinguishes:

- `SEARCH_BACKEND_UNAVAILABLE` — transport failure of all attempted backends (retryable);
- `NO_RESULTS` — backends responded and found nothing (retryable);
- `SOURCE_UNAVAILABLE` — hits existed, no page could be retrieved (retryable);
- `IRRELEVANT_RESULTS` — pages retrieved but none mention the subject (retryable);
- `SUCCESS` — at least one subject-relevant grounded page.

Findings carry `grounded` and `relevant`. Snippets never ground a claim. The artifact
records `historical_capable` and `source_quality`; the runtime must not claim full open
historical web research when only recent feeds are available (Wikipedia provides historical
encyclopedic retrieval; publisher feeds provide recent news).

Phase WR2 (ADR 0026–0030) extends discovery, routing, ranking, budgeting and structured-source
handling behind this same capability **without changing this taxonomy**; see §13.

### 10.6 Claim-level verification
- A `DATABASE_RESULT` claim requires the artifact's own entity scope and attested measure
  to match the obligation it serves. A mismatched artifact is dropped; sibling artifacts are
  not.
- A `WEB_EVIDENCE` claim requires a grounded finding that matches the subject and, for a
  requested measure, the measured term.
- Independent supported claims survive when another requirement is unresolved.
- Goal status: `COMPLETE` iff all required requirements are `SATISFIED`; `LIMITED` when some
  are supported/partial or a non-high-risk explanation is answerable from labelled
  background; `FAILED` only when no permitted path produced anything and background is
  disallowed.
- `AnswerProjection.background_ok` / `background_topic` and `hypotheses` are carried to the
  composer. The composer may add a clearly-labelled, citation-free background section only
  when `background_ok`; it never adds numbers or dates to it.

### 10.7 Safety boundaries kept from Phase 3
Measure verification, Binder enforcement, SQL guard, read-only execution, SSRF guard,
reasoning-budget bounds, and the ACTIVE/Candidate knowledge split are unchanged. Phase 4
changes *where* those gates apply, not whether they exist.

## 11. Runtime dataflow integrity (Phase 5A)

### 11.1 Contextual entity references (F1, ADR 0018)
`EntityReference` is built deterministically from the semantic message plus accepted
conversation context before requirements are constructed. It preserves the user's `surface`
form, the `canonical_id`/`canonical_name`/`namespace`, the resolution `status`
(`CANDIDATE`/`RESOLVED`/`CANONICAL`), `identity_verified`, `source`, `antecedent`,
`carried_from_goal` and the user-span `Reference`. `UserRequirement.entity_ref` and the
`entity_resolution` request `mentions` carry it end to end. A contextual reference resolves
only a unique antecedent; ambiguity is marked and clarified, never guessed, and a candidate
name never becomes a canonical id.

### 11.2 Artifact projection (F2, ADR 0019)
`artifact_projection` accepts `RANKED_ENTITY_SET` / `STATISTICAL_RESULT` / `DERIVED_MEASURE`
and, under a recorded `SelectionRule` (`TOP`/`INDEX`/`ENTITY_ID`), exports a `SEARCH_QUERY`
(plus `ENTITY_MAPPING` and, when a canonical id exists, `PLAYER_ID_SET`). The export carries
the source artifact/export id, selected row identity, selection rule, canonical id,
searchable name, original scope and lineage. The Binder is unchanged and remains strict;
`web_research` / `entity_resolution` consume the projected `SEARCH_QUERY`.

### 11.3 Enumeration completeness (F5, ADR 0020)
`Artifact.enumeration: EnumerationContract` records population, qualification, ordering,
applied limit, returned row count, completeness (`COMPLETE`/`TRUNCATED`/`UNKNOWN`), stable
identity columns and source scope. An analytics Need feeding a `python_compute` Need is
compiled with a bounded complete scan instead of an intermediate top-N. `python_compute`
refuses a `global_comparison` over incomplete inputs (`INCOMPLETE_ENUMERATION`), inherits
the worst input completeness into its derived artifact, and records each input scope in
`source_scopes`.

### 11.4 Regression guarantees
General contract/property tests cover: contextual entities across turns; true ambiguity not
forced; independent turns inherit nothing; the Binder rejects rank→web without a converter
and accepts it through `artifact_projection`; projected lineage; local top-N not masquerading
as global; cross-source scope preservation; and non-regression of the SQL guard and measure
verification. Real cross-tool execution (real DB → projection → strict Binder → consumer) is
covered by live integration tests.

### 11.5 Operation-aware completeness and projection safety (Phase 5A closure)

`GlobalOperation(kind, identity, partition_by, dedup_key)` declares the semantics of a
cross-artifact computation (`LOCAL`/`EXTREMUM`/`RANK`/`DELTA`/`JOIN`/`UNION`/`UNKNOWN`).
`python_compute` runs `check_compatibility(operation, inputs)` before computing:

- all contributing inputs must be `COMPLETE`;
- shared identity domain, population and qualification are required;
- `RANK`/`UNION`/`EXTREMUM` require disjoint partitions or an explicit dedup proof;
- `DELTA` requires a proven per-entity identity across distinct partitions;
- `JOIN` aligns identities within one population;
- same-source/same-window inputs are rejected (`SOURCE_OVERLAP`);
- an unclassified (`UNKNOWN`) multi-input operation fails closed.

Different seasons and different sources are never automatic mismatches; they are judged
against the declared operation. `EnumerationContract` records `ordering_verified`,
`population_count`, `entity_kind`, `entity_namespace` and `source_coverage`. When a grouped
scan fills the 5,000-row budget, a bounded `COUNT(*)` pushdown measures the exact population
so `COMPLETE` is proven rather than inferred; otherwise the contract is `TRUNCATED`.

`artifact_projection` accepts default `TOP` only with a verified ordering (or a single row),
requires explicit `INDEX`/`ENTITY_ID` otherwise, emits a canonical `PLAYER_ID_SET` only for a
catalog-validated player id column with `entity_kind=PLAYER`/`namespace=MLBAM`, and carries
source artifact/export, selected row, selection rule, original scope and lineage on every
export. The runtime deterministically inserts a registered converter when a consumer declares
an input type its dependency cannot supply; the Binder is not relaxed.

## 12. Evidence verification and completion integrity (Phase 5B)

The single execution flow is unchanged:

```text
User Message -> Semantic -> Goal/Requirements -> Planner -> Tool Execution
             -> Artifacts -> Judge -> Answer
```

Phase 5B strengthens `Artifacts -> Judge -> Answer`. No second proposition/evidence graph is
introduced and no capability scheduling changes.

### 12.1 Answer propositions (ADR 0021)

`AnswerProposition` is a derived refinement of a `UserRequirement`, built once per Goal by
`app/verification/propositions.py`. It carries `requirement_id`/`goal_id`, `kind`
(`IDENTITY|SCOPE|VALUE|FACT|EVENT|COMPARISON|EXPLANATION|GROUNDED`), subject, time/population
scope, required information/operation, `mandatory`, dependencies and coverage
(`SATISFIED|PARTIAL|UNSUPPORTED|CONTRADICTED|NOT_ATTEMPTED|BLOCKED`) with supporting and
contradicting claim/artifact/evidence ids. A requirement may carry an explicit
`value.propositions` list to split a compound obligation; otherwise it maps 1:1. It never
schedules or executes work.

`GoalStatus` is computed from mandatory proposition coverage: `COMPLETE` iff every mandatory
proposition is `SATISFIED`; `LIMITED` when material support exists but a mandatory obligation
is partial/contradicted or background is allowed; `FAILED` when nothing material is supported.
`RequirementAssessment` is the first-class Judge output per Requirement. It records authority,
coverage, support references, gaps/conflicts and inspection references. Its final coverage
aggregates all mandatory child propositions: SATISFIED iff all are SATISFIED. Supported
children remain visible when siblings are unresolved. AnswerPackage consumes this conclusion
rather than independently recomputing satisfaction.

For multi-subject statistics, input entity membership is distinct from requested output
units. `RelationSemantics.output_granularity` preserves the interpreted
`PER_SUBJECT|POOLED|UNRESOLVED` meaning. Individual output propositions retain their
`subject_requirement_ids`; canonical acquisition preserves the subject grouping dimension.
Judge admits either a uniquely scoped individual scalar or an independently inspected
identity-grouped row, never a pooled row as evidence for each individual. Missing or
conflicting siblings stay unresolved without erasing independent approved subject cells.
These units are Requirement-local even when interpretation creates separate single-subject
ANALYSIS requests. Existing independent partitions also scope inherited MEASURE units;
a shared acquisition may use the Goal's input identities without importing another task's
requested metrics or admitting its rows. Each subject proof rechecks its owner's subjects.
Unknown multi-subject output semantics remain mandatory and unresolved. The legacy finite
role adapter cannot make omitted role/output metadata bypass that obligation: an otherwise
unshaped multi-entity statistical request remains unresolved in its bound contract.
A POOLED label alone grants no
aggregation authority: explicit sums, means and ranks still require the existing finite
operation contract, individual root cells and replayable ancestry. Backend table projection
uses Claim-linked approved row/field footprints rather than publishing an entire mixed
Artifact. These contracts do not waive unrepresented source clauses or authorize arbitrary
open-language aggregation.

`AnswerProjection` is detached and recursively sealed immediately at the end of
`Judge.evaluate()`. Claims, assessments, coverage, GoalStatus and nested provenance cannot be
mutated by Composer, Conformance, consistency or presentation repair.

An `IDENTITY` proposition is satisfied by a candidate/resolved identity for research; a
canonical id is required only for database claims, which keep their own gate. A statistical
observation does not by itself establish an `EXPLANATION` proposition.

### 12.2 Evidence verification (ADR 0022, amended by ADR 0025)

Web evidence keeps the levels distinct: `SearchHit` (never grounds), `RetrievedDocument`
(URL, provider, title, publication date, retrieval time, status, content hash, bounded
excerpt), `EvidenceSpan` (passage text + offsets + `source_type` + `retrieval_status`),
`EvidenceFinding` (per-span, per-proposition `SUPPORTED|CONTRADICTED|INSUFFICIENT|IRRELEVANT`
with verifier, checks and rationale) and `Claim` (answer-eligible). Phase-4 `EpistemicStatus`
labels are unchanged.

Only a `DOCUMENT_BODY` span of an `OK` fetched document with consistent offsets is
admissible. A search title or snippet may drive discovery relevance but can never satisfy
the subject, requested year/population or required-information conditions. Deterministic
constraints classify each scope as `MATCH`/`MISMATCH`/`UNKNOWN`/`NOT_REQUESTED`: a known
conflict or unresolved scope freezes the finding and the LLM verifier is not consulted; it
can never upgrade a blocked finding. `deterministic_status` and `llm_status` are both
persisted. Every finding's `span_id` resolves to a persisted span and `document_id`; the
Judge drops findings that do not resolve. Retrieval outcomes distinguish backend failure,
no results, source unavailable, irrelevant results, insufficient evidence, partial
support, full support and contradiction. `SUCCESS` requires an inspectable supported span.

### 12.3 Status/answer consistency (ADR 0023, amended by ADR 0025)

After composition, `app/verification/consistency.py` performs a *structured* comparison of
the rendered answer with the accepted claims attached to each mandatory satisfied prose
proposition. It does not re-read evidence and does not call an LLM. A missing mandatory
result triggers a bounded deterministic repair (rendering the verified claims). Presentation
failure does not downgrade or mutate Judge authority. StatusConsistency is presentation
telemetry on TurnResult and diagnostics, not a post-composition write on AnswerProjection.
An upstream governed inconsistency must route to a new Judge evaluation or justified replan
through lifecycle ownership, never `Judge.reconcile_expression`. Missing optional material
never changes governed status. These accepted revised ADR decisions supersede the historical
post-composition downgrade in ADR 0023.

### 12.4 Observability (ADR 0024, amended by ADR 0025)

`app/observe/diagnostics.py` writes a redacted, bounded per-Goal JSON record (search
objective/queries, provider, candidate hits, fetched documents with hashes, failures,
spans, evidence findings, coverage, Judge decision, claim citations, LLM call counts) and
collects latency spans (`web:search`, `web:fetch`, `web:verify`, tool/judge/composer,
`consistency` and `goal_end_to_end`). Redaction is field-aware and value-aware: credential
fields are masked, token-usage telemetry such as `subject_tokens`/`required_tokens`/
`matched_tokens` is preserved, and credentials embedded in URLs, headers, bearer tokens
and cookies are scrubbed. The diagnostics directory/file are created `0700`/`0600`, and
`.runtime/`/`.scratch/` stay out of version control.

### 12.5 Responsibility boundary (ADR 0025)

The Evidence Verifier (inside the evidence-producing tool) answers *what this evidence
establishes*; the Judge answers *whether verified artifacts satisfy the Requirements*; the
Composer answers *how the verified answer is communicated*. They are stages of one flow,
not separate agents. The Judge performs no second semantic re-reading of articles and does
not treat tool success or artifact count as proof. The Composer does not evaluate
completeness and cannot promote an unsupported claim.

### 12.6 Regression guarantees (Phase 5B)

General contract/property tests (no named holdout questions) cover: subject-only pages do
not support an event; wrong-year and wrong-entity evidence is rejected; a fetched passage
preserves URL/offsets/claim support; an unavailable page invents no evidence; conflicting
sources retain the conflict; an explanatory web answer completes without a local
statistical artifact; one correct metric plus one missing metric is `LIMITED`; irrelevant
pages cannot make a multi-part task `COMPLETE`; a retracting Composer is repaired from its
verified claims (current lifecycle repairs/falls back without mutating Judge status); a supported analysis
survives an unsupported causal explanation; and league-level history does not require
player identity resolution. Phase 5B closure adds adversarial fixtures for title-only
support, year/population conflicts, LLM override, span lineage, mandatory-coverage
completion, optional-gap non-downgrade, no-second-LLM consistency, and field-aware
diagnostic redaction. Replayable Web fixtures are separated from network-backed
integration tests in the closure report.

### 12.7 Final repair contracts (2026-09-27)

`RetrievedDocument.body_text` stores the extracted fetched body in the Web Artifact.
The verifier and Judge require a real, successful document in that Artifact's document
collection, matching ids, retrieval metadata and an exact `body[start:end] == span.text`.
The document excerpt is a compatible replay source only when it contains the full span.
Missing collections fail closed. Findings bind the decision to `span_hash` (SHA-256 of
span text) and `proposition_signature` (the semantic subject/metric/scope obligation).
Judge validates these references and signatures without semantically re-verifying articles.
Nested finding records must equal the admitted persisted finding. Only admitted SUPPORTED
span material becomes a Web Claim.

Simple named-subject metric clauses are attributed deterministically, including both
subjects of comparisons. Known ownership/value conflicts block model refinement.
Requested subject, metric, value and scope must match one compatible predicate. A value
in an UNKNOWN-scope predicate cannot borrow scope from a neighboring fact of the same
player. Values are bound to metric labels, not arbitrary numbers in the predicate.
Leading sentence scope may govern comparisons within that sentence; explicit local
scope overrides it, and separate sentences do not inherit it. Unsplit predicates with
multiple scopes remain UNKNOWN. Unresolved metric attribution freezes LLM refinement;
semantic numeric facts must pass this same relationship gate.
Unresolved metric attribution is not support; an LLM support decision must identify
passage-grounded subject, metric/predicate, value where present and scope. Existing
year/population/provenance gates remain frozen.

Mandatory VALUE participates in expression checks. `Claim.answer_values` carries
user-required measures with requirement id, entity, metric, value, unit and scope from
accepted structured results; auxiliary columns are excluded. Unstructured numeric claims
are checked by complete expression units, with all required values and before/after order.
The current production path may generate a `ComposerDraft` for COMPLETE or LIMITED
projections, independently review it against the same immutable `AnswerPackage`, then
regenerate or use governed rendering under Orchestrator bounds (§13.13–13.14).
Bounded accepted-Claim rendering remains available for consistency repair; its
16,000-character bound includes header/citation markers and omits whole facts rather
than truncating them. This is not the only composition path. Original polarity and
claim source numbering are preserved. No factual authority is introduced by rendering.
Negative accepted facts stay negative. Exact standalone units, a small explicit before/after
formatting grammar, and structured answer_values rows can confirm equivalent expression;
claim substrings, numeric/token overlap and denial vocabularies cannot.
Unconfirmed surrounding prose (including optional commentary) triggers replacement with
all accepted mandatory results, rather than appending results beneath a possible denial.
Missing optional details do not change coverage or final status. Unsupported translations
or paraphrases use this same rendering path; arbitrary natural-language equivalence is
not guaranteed. COMPLETE output may retain the language of accepted Claims, and table
whitespace/optional commentary may be simplified. These are presentation limitations.
Uncertain expression triggers bounded repair or governed package fallback owned by
Orchestrator. Consistency reports presentation telemetry on `TurnResult`; there is no
production `Judge.reconcile_expression` and no post-composition mutation/downgrade of
the sealed Judge projection. An upstream inconsistency requires a new governed pass
through lifecycle ownership. Explicit proposition specs inherit the parent
Requirement's required flag.

Diagnostics default to metadata, validated ids, hashes, offsets, typed outcomes, coverage,
latency and measured numeric usage. All unanticipated strings and non-operational numbers
are omitted recursively, including exception fallbacks and developer trace export. URLs,
queries, user/history/answer prose, lexical tokens and body content are omitted. Credential
scrubbing also normalizes percent-encoded keys. Files/directories are 0600/0700, including
creation, and symlink file writes fail closed.

Default diagnostic records expire after seven days. Explicit developer construction of
`DiagnosticRecorder(..., research_content=True)` now selects a metadata-only
`restricted-research/` directory. Arbitrary research-body persistence is disabled in this
bounded closure, including mutated records at final write. Structured credential fields
are replaced as whole values before nested traversal, including encoded keys and nested
objects; raw serialized JSON/article strings are not permitted to bypass that rule.
Usage and operational metadata remain available. This mode retains private permissions
and expires after 24 hours; Runtime does not enable it. Expiration runs on the next
recorder write to the same directory, leaving
legacy records untouched. Idle deployments must arrange periodic cleanup; retention is
not guaranteed without that lifecycle activity. Fetched body evidence remains in the
existing runtime Artifact store for lineage replay; diagnostics no longer copy it.

The shared LLM client captures usage from every received provider response (including
JSON parse retries) and text calls, with provider host, response model and call role/category.
Runtime records these per-turn measurements, including pre-Goal semantic calls. Missing
provider fields are null with `usage_available=false` when none are measured, never zero.
Network-failed attempts also record unavailable usage. No prompt or raw payload is stored.

## 13. Web Research v2 (Phase WR2, ADRs 0026–0030)

This section specifies the accepted Web Research v2 design. It **adds** to §5 and §10.5; it
does not amend §12 or ADR 0021–0025. The single execution flow and the Phase 5B evidence
contract are preserved unchanged. Architecture detail:
`docs/architecture/WEB_RESEARCH_V2.md`.

### 13.1 One capability, internal sources (ADR 0026)

`web_research` remains the only Planner-visible research capability
(`accepts: SEARCH_QUERY`, `produces: WEB_EVIDENCE`). Provider selection, routing, ranking,
fetching and budgeting live inside `WebResearchTool.execute()` in an internal `app/web/`
package (`sources/`, `router.py`, `query_plan.py`, `ranking.py`, `fetch.py`, `cache.py`,
`budget.py`). No new Planner-visible tool or capability is introduced.

`SourceDescriptor(source_id, kind, discovery, full_body, structured, historical, current,
auth, trust_weight, latency_class, rate_limit_per_min)` declares each source. `SearchProvider`
extends the existing `SearchBackend`/`SearchHit`/`FetchedPage` surface. The Research Router
`route(propositions, scope, freshness, available_sources) -> SourcePlan` is deterministic and
reads only `AnswerProposition.kind`, time/population scope and the existing `is_high_risk()`
markers; it never inspects player or event names.

### 13.2 Analytical vs research responsibility boundary (ADR 0026, ADR 0030)

- `local_analytics` owns aggregation over raw PostgreSQL/Parquet **source data** (Safe IR,
  measure verification, `EnumerationContract`).
- `python_compute` owns bounded temporary computation over **already-accepted Artifacts**
  (combine/join/difference/transform) under the Phase 5A enumeration/scope gates; it does not
  read source tables and is not the default compute path.
- `dynamic_fact` owns operational relation facts with `as_of`.
- `web_research` retrieves evidence only: it never aggregates, ranks or computes derived
  measures. Cross-record computation leaves Web Research entirely. Raw-data aggregation is
  not moved into `python_compute`, and all computation is not forced into Python.
- Structured lookup remains internal to `web_research`.

### 13.3 Retrieval, query planning and ranking (ADR 0027)

`ResearchQueryPlan` variants are facet-tagged: `ENTITY`, `METRIC`, `EVENT`, `INTERVIEW`,
`FRESHNESS`, `MISSING`. Aliases come from entity references and the entity directory;
historical dates come from the structured `time_scope`; mandatory propositions without a
facet are recorded in `facets_missing`. At most one bounded LLM refinement call is allowed and
it preserves the subject prefix.

`CandidateRanker.score(...)` is deterministic and lexical (subject/alias coverage,
required-information coverage, scope coverage, intent, `trust_weight`, freshness, minus
discovery-only/duplicate-domain penalties). `trust_weight` is a float where **a larger value
means a more trusted source** (`1.00` official, `0.85` encyclopedia/reference, `0.55`
publisher news, `0.25` open web, `0.00` discovery-only). Relationship relevance dominates
generic source prestige: the authority term is bounded below the subject/required-information
terms, so an authoritative but irrelevant page cannot outrank a directly relevant credible
source. Proposition-aware `SpanSelector` passage selection (subject, required information,
scope, adjacent-sentence coreference, citation/navigation/metadata avoidance) feeds candidate
admission before the unchanged verifier; low-quality subject-only fragments never reach it.
Ranking is order-stable so concurrent retrieval is reproducible. Fetch uses a bounded ladder
(cache → primary fetch with SSRF guard → transient retry → extraction ladder → alternate URL →
typed failure → provider fallback); a provider outage is never reported as `NO_RESULTS`.

### 13.4 Body-version and offset contract (ADR 0027)

Three versioned artifacts: (1) raw response with
`raw_content_hash = SHA-256(raw_bytes)` stored on `RetrievedDocument.content_hash`;
(2) extracted `body_text = extract(raw_bytes, extractor, extractor_version)` with
`body_hash = SHA-256(body_text)`; (3) `EvidenceSpan` with character offsets into that same
`body_text`, enforcing `body_text[start:end] == span.text` and
`span_hash = SHA-256(span.text)`. Offsets are never raw-byte offsets. A span is valid only
against its `(document_id, body_hash, extractor_version)`; a mismatch forces re-extraction
rather than offset reuse. `body_hash`/`extractor_version` are additive artifact/cache
metadata; §12.7's replay and the evidence contract are unchanged.

### 13.5 Official structured records (ADR 0030)

A structured adapter may only fetch and render. Each record carries a `StructuredRecord`
envelope: `record_id` (deterministic source + endpoint + key fields), `source_id`,
`source_url`, `as_of`, `conversion_version` and verbatim `raw_fields`. The normalized
`body_text` begins with a deterministic provenance header and is a **labeled rendering**, not
a news article and not the unconverted official original. `RetrievedDocument.source_quality`
is `official_structured_record:<source_id>`; `EvidenceSpan.location` is
`structured:<record_id>`; the raw record and envelope are stored under `structured_records`.
Admissibility is unchanged: the rendered body is a fetched `DOCUMENT_BODY` with
`retrieval_status=OK` and exact offsets, so §12.2 `verify_span` still decides support.

### 13.6 Goal-level research budget and deadlines (ADR 0028)

**Accepted M3 target, not current enforcement.** `app/web/budget.py` implements
budget types and the pure clamping helper. Current `ExecutionContext` has no shared
budget handle; Web reports default budget metadata and uses local bounds, not full
Goal-wide reservations/settlement/deadlines. The following describes the target:

A single `GoalResearchBudget` is to be held by the execution context / Goal State, keyed by `goal_id`,
created once per Goal; `WebResearchTool` may only consume it. A Planner replan never resets
it. Each call's `ResearchBudget` is clamped to the remainder
(`effective = min(tool_default, goal_remaining)`). Three clocks are distinct: per-call
provider/fetch timeout, shared soft deadline (stop launching new work, drain in-flight) and
shared hard deadline (cancel in-flight, finalize verified results). In-flight semantics:
per-call timeout bounded independently; cancellation leaves completed futures untouched;
only results completed before finalization and passing the offset contract are admitted;
reserved-but-unstarted calls are released and started calls are counted in a `finally`
settlement. Exhaustion returns a `PARTIAL` `WEB_EVIDENCE` artifact with typed
`BUDGET_EXCEEDED` and a `budget_report`, never a fabricated completion.

### 13.7 Freshness and caching (ADR 0029)

Fact freshness (state-of-the-world: `VOLATILE`/`IMMUTABLE`/`MIXED`, from the Goal) and
document freshness (`publication_date`/`retrieved_at`) are independent axes. The cache is
URL-keyed (plus `extractor_version`); the content hash is a version/validation token, not the
lookup key. `retrieved_at`, `revalidated_at` and `as_of` have distinct meanings and a cache
hit never rewrites `retrieved_at`/`as_of`. `VOLATILE` facts are revalidated within TTL and
carry a valid `as_of`. **M1 defines this contract only; full cache behavior is implemented
in M3.**

### 13.8 Research language policy (M2, ADR 0027)

Four language roles are distinct: `user_language`, `primary_research_language`,
`source_language`, `response_language`. For general MLB research the primary search language
is **English by default regardless of the user's input language**, because MLB official
information, American journalism, historical reporting, interviews and analysis are
predominantly discoverable through English search. This is **English-first, not English-only**;
a user's language must never determine the primary search language.

Context-aware multilingual expansion beyond English fires only when at least one condition
holds: (1) the Goal explicitly requests a regional/language-community perspective; (2) the
likely primary source originates in another language (Japanese interview, NPB/KBO
announcement, Spanish-language local reporting, Korean press conference); (3) English research
leaves mandatory propositions unsupported and a relevant locale/language is known; or (4) the
research target is inherently language/community scoped (slang, memes, nicknames, fan
discourse, community opinion, local terminology). Languages are not a fixed baseball list.

Entity references support language-aware aliases (`canonical_name` + `aliases_by_language`).
Aliases may shape search queries but are **not** evidence and cannot prove identity without the
existing entity-resolution rules. `RetrievedDocument.body_text` and `EvidenceSpan.text` are
always the original source language, and `body_text[start:end] == span.text` still holds; an
LLM translation may only be stored as derived data linked to the original span
(`derived_translation`, `source_span_id`, `source_language`, `target_language`,
`translation_model/version`) and never replaces provenance. Candidate ranking is
language-neutral in authority: English-first controls discovery order, not evidence quality,
so a Japanese first-party interview may outrank an English secondary summary. The Result
Composer answers in the user's current conversation language unless another language is
explicitly requested. `language_policy` and `query_plan` are recorded as additive
`WEB_EVIDENCE` artifact metadata.

### 13.9 Scope, milestones and preserved contracts

Milestones: **M1** contracts, instrumentation, real Wikipedia historical retrieval with body
verification, and the old-vs-new Answer Completion / Evidence Precision / Latency baseline on
a frozen task set; **M2** query facets, relationship ranking (including measurement of the
`trust_weight` benefit), bounded parallel fetch; **M3** budget/deadline enforcement and full
cache; **M4** optional config-gated broad-search provider and structured adapter family.

Preserved: §12 and ADR 0021–0025, the Judge, the Evidence Verifier, the Composer, the Binder,
the SQL guard, measure verification, enumeration completeness, and the Phase 5B independent
acceptance baseline. No embeddings/RAG, no new Agent framework, no named-player/named-event
shortcuts, and no M2–M4 implementation during M1.

### 13.10 Requirement relation semantics (M2 correction)

A Requirement carries three distinct things, without a parallel `VerificationIntent`
subsystem:

```
UserRequirement
├── original_intent        immutable user request
├── statement / scope / mandatory metadata
├── relation_semantics     normalized, capability-agnostic interpretation
└── AnswerProposition(s)   atomic answer obligations
```

`RelationSemantics` (`app/contracts/models.py`) is the canonical machine interpretation of the
relationship a Requirement asks for (`relation_kind`, subject/object/metric refs, time scope,
population, comparison direction, polarity, semantic cues, source text). It is produced once
by `RequirementBuilder` from the requirement's own wording and is copied onto the derived
`AnswerProposition`. `original_intent` preserves the exact user request and is never
overwritten by the normalized interpretation.

`app/semantic/relations.py` owns the relationship vocabulary (CAUSE, EFFECT, QUOTE, STATUS,
FRESHNESS, VALUE, COMPARISON, AGGREGATION, EXPLANATION) as the single meaning consumed by the
Planner payload, `query_plan.py`, `ranking.py`, `span_selector.py`, candidate admission and
verifier preparation. `relation_kind` is a routing signal, not a hard tool rule; each
capability keeps its own proof mechanism and database-derived analytical facts are never
routed through the Web EvidenceVerifier for uniformity.

Verifier preparation may add relational cue tokens to the required-information match only for
CAUSE/EFFECT/QUOTE/COMPARISON/EXPLANATION. STATUS/FRESHNESS are routing/recency signals and
are deliberately excluded so currentness vocabulary cannot masquerade as factual support.
`verify_span`, the Judge, the Composer, the consistency checker and their hard gates
(admissibility, subject, scope, no LLM upgrade past a hard conflict) are unchanged.

`shared_knowledge` is a constrained cross-layer interpretation service (terminology,
multilingual aliases, metric definitions, rules, scouting/tactical terms, relation cues). It
may assist semantic parsing, normalization, Planner routing, query construction/ranking/span
selection and response explanation, but it must not silently satisfy a factual Requirement or
bypass Artifact/Judge governance. Mutable, current, source-specific or externally asserted
facts require an evidence-producing capability.

Closure results and the A–E failure diagnosis are in
`docs/audit/WEB_RESEARCH_V2_M2_CLOSURE.md`.

### 13.11 Answer Proposition decomposition (M2 completion)

Requirement → AnswerProposition is a decomposition, not a one-to-one copy. A Requirement may
produce several atomic propositions, each independently evidence-testable (one subject, one
requested relation, one scope, one required-information slot). A compound CAUSE+EFFECT
obligation is split; a non-relational analysis keeps its completion-derived kind. The
relation label is descriptive: `RelationSemantics` is **multi-label** (`relation_kinds`) and
non-prescriptive, and the original user wording is preserved in `original_intent`.

Natural-language decomposition may use the LLM, but only for genuinely compound wording and
always under deterministic structural validation: malformed relation semantics, empty claims,
missing subject, invented numbers/identifiers, invalid metric references, duplicate factual
identities and pure task restatement are rejected before the proposition reaches the Planner.
Truth is never validated at generation time.

Evidence-context recovery in ``app/web/span_selector.py`` is slot-driven: context expands only
while a proposition slot (subject, relation, time/population scope) is missing, bounded by the
character cap, and preserves exact source offsets. The spanning context is never paraphrased
or translated.

Generated/property tests (`tests/test_generated_propositions.py`) exercise these properties
across recorded seeds. Implementation report:
`docs/audit/WEB_RESEARCH_V2_M2_FUNCTIONAL_COMPLETION.md`.

### 13.12 Temporal scope and source-coverage escalation (M2.5)

Relative temporal expressions are resolved **once** at Requirement normalization time and
shared downstream. A `TemporalScope` is attached to both the Requirement (`resolved_scope`,
with the original wording preserved in `original_scope`) and the Goal
(`resolved_time_scope`, `anchor_date`, `original_time_scope`). `app/semantic/temporal.py` owns
the deterministic resolver and the centrally defined `DEFAULT_WINDOW_DAYS` per information
class; explicit user dates/durations always override inferred defaults, and an unanchorable
relative expression stays explicitly `UNRESOLVED` rather than inventing precision. The Goal
anchor is fixed at creation, so a replan never moves a relative window. Temporal resolution is
descriptive and never selects a capability; the resolved window is rendered into the research
artifact `actual_scope.date_range` so the existing Judge `time` channel can be satisfied.

Source coverage is diagnosed separately from query/ranking/fetch/span/verification. When direct
sources cannot discover the requested relation, `web_research` may escalate to a real broad
discovery surface (public search HTML, keyless, discovery only) under a bounded budget
(`_BROAD_SEARCH_VARIANTS`, `_BROAD_FETCH_BUDGET`). A broad hit is a candidate URL that must be
fetched into a document body and verified; breadth never makes it trusted evidence, a provider
failure is typed and never becomes `NO_RESULTS`, and English-first plus context-specific
multilingual expansion is unchanged (each query plan variant searches in its own language).

Generated seeded tests: `tests/test_temporal_scope_m25.py`,
`tests/test_source_coverage_m25.py`. Implementation report:
`docs/audit/WEB_RESEARCH_V2_M25_FUNCTIONAL_COVERAGE.md`.

### 13.13 Answer composition contract (M2.6)

The Judge → Composer boundary is an explicit **answer navigation contract**
(`app/response/answer_contract.py`). `AnswerPackage` maps each Requirement to its coverage, the
accepted Claim ids and the accepted EvidenceFinding/EvidenceSpan/Document references, plus the
approved factual text and a bounded read-only context. It is a structured map, not a lossy
summary and not a second truth system. The package is a detached, recursively immutable
snapshot containing the full governed Requirements, RequirementAssessments, propositions,
Claims, QualifiedEvidence, unresolved meaning, GoalStatus, limitations, citation/source
mappings and origins. Its content identity is bound to each semantic review together with
the exact Composer draft digest.

Composer labels, citations and supports are untrusted proposals. An absent, incomplete,
malformed or mismatched semantic review cannot authorize any unassessed free prose, including
ANALYSIS or LIMITATION. LLM review owns open-language representation; deterministic code
validates identities/linkage/schema and preserves missing governed content canonically.

The Result Composer may read the approved evidence graph (claims, findings, spans, bounded
surrounding context, source metadata) to write a coherent answer, but its factual authority is
bounded by the Judge-approved Claims. It may synthesise, merge and paraphrase; it may not
introduce a fact, number, date, quote, comparison, cause or attribution that is not approved,
expand a claim's scope, or treat a Document as approved because one span was accepted.

Every satisfied mandatory prose proposition must be expressed in the final answer, for COMPLETE
and LIMITed Goals; a LIMITed answer still carries its supported partial results and clearly
distinguishes what remains unresolved. A retraction, omission, inverted numeric direction or
swapped metric value is not an expression receipt, and an unapproved number triggers a bounded
deterministic repair from the accepted facts. Normal answers do not expose Document/Evidence/
Requirement/Proposition ids, raw queues or internal scores; source metadata stays in the
provenance chain and diagnostics.

The Evidence Verifier, evidence lineage, Judge sufficiency and fact-local attribution remain
protected. Implementation report and generated seeds:
`docs/audit/WEB_RESEARCH_V2_M26_ANSWER_COMPOSITION.md` and
`tests/test_answer_composition_m26.py`.

### 13.14 Explicit bounded lifecycle (governed architecture migration)

`app/runtime/orchestrator.py` owns FINAL / REGENERATE_COMPOSER / REPLAN / FALLBACK.
Production composition permits at most two actual draft-provider attempts (initial plus one
regeneration), with independent Conformance against the same AnswerPackage. Regeneration
feedback contains bounded violation kinds, not an authoritative failed draft. Exhaustion
renders only the governed package. Presentation-only violations never invoke Planner.

Pre-Judge Need repair remains intact. One post-Judge continuation is permitted for unresolved
mandatory assessments when an available untried acquisition family or retryable path exists,
Need budget remains and the continuation window has not elapsed. Needs/outcomes/artifacts
are retained; Planner sees governed gaps and Judge evaluates again into a new immutable
projection. A structural upstream inconsistency allows one bounded new Judge evaluation;
persistent invalid output fails closed. Composer/Conformance never mutate the prior result.

Orchestrator owns presentation consistency/repair and separately records attempts, violations,
decisions, package identity and fallback reasons on TurnResult/diagnostics. This lifecycle
accounting does not yet claim to repair the separate evidence-assessor/provider budget gaps.

### 14 Frontend interaction and presentation (ADR 0033)

The FastAPI adapter is a public command/event boundary around the existing synchronous
`Engine.ask`, not a second semantic engine. REST starts a run or submits a pending clarification;
SSE delivers versioned, replayable run-scoped events. The backend resolves opaque clarification
option IDs into its *own* option text and the existing conversation resume path interprets that
text. Invalid/stale clarification IDs fail closed. The browser cannot construct a Requirement,
identity, filter, qualification, scope, measure or verdict. **Frontend state is not factual
authority.** See `docs/architecture/FRONTEND_INTERACTION_FOUNDATION.md` for the wire contract,
partial-result behavior and implementation tickets.

Public tables require an admitted Judge Claim referencing the exact internal database artifact;
raw SQL/IR/execution receipts and unapproved artifacts are not exported. React renders typed,
Zod-validated presentation artifacts, not executable model output. The current Engine has no
safe token callback: initial status streams live, while answer text is emitted as an approved
single delta on completion. `PARTIAL` is reserved for a future backend-authoritative status;
the current Judge uses `LIMITED` for mixed outcomes and `WAITING_FOR_USER` for clarification.
