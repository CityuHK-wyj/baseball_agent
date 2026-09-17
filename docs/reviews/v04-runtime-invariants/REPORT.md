# Baseball Agent v0.4 — runtime invariants stop report

Branch: `pi/v0.4-runtime-invariants` (created from the independent review checkpoint
`codex/v0.3-architecture-review @ 0ee26ead9f3f5812f39e79c1b8c03ed420a360de`).

Status: **`V0_4_RUNTIME_INVARIANTS_IMPLEMENTED — READY_FOR_INDEPENDENT_REVIEW`**.

v0.4 is **another correction of the existing architecture**, not a new architecture. The
Interaction / Planning / Tools / Evaluation / Response layers and all security,
persistence and governance boundaries are preserved. No merge, tag or release was made;
`main` is untouched.

## Commit list

- `feat(runtime): split requested/declared/verified scope with dimension verification`
- `feat(runtime): freeze user obligations and check Planner work against them`
- `feat(runtime): independent Judge, state projection and durable ToolOutcome taxonomy`
- `feat(runtime): explicit Artifact bindings and typed/versioned export contracts`
- `fix(ir): validate aliases, implement accepted semantics, compile the window`
- `feat(runtime): append-only redacted event journal and trace projection`
- `feat(persistence): durable identity, execution intent and surfaced persistence errors`
- `feat(knowledge): ACTIVE-only retrieval, structured conflicts, provenance-preserving promotion`
- `fix(web): redirect validation and bounded streaming`
- `test(runtime): v0.4 architectural invariant suite`
- `docs(runtime): ADR 0026, architecture diagram, responsibility migration map, stop report`

## Actual runtime architecture (as implemented)

```text
Interaction / Conversation
        ↓
Goal Understanding
        ↓
Immutable User Obligations            (extract_obligations → Goal.obligations)
        ↓
Planner                               (LLM or deterministic)
        ↓
Need / Artifact Graph                 (depends_on + explicit InputBinding)
        ↓
Action Admission                      (truthful ToolCapabilityContract + budget)
        ↓
Tools / Data / Web / ACTIVE Knowledge / Compute
        ↓
ToolOutcome                           (durable taxonomy; ToolAttempt before/after)
        ↓
Artifact + References                 (ExportContract schema/version/role/grain)
        ↓
Deterministic Validation              (Safe IR + SchemaCatalog.role/type/ops + AST guard)
        ↓
Scope Verification                    (Requested / Declared / Verified, per dimension)
        ↓
Independent Judge                     (runs on every candidate, incl. apparent SATISFIED)
        ↓
Need / Goal State Projection          (StateProjector owns terminal truth)
        ↓
        ├── insufficient → Planner re-plan (gaps + failed attempts)
        └── sufficient/limited → Grounded Claims → Response
```

Cross-cutting: `EventJournal` (redacted, append-only), `SqliteOperationalStore`
persistence, permissions/budget, and the unchanged read-only SQL / DuckDB sandbox / SSRF
boundaries.

## Frozen invariants

See `docs/adr/0026-v0.4-runtime-invariants.md` for the full statement. Summary:

1. Requested scope cannot certify evidence scope.
2. Planner cannot approve its own completion baseline.
3. Independent assessment participates in every evidence loop.
4. Every attempted action has a durable outcome.
5. Artifact bindings must be explicit.
6. Accepted IR semantics must equal executed semantics.
7. Wrong-scope evidence cannot satisfy a core obligation.
8. Tool capability contracts must be truthful.
9. Stable identity and lineage survive restart.
10. Candidate Knowledge is never authoritative automatically.

## Requested / declared / verified scope model

- `RequestedScope` = `Need.required_scope` / `Goal.scope`.
- `DeclaredScope` = `RuntimeArtifact.actual_scope` (the Tool's claim; never trusted alone).
- `ScopeVerification` = per-dimension record
  `{dimension, status ∈ VERIFIED|PARTIAL|MISMATCH|UNKNOWN, requested, observed,
    evidence_refs, verifier, source_snapshot, limitations}`.
- `verify_artifact_scope` is the only producer. It reads a truthful execution/provider
  receipt, and for entity/membership inherits the *weakest/strongest* status of the exact
  bound upstream artifacts.
- A declared value that echoes the request can reach `PARTIAL` only. `UNKNOWN` is never
  coverage. Any `MISMATCH` on a requested dimension blocks `SATISFIED`.
- Dimensions: entity, population, membership, time, season, game type, event population,
  measure, aggregation, qualification, source coverage.

## User-obligation model

`Goal.obligations: tuple[UserObligation, ...]` (frozen): `{obligation_id, kind, description,
value, source_ref, origin, status}`. Kinds: ENTITY, POPULATION, MEMBERSHIP, TIME, SEASON,
GAME_TYPE, METRIC, QUALIFICATION, RANKING, GROUPING, COMPARISON, CLAIM_TYPE, EXPLANATION.
Derived from the semantic brief plus high-confidence lexical anchors (years, ISO dates,
game-type/population/membership words, explicit thresholds). Coverage is verified
deterministically from the accepted work (`obligation_coverage`), not from a Planner
declaration; `COMPLETE` requires every core obligation `VERIFIED`.

## Judge wiring

`CoverageJudge` = deterministic verification + `IndependentJudge`. The Judge is consulted
on **every** candidate (including a deterministic `SATISFIED`), may downgrade, and may
never upgrade a deterministic `MISMATCH`. A judge exception becomes
`assessment_available = False` and forces `PARTIAL`. The default judge is a deterministic
contextual judge (`DeterministicContextJudge`); an LLM judge can be injected through
`independent_judge=`.

## State projection rules

`StateProjector` owns Need `SATISFIED/PARTIAL/BLOCKED/FAILED` and Goal
`COMPLETE/LIMITED/FAILED/WAITING_FOR_USER`. `COMPLETE` requires all core Needs `SATISFIED`
**and** all core obligations `VERIFIED` **and** at least one accepted claim. `LIMITED`
requires at least one useful accepted claim plus explicit material gaps. A bare OK artifact
is not enough for `LIMITED` or `COMPLETE`.

## ToolOutcome / recovery model

`ToolOutcomeCode` ∈ {SUCCESS, EMPTY_RESULT, UNSUPPORTED_CAPABILITY, INPUT_UNRESOLVED,
INPUT_INCOMPATIBLE, INVALID_IR, UNKNOWN_FIELD, SCOPE_MISMATCH, COVERAGE_UNAVAILABLE,
SOURCE_TRANSIENT, POLICY_BLOCKED, MODEL_UNAVAILABLE, INTERNAL_FAILURE, INTERRUPTED,
UNCERTAIN}. Every request produces a persisted `ToolAttempt` (intent before side effect,
outcome after). Unexpected exceptions are normalized centrally. Failed/empty/no-Artifact
attempts become Planner gaps.

## Need / Artifact binding model

`Need.depends_on` + `Need.input_bindings: tuple[InputBinding, ...]`. `resolve_bindings`
only considers the Need's own `input_refs` and the artifacts of its declared dependencies;
ambient export injection was removed. `Need.route_of` / `equivalence_note` express
alternative routes; the state projection decides acceptability.

## Safe IR changes

- Identifier pattern enforced on every alias / order-by token (model + compiler).
- Every accepted aggregate × condition × period combination is implemented
  (COUNT/COUNT_IF/COUNT_NON_NULL/AVG/SUM/MIN/MAX with conditions and periods) or rejected.
- Catalog `allowed_operations`, role and type checks enforced.
- Declared `window` is compiled into the executed predicate (`date_field BETWEEN …`).
- Qualification uses the measured denominator (`COUNT(field)`), not raw row count.
- `IRCompilationResult` carries `ir_digest`, `applied_window`, `game_types`,
  `coverage_status`, `qualification`, `aggregation`, `select_count`.
- The SQL AST guard, read-only roles, table allowlist, timeout, bounded fetch and DuckDB
  sandbox are unchanged.

## Persistence / event model

- `RuntimeConversation` snapshots schema v2: messages, references, artifacts, goal + history,
  obligations, needs, assessments, decisions, attempts, events, export refs, clarification
  refs, turns.
- `ArtifactStore`/`ReferenceStore` never reuse ids; counters are re-derived on restore.
- Execution intent is persisted before an external side effect; outcomes after.
- Persistence failures set `conversation.persistence_error`, append a step and emit a
  `PERSISTENCE_ERROR` event (never swallowed).
- Normal product CLI (`_build_runtime`) wires `SqliteOperationalStore`.
- `--trace` renders steps, per-request ToolOutcomes and the event-journal projection.

## Claim grounding changes

`Claim` gains `claim_type` ∈ {OBSERVED_FACT, DERIVED_CALCULATION, COMPARISON,
REPORTED_EXPLANATION, HYPOTHESIS, CAUSAL_CLAIM}. `validate_claims` requires all supports to
resolve and rejects/downgrades inferential claims: an explanation needs narrative evidence;
a causal claim needs narrative + measurement or is downgraded to `HYPOTHESIS`. Web
artifacts only expose grounded spans in `text_content`.

## Shared Knowledge changes

- `KnowledgeQuery.statuses` defaults to `("ACTIVE",)`; HISTORICAL is explicit only.
- `community` is an explicit retrieval scope.
- Structured `ConflictRecord`s with exact IDs; `ConflictCheckUnavailable` fails closed.
- Promotion preserves original category, scope, language/locale/community, evidence spans,
  provenance, originating run, reviewer and canonical target; promotion is idempotent.
- Candidate store remains separate from authoritative knowledge.

## Migrated legacy responsibilities

See `docs/v0.4-responsibility-migration.md`. Highlights: guarded executors/SQL guard/
knowledge store = still-required shared infrastructure; old Assessment/Judge = migrated
(scope verification + independent Judge + state projection); RunRecorder = migrated to
EventJournal + ToolAttempt; ResumeService = migrated to the product-path store; RunMetrics
redaction = migrated to `events.redact`; Router permission/cost admission = migrated
partially (truthful capability cost/availability + `ACTION_ADMITTED`; full cost ledger not
yet wired).

## Test counts

- Full suite: **673 passed, 164 subtests** (`python3 -m pytest tests -q`).
- New v0.4 invariant module `tests/artifact_runtime/test_v04_invariants.py`: **47 tests**
  covering scope truth, Judge veto/outage/joint-support, binding isolation, IR numerical
  semantics (independent DuckDB reference execution), recovery, persistence identity,
  grounding, and knowledge governance.
- `python3 -m compileall -q app tests` clean; secret-scan tests pass.

## Live validation

- PostgreSQL live validation: **not run** (no configured instance in this environment).
- Parquet live validation: **run and passed** —
  `docs/reviews/v04-runtime-invariants/live_parquet_validation.py`:
  compiled OK, `coverage_status=FULL`, window predicate present, execution `OK`,
  selected batter `(143 BBE, 89.00559440559441 EV)` which **matches an independent DuckDB
  reference calculation exactly**.
- Web live validation: not run; SSRF/redirect/streaming hardening was **not** relaxed to
  obtain a green test.

## Known limitations

- The default independent Judge is deterministic-contextual; an LLM Judge seam exists but
  is not wired by default.
- No historical/official-roster provider: v0.4 makes the current-active-roster adapter
  *truthfully refuse* historical scope rather than silently returning current data.
- Generic composition is implemented for DB→Web (value-parameterized query), Web→DB
  (grounded entity extraction → PLAYER_ID_SET → SQL) and DB→Compute; Knowledge→DB remains
  dictionary/entity based rather than arbitrary predicate compilation.
- A full model/provider token+cost ledger is not wired; iteration bounds and declared
  capability cost remain.
- Some analytical operations (joins, distinct counts, window functions, multi-source
  composition) are still out of scope and explicitly unsupported.

## Answers to the required questions

1. **Can a Tool still certify its own requested scope?** No. Declared scope alone reaches
   `PARTIAL` at best; `VERIFIED` requires an execution/provider receipt or verified
   upstream inheritance.
2. **Can a deterministic SATISFIED candidate bypass the independent Judge?** No. The Judge
   is invoked on every candidate and may downgrade; only a hard deterministic mismatch is
   non-overridable.
3. **Can Planner lower or omit an explicit user obligation and still reach COMPLETE?** No.
   `COMPLETE` requires every core obligation `VERIFIED` by deterministic coverage of the
   accepted work.
4. **Does every ToolRequest have a durable ToolOutcome?** Yes: a persisted `ToolAttempt`
   (intent before, outcome after) and an `EXECUTION_OUTCOME` event; exceptions normalize to
   `INTERNAL_FAILURE`.
5. **Can two compatible same-type exports accidentally bind to the wrong downstream Need?**
   No. Bindings come only from the Need's declared dependencies; a test proves only the
   declared dependency's `PLAYER_ID_SET` reaches SQL.
6. **Does every accepted IR construct have faithful executed semantics?** Yes for the
   current grammar: accepted aggregates × conditions × periods are implemented; unsupported
   operations/roles/types are explicitly rejected; the window is compiled and the
   qualification denominator is the measured count.
7. **Can any model-controlled alias/identifier inject executable SQL structure?** No.
   Identifier syntax is enforced at model and compiler level; the AST read-only guard
   remains as defense in depth.
8. **Can the system explain exactly why a failed local analytical attempt failed?** Yes:
   the event journal, `ToolAttempt.outcome_code`/`detail`, `Need.unsatisfied_inputs` and the
   `--trace` projection expose the failed stage; `local_analytics` no-Artifact failures now
   produce gaps.
9. **Can IDs or refs collide after restore and continued execution?** No. Stores never
   reuse ids and counter state is re-derived on restore; append-after-restore is tested.
10. **Can a rejected/ungrounded Artifact become trusted by passing through another Tool?**
    No. `resolve_export_ref` refuses unaccepted/rejected artifacts, `EvidenceEntityTool`
    requires accepted grounded input, and derived products inherit upstream verification.
11. **Can pending CandidateKnowledge influence authoritative retrieval?** No. Authoritative
    retrieval is ACTIVE-only and the candidate store is separate.
12. **Can an important response claim be traced to evidence that actually supports that
    proposition?** Claims carry type + support refs; `validate_claims` resolves supports and
    enforces claim-type sufficiency (causal claims need narrative + measurement).
13. **Can Judge feedback trigger re-planning before finalization?** Yes: a downgrade keeps
    the Need unsatisfied/partial, which becomes gaps fed to `add_needs` inside the loop.
14. **Does COMPLETE mean the frozen user obligations are supported rather than all
    Planner-created Needs marked satisfied?** Yes: obligations are checked independently of
    the Planner's Need decomposition.
15. **Are old security/persistence/governance responsibilities genuinely preserved?** The
    read-only SQL/DuckDB/SSRF/credential boundaries are unchanged and still tested;
    persistence identity and governance responsibilities were migrated rather than dropped
    (see the migration map; a full cost ledger is the one explicitly partial item).

## New independent audit SHA

Point the next Codex audit at the tip of `pi/v0.4-runtime-invariants` (recorded in the
final commit message / see `git log -1`). Do not audit `main`.
