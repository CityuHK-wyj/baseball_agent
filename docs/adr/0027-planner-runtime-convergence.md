# ADR 0027: Planner-runtime convergence surface

- Status: Accepted
- Date: 2025-10-02
- Corrects (does not replace): 0026 v0.4 runtime invariants
- Related: 0025 artifact runtime, 0026 v0.4 runtime invariants

## Context

By the end of v0.4 the runtime was good at *rejecting* incorrect plans: requested /
declared / verified scope were separated, the Judge was independent, IR semantics were
faithful, bindings were explicit, and every attempted action had a durable ToolOutcome.
An independent review of the v0.4 direction asked the next question: can a flexible
Planner **converge** from an open-ended request onto the capabilities, schemas, evidence
products and bindings that actually exist?

Inspection of the v0.4 implementation found that convergence information existed in
Python models but was not represented to planning:

1. `PlannerContext` carried only tool names/capabilities, a flat field list and export
   *type* hints. A run's actual exports (with contracts, scope and accepted state), the
   outcomes of prior attempts, obligation coverage and scope mismatches were invisible to
   the Planner. `LLMPlanner.add_needs` received only a list of gap strings, so replanning
   appended another tool call instead of reacting to evidence.
2. `SchemaCatalog` functioned mainly as an execution-time rejection layer. Field
   `meaning`, `role`, `entity`, `allowed_operations`, `grain`, table `coverage` and the
   truthful tool restrictions (`temporal_modes`, `population_modes`, `game_types`,
   `supported_measures`, `availability`) were not surfaced to planning, and the trusted
   `resolve_field` semantic layer was dead code.
3. `resolve_bindings` accepted any export whose *type* matched a derived dependency.
   Entity namespace, population, membership, game type, season, window and contract were
   not checked, and no structured gap was reported for an incompatible export.
4. Recovery classes collapsed: `classify("UNSUPPORTED_CAPABILITY")` fell through to
   `INTERNAL_FAILURE`; an unsupported *operation* was indistinguishable from a
   permanently unsupported *capability*; identity ambiguity had no class at all; and a
   failed-but-substituted core Need blocked `COMPLETE` forever.
5. `EvidenceEntityTool` scanned every recognized entity in a document and turned all of
   them into one population, with no Need direction, no spans and no ambiguity
   preservation.
6. Contradictory/impossible requests were reported as generic gaps; a logical
   impossibility was indistinguishable from a capability or data limitation.
7. Qualification was a single integer `min_rows`; measured-event, game and event
   minimums could not be expressed or validated.

## Decision

Add a bounded, trustworthy **planner-runtime convergence surface**. It is a *view
builder*, never an authority: it does not authorize fields, identifiers or actions, and
it never mutates the catalog, artifact store or attempt journal. The authoritative
components remain `SchemaCatalog` (execution validation), `ToolCapabilityContract`
(admission), `bindings` (explicit input selection), `obligations` (frozen baseline) and
`recovery` (durable outcome taxonomy).

1. **`app/artifact_runtime/convergence.py`** builds `CapabilityView`, `SchemaTableView`,
   `ExportView`, `AttemptView` and `PlannerFeedback`. `planning_state` classifies each
   Need as `READY / SATISFIED / CLOSED / ATTEMPTED / IMPOSSIBLE_CAPABILITY /
   BLOCKED_WAITING / DEPENDENCY_REJECTED / NO_CAPABILITY`.
2. **`PlannerContext`** carries those views plus obligation coverage, conflicts, prior
   attempts, unavailable capabilities and the remaining budget, and renders them for the
   `LLMPlanner` initial *and* replan prompts. The planner sees what was attempted, what
   succeeded/failed and why, which obligations remain, and which exact exports exist.
3. **Semantic field resolution** is wired into the compiler: the planner may name a
   catalog field or a canonical semantic handle; only a trusted catalog field survives and
   the physical name is what reaches SQL.
4. **Explicit binding validation** (`export_compatible`) checks accepted state, artifact
   status and the typed contract/scope (entity namespace, population, membership, game
   type, season, window). Incompatible candidates produce structured `BINDING_REJECTED`
   facts rather than silent type-only binding.
5. **Recovery taxonomy** gains `UNSUPPORTED_OPERATION` and `IDENTITY_AMBIGUOUS` (distinct
   from a permanently unsupported capability), a `failure_class` mapping, and bounded
   `replan_hint`s. A structurally impossible capability is not re-proposed.
6. **Need-directed evidence extraction**: `EvidenceEntityTool` accepts an explicit
   `focus`, returns only relevant resolved identities, records the source spans that
   justified each value, and preserves same-surface ambiguity as candidates rather than
   guessing. `EntityResolutionTool` likewise surfaces candidate identities.
7. **Typed user conflicts** (`UserConflict`, `Goal.conflicts`) are derived structurally
   from the frozen obligations (impossible window, contradictory thresholds, future
   result) and are distinguishable from capability or data limitations. A conflict can
   never reach `COMPLETE`.
8. **Structured qualification** (`Qualification`) lets an analysis name its measured
   denominator (`ROWS`, `MEASURED`, `EVENTS`, `GAMES`, `ENTITIES_PER_GROUP`); unsupported
   bases/fields are rejected, never approximated.
9. **Alternate-route credit**: a core Need whose durable outcome is structurally closed
   and that produced no usable evidence does not by itself block `COMPLETE` once the
   frozen obligations are independently verified by other accepted work. The decision is
   deterministic (outcome + obligation coverage), not a Planner declaration.

## Consequences

- The Planner can discover available exports, compatible bindings, trusted fields and
  truthful capability limits *before* execution, while execution remains closed and
  deterministic.
- A failed attempt now changes replanning materially (failure class + hint + obligation
  state + accepted exports), instead of appending another predefined tool call.
- `UNKNOWN_SCHEMA`, `UNSUPPORTED_ANALYSIS`, `WRONG_BINDING`, `IDENTITY_AMBIGUITY`,
  `INSUFFICIENT_EVIDENCE`, `POLICY_BLOCKED`, `RETRYABLE_SOURCE` and `VALID_EMPTY` are
  distinguishable and produce different next-step guidance.
- No safety boundary is weakened: the Safe Analytical IR, SchemaCatalog allowlisting,
  read-only SQL/DuckDB sandbox, SSRF controls, explicit bindings, durable ToolOutcome,
  independent Judge and State Projection remain authoritative.

## Known limitations

- The convergence surface guides planning; it does not make an LLM Planner correct. Live
  exploration showed a model can still invent an entity that was never mentioned.
- `route_of`/`equivalence_note` are honored by the alternate-route credit rule but there is
  no automatic route *search*; a Planner must propose the alternate.
- A full token/cost ledger across model + provider calls is still not wired.
- Historical/official roster membership and multi-source joins remain out of scope.
