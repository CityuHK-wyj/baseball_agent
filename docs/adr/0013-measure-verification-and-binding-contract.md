# ADR 0013 — Measure Verification and Binding Contract Enforcement

Status: accepted (2026-09-21)

## Context

The data-source audit (`docs/audit/`) established that accepted analytical results were not
tied back to the user's requested measure, and that the Runtime Binder did not enforce the
capability contracts it was documented to enforce:

- The requested measure (ERA, RBI, stolen bases, …) was never a `UserRequirement`. A
  `DATABASE_RESULT` with any row satisfied the generic `ANALYSIS` requirement, so an
  unsupported or different metric could be reported as `COMPLETE` (e.g. “RBI = 172”, which
  is actually hits).
- `Binder` matched export types loosely; `input_requirements` could bypass a capability's
  `accepts` set, and no scope compatibility was checked despite ADR 0005.
- An entity mention that failed to resolve (team, ambiguous name) still produced an
  `ENTITY_RESOLUTION` artifact, so a dependent `local_analytics` Need ran with no entity
  filter and returned a league-wide aggregate.
- A non-integer season (`"career"`) reached `SchemaCatalog.source_for_window` and raised an
  uncaught `TypeError`.
- An aggregate over an empty population (no rows) returns one all-zero row and was treated
  as an accepted result.

## Decision

Measure *semantics* live in one place and are machine-checked; binding *contracts* fail
closed.

1. **Measure authority** — `app/capabilities/analytics/measures.py` is the single authority
   for measure keys, labels, aliases (zh/en), derivability, and structural recognition. It
   is a definitions/verification layer over the SchemaCatalog, not a second schema: every
   base quantity is expressed with the real catalog fields and event vocabulary.
2. **Requested measure is a requirement** — `RequirementBuilder` emits
   `UserRequirement(kind=MEASURE)` with `{key, label, derivable}` from explicit measure
   constraints and from the analysis objective/raw query (deterministic alias resolution,
   gated so definition questions such as “什么是OPS” never become data requirements).
3. **Actual measure is attested from the Safe IR** — `local_analytics` blocks a request
   whose measure is not derivable (`UNSUPPORTED_MEASURE`), and otherwise declares
   `actual_measures` built from the *validated IR* (conditions, aggregates, derived
   expressions), never from a SQL column name or LLM prose. `Scope.measures` carries the
   machine-checkable keys.
4. **Judge verifies the measure** — a `MEASURE` requirement is `SATISFIED` only if an
   accepted result attests that key. `DATABASE_RESULT` claims are emitted only when the
   goal's required entity and measure requirements are satisfied (an unresolved entity or a
   mismatched/unsupported measure suppresses the database claim).
5. **Binder enforcement** — the capability `accepts` set is authoritative;
   `input_requirements` is a preference. The Binder rejects empty `PLAYER_ID_SET`
   (`EMPTY_ENTITY_SET`), wrong namespace (`ENTITY_INCOMPATIBLE`), incompatible scope
   (`SCOPE_INCOMPATIBLE`), wrong export type (`EXPORT_TYPE_INCOMPATIBLE`), missing accepted
   artifact (`ARTIFACT_NOT_ACCEPTED`) and missing required input
   (`UNRESOLVED_REQUIRED_INPUT`), each as a typed `BindingFailure`.
6. **No season type crash** — non-integer seasons fail closed as `SCOPE_MISMATCH` rather
   than propagating a `TypeError`.
7. **Empty population is not zero** — a single all-zero scalar row over a volume aggregate
   is reported as `VALID_EMPTY` / `empty_population`, and cannot satisfy an analysis.

## Consequences

- Wrong measures and wrong entity scopes can no longer enter accepted evidence.
- Some previously `COMPLETE` answers become `LIMITED`/`FAILED`; this is intended
  (fail closed). Ad-hoc flexibility is preserved by structural recognition: an `AVG`
  computed under an arbitrary column name is still attested, while a mistyped `rbi` column
  computed as hits is not.
- The capability inventory and planner prompt are unchanged; `python_compute` no longer
  demands a specific export type and instead enforces “at least one accepted data binding”
  in the tool.
- Deferred to a later phase (per the Phase 3 scope): historical entity dictionary, team
  dimension, new web backends, broader metric packs, `dynamic_fact` repair, cross-source
  SQL, and Planner architecture changes.
