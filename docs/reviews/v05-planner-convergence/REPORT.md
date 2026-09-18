# Baseball Agent v0.5 — planner-runtime convergence stop report

Branch: `pi/v0.5-planner-convergence` (created from `pi/v0.4-runtime-invariants`
@ `6866dd36f2f94b89dea4aceff85c3caf825d186d`).

Implementation commit: `6534b18f79cbefb84c69ab816342b18ed7401d58`
(`feat(runtime): planner-runtime convergence surface (v0.5)`).

Status: **`PLANNER_RUNTIME_CONVERGENCE_IMPLEMENTED — READY_FOR_EXTERNAL_DOGFOODING`**.

No merge and no tag were made; `main` is untouched.

## Commits

- `6534b18 feat(runtime): planner-runtime convergence surface (v0.5)`
- `docs(review): record v0.5 implementation SHA` (tip)

## Modules changed

New:

- `app/artifact_runtime/convergence.py` — planner-facing capability/schema/export/attempt
  views, `planning_state`, `PlannerFeedback`.
- `tests/artifact_runtime/test_convergence.py` — 47 architectural convergence tests.
- `docs/adr/0027-planner-runtime-convergence.md`.
- `docs/reviews/v05-planner-convergence/{explore.py,FINDINGS.md,explore.jsonl}`.

Modified:

- `app/artifact_runtime/planner.py` — `PlannerContext` views + rendering; `planning_state`
  scheduling; initial and replan prompts carry capabilities, schema, available exports and
  operational feedback; entity-set export-ref guidance.
- `app/artifact_runtime/engine.py` — builds and passes the convergence context; typed
  conflicts on the Goal + `USER_CONFLICT` events; `BINDING_REJECTED` events;
  `_closed_needs` alternate-route credit.
- `app/artifact_runtime/bindings.py` — `export_compatible` and
  `resolve_bindings_with_gaps` (contract/scope/state compatibility + structured gaps).
- `app/artifact_runtime/recovery.py` — `failure_class`, `replan_hint`,
  `is_structurally_impossible`, `requires_replan`, new outcome mappings.
- `app/artifact_runtime/obligations.py` — `detect_conflicts` (typed conflicts);
  qualification reflection.
- `app/artifact_runtime/analytical_ir.py`, `ir_compiler.py` — structured `Qualification`;
  semantic field-handle resolution.
- `app/artifact_runtime/tools_analytics.py` — entity-set fallback to an explicitly bound
  export; measure rejection as `UNSUPPORTED_OPERATION`.
- `app/artifact_runtime/tools_evidence.py` — Need-directed, span-grounded,
  ambiguity-preserving entity extraction.
- `app/artifact_runtime/sufficiency.py` — conflict-aware summary; alternate-route credit
  for structurally closed Needs.
- `app/artifact_runtime/state.py` (unchanged interface), `app/models/artifact_runtime.py`
  — `UserConflict`, `Goal.conflicts`, `UNSUPPORTED_OPERATION`, `IDENTITY_AMBIGUOUS`.
- `app/tools/entity_lookup.py` — preserve dictionary ambiguity instead of dropping it.
- `docs/artifact-runtime.md`.

## Diagnosed convergence problems

1. Convergence information existed in Python but was invisible to planning: available
   exports/contracts, prior ToolOutcomes, obligation coverage, conflicts and scope
   mismatches were not represented to `LLMPlanner` (which received only gap strings on
   replanning).
2. `SchemaCatalog` worked mainly as an execution-time rejection layer; field role/meaning/
   entity/allowed-operations, table grain/coverage and truthful tool restrictions were not
   surfaced to planning, and the trusted semantic field resolver was dead code.
3. Bindings were accepted by export *type* alone; entity namespace, population, membership,
   game type, season, window and contract were unchecked, with no structured gap.
4. Recovery collapsed: `classify("UNSUPPORTED_CAPABILITY")` was `INTERNAL_FAILURE`; an
   unsupported operation was indistinguishable from a permanently unsupported capability;
   identity ambiguity had no class; the scheduler could re-propose an impossible action; a
   failed-but-substituted core Need blocked `COMPLETE` forever.
5. Evidence extraction was broad (every recognizable entity in a document became one
   population), with no spans and no ambiguity preservation.
6. Contradictory/impossible requests were generic gaps, indistinguishable from capability
   or data limitations.
7. Qualification was one integer; measured-event/game/event minimums could not be
   expressed or validated.

## Architectural changes

Material planner-context and convergence-surface change, recorded in **ADR 0027**. The
frozen boundaries of ADR 0025/0026 are preserved: requested/declared/verified scope,
ToolResult ≠ Artifact, Artifact ≠ Artifact Assessment, Planner ≠ Judge, Planner ≠ terminal
authority, Candidate Knowledge ≠ ACTIVE, Safe Analytical IR, SchemaCatalog allowlisting,
read-only SQL/DuckDB, SSRF protections, durable ToolOutcome, stable identities, claim
grounding and recovery observability are unchanged.

## Planner context model

`PlannerContext` now carries, in addition to the legacy names/flat catalog:

- `CapabilityView`s (truthful restrictions: availability, authority, temporal/population/
  game-type/entity-namespace/measures/required inputs/cost);
- `SchemaTableView`s (grain, coverage, description; per-field name, type, role, meaning,
  entity, allowed operations);
- `ExportView`s (exact export id, accepted state, owning artifact/status, typed contract,
  producer, scope summary, verified dimensions, hard-mismatch flag);
- `AttemptView`s (outcome code, failure class, retryability, detail, next-step hint);
- `PlannerFeedback` (obligation coverage + missing obligations, conflicts, unavailable
  capabilities, blocked/impossible Needs, gaps, remaining iterations).

`render_capabilities/render_schema/render_available_exports/render_feedback` feed both the
initial and replan LLM prompts. `planning_state` classifies each Need as `READY`,
`SATISFIED`, `CLOSED`, `ATTEMPTED`, `IMPOSSIBLE_CAPABILITY`, `BLOCKED_WAITING`,
`DEPENDENCY_REJECTED` or `NO_CAPABILITY`; the deterministic scheduler only proposes
`READY` Needs.

## Capability / schema exposure model

Semantic, bounded and trustworthy, never an authorization channel. The planner sees field
meanings, roles, types, entities, allowed operations, table grain/coverage and tool
restrictions. Physical identifiers remain controlled by the deterministic compiler; the
planner may name a catalog field or a canonical semantic handle, and
`field_resolver.resolve_field` maps the handle to a trusted catalog field. Unknown handles
still fail `UNKNOWN_FIELD`; unknown tables still fail `UNKNOWN_TABLE`.

## Artifact binding model

`bindings.export_compatible` validates the export's accepted state, the owning Artifact's
status and the typed contract/scope (entity namespace, population, membership, game type,
season, window overlap). `resolve_bindings_with_gaps` returns the chosen bindings plus
structured incompatibility reasons; the engine emits `BINDING_REJECTED`. Ambient binding
remains removed. When a model-supplied `entity_set.export_ref` is not a real export id,
`local_analytics` falls back to the compatible export already bound explicitly from the
declared dependency — still binding, never ambient injection.

## Evidence extraction model

`EvidenceEntityTool` accepts an explicit `focus` (Need-directed). It scans only grounded,
accepted evidence text, resolves the requested mentions (using surrounding evidence to
disambiguate), keeps unrelated entities out of the population, records the source spans
that justified each value, and preserves same-surface ambiguity as candidate identities
rather than guessing. Without `focus` it falls back to a broad scan but still records spans
and preserves ambiguity (`IDENTITY_AMBIGUOUS`). `EntityResolutionTool` likewise surfaces
candidate identities and unresolved mentions instead of silently dropping them.

## Recovery / replanning model

`recovery.failure_class` maps outcome codes to `RETRYABLE_SOURCE`, `RETRYABLE_MODEL`,
`WRONG_BINDING`, `UNKNOWN_SCHEMA`, `UNSUPPORTED_ANALYSIS`, `SCOPE_MISMATCH`,
`IDENTITY_AMBIGUITY`, `INSUFFICIENT_EVIDENCE`, `POLICY_BLOCKED`, `UNSUPPORTED_CAPABILITY`,
`VALID_EMPTY` or `INTERNAL_FAILURE`, each with a bounded `replan_hint`. A capability is
marked unavailable only for a policy block, a missing registry capability, or a provider
advertised unavailable. A structurally closed core Need no longer blocks `COMPLETE` once
the frozen obligations are independently verified by other accepted work (deterministic
outcome + obligation coverage, not Planner self-approval).

## Multilingual / invalid-input handling

Deterministic components operate on typed representations (obligations, scope, IR,
qualification, conflicts); the multilingual lexical anchors remain a safety net, not the
semantic authority. Typed conflicts (impossible window, contradictory thresholds, future
result) are derived structurally and cannot reach `COMPLETE`. A live contradictory request
became a clarification; a live impossible/future request became a typed `FUTURE_RESULT`
conflict with status `FAILED`.

## Tests added

- `tests/artifact_runtime/test_convergence.py` (47 tests): capability/schema exposure,
  planner-prompt content, semantic field resolution, scheduling states, binding
  compatibility, recovery classes/hints, Need-directed extraction, DB↔Web value flow,
  typed conflicts, qualification semantics, multilingual convergence, and end-to-end
  LLM-planner replanning driven by failure feedback plus alternate-route recovery.

## Test results

- Full suite: **720 passed, 164 subtests** (`python3 -m pytest tests -q`, ~22s).
- `tests/artifact_runtime/test_v04_invariants.py` + `tests/security` + `tests/persistence`
  + `tests/knowledge`: **151 passed, 41 subtests**.
- `python3 -m compileall -q app tests` clean.
- Secret-scan and governance tests pass.
- Live Parquet validation: compiled `FULL`, window predicate present, execution `OK`,
  result matched an independent DuckDB reference exactly.
- Live PostgreSQL read-only executor reachable (bounded `COUNT(*)` succeeded).
- Live LLM sessions: contradictory → `WAITING_FOR_USER`; impossible/future → typed
  `FUTURE_RESULT`, status `FAILED`, no fabrication. Web research was unavailable in the
  sandbox and was correctly classified `SOURCE_TRANSIENT` (SSRF controls not relaxed).

## Self-dogfooding categories used

simple vs compositional; single-source vs multi-tool; DB-first vs Web-first;
historical/current; ranking/comparison/derived; ambiguous identity; missing capability;
impossible request; contradictory request; malformed request; multilingual; mixed;
follow-up/changed constraint; unsupported analytical operation; valid zero-result. The
exploratory corpus lives under `docs/reviews/v05-planner-convergence/` and is not encoded in
any production prompt or branch.

## Failures still found

- The live model invented an entity that was not in the request and used a Need id where an
  export id was required. The runtime refused to fabricate evidence; the export-id mistake
  is now recovered from the explicit dependency binding, and the entity invention remains a
  model-quality limitation.
- The configured model was very slow (~3–4 min/completion), which limited live sampling.
- Live web research was unavailable in the sandbox.

## Known limitations

- The convergence surface guides planning; it cannot make an LLM Planner correct.
- No automatic alternate-route *search*: the Planner must propose the alternate (the credit
  rule only prevents a closed route from blocking a verified obligation).
- No full model/provider token-cost ledger.
- Historical/official roster membership, multi-source joins, distinct-count and window
  operators remain out of scope.
- The default independent Judge is deterministic-contextual; an LLM Judge seam exists but
  is not wired by default.

## Required questions

1. **Can Planner still invent a nonexistent executable source/table/export?** It can name
   them, but they cannot execute: unknown tables/fields fail `UNKNOWN_TABLE`/`UNKNOWN_FIELD`,
   and an unresolvable export reference yields a structured `MISSING_ENTITY_SET` gap. The
   available-exports list is rendered into the prompt; no invented export executes.
2. **Can Planner discover an existing compatible Artifact without ambient binding?** Yes —
   through the declared dependency and the `ExportView` list, with scope/contract
   compatibility checked by the binder.
3. **Do specific Artifact values genuinely affect downstream Tool requests?** Yes — bound
   upstream values parameterize the downstream request; a test proves two different DB
   values produce two different Web queries.
4. **Can Web/Knowledge evidence produce a narrowly grounded structured input?** Yes —
   focus-directed extraction returns only relevant resolved identities with source spans.
5. **Can DB results genuinely alter a later Web research action?** Yes — the bound
   `STATISTICAL_RESULT` value is rendered into the research query (tested).
6. **Can ToolOutcome codes cause materially different replanning?** Yes — failure classes
   and replan hints differ per code; an impossible capability is not re-proposed; a
   `POLICY_BLOCKED` capability is blacklisted.
7. **Can a dependent Need run without its required accepted binding?** No — the binder only
   binds compatible accepted exports from declared dependencies; otherwise the action
   fails `INPUT_UNRESOLVED`/`MISSING_ENTITY_SET`.
8. **Can multilingual paraphrases converge onto the same typed execution semantics?** Yes —
   equivalent typed obligations/conflicts for equivalent English/Chinese inputs (tested).
9. **Can contradictory or impossible requests accidentally become COMPLETE?** No — typed
   conflicts force `core_goal_supported = false`; live validation confirmed.
10. **Can unavailable capability be distinguished from unavailable data?** Yes —
    `UNSUPPORTED_CAPABILITY`/`POLICY_BLOCKED` vs `EMPTY_RESULT`/`COVERAGE_UNAVAILABLE`, with
    distinct failure classes.
11. **Can an alternate route satisfy an original obligation without silently changing its
    meaning?** The frozen obligation coverage (`_reflected`) and the Judge still decide;
    the alternate must independently cover the obligation. Live/deterministic tests confirm.
12. **Can valid empty results be distinguished from execution or coverage failure?** Yes —
    `EMPTY_RESULT`/`VALID_EMPTY` vs `SOURCE_TRANSIENT`/`COVERAGE_UNAVAILABLE`.
13. **Are Judge and State Projection still authoritative after Planner improvements?** Yes —
    the Planner only produces Needs/proposals; the Judge runs on every candidate and the
    State Projector owns terminal truth.
14. **Did any safety, persistence, scope, knowledge or grounding invariant regress?** No —
    full v0.4 invariant, security, persistence and governance suites pass unchanged; no
    boundary was relaxed.
15. **Which generalization weaknesses remain?** Model-dependent semantic invention; no
    automatic alternate-route search; limited operator/join coverage; no full cost ledger;
    live web availability.
