# Requirement-Local Partial Approval & Clarification Integration

Date: 2026-10-06
Branch: `codex/phase5b-final-repair`
Starting SHA: `66f1f5712b87c38e69be9a143af597d44f10d473`
Scope: M2.12R4 continuation. No M2.13, no M3, no new semantic authority in the browser.

> This work closes the backend gap recorded in
> [`FRONTEND_INTERACTION_FOUNDATION.md`](FRONTEND_INTERACTION_FOUNDATION.md):
> a supported Requirement could lose its correctly Judge-approved Artifact because an
> independent Requirement in the same Goal was unresolved. The frontend interaction
> foundation is unchanged except for the minimum needed to consume same-Goal partial
> results and clarification.
>
> The natural-language hardcoding audit
> ([`NATURAL_LANGUAGE_HARDCODING_AUDIT.md`](NATURAL_LANGUAGE_HARDCODING_AUDIT.md)) is a
> normative constraint for this work. See §4 for the explicit audit of the one lexical
> segmentation signal that was considered and removed.

## 1. Diagnosis

The Goal-wide analytical admission path rebuilt one `RequestedAnalyticalContract` from
**all** mandatory Requirements and compared that single contract to the execution
receipt. A single unsupported sibling (for example an `RBI` MEASURE with
`derivable=False`) or an unresolved clause made that comparison fail, so
`receipt_requirement_ids` returned the empty set and the whole `DATABASE_RESULT` was
dropped from Judge consideration. The live reproduction on the real 2023 Parquet
archive showed this directly: a Goal containing *2023 regular-season batting
performance* plus *RBI* produced `LIMITED` with **zero** approved tables.

The pre-existing fallback (`independent_analytical_partitions`) could sometimes recover
the artifact, but it partitioned the **raw query string** using a finite "task verb"
gate (`report|count|show|…` / `统计|…`). Natural-language (especially Chinese)
multi-part questions such as `…打击表现如何？他的RBI是多少？` do not satisfy that gate, so
the fallback returned no partition and the valid Artifact stayed dropped.

Two secondary defects made partial retention unsafe or incomplete:

1. the per-artifact `MEASURE` claim gate used the **Goal-wide** requested measure set,
   so an independent supported analysis could not be claimed when the only explicit
   `MEASURE` Requirement was the unsupported sibling;
2. an "empty" or deictic sibling `ANALYSIS` (`Show his numbers there`) could be
   satisfied by an unrelated artifact because its wording was lexically waived by the
   residue test, producing a false `COMPLETE`.

## 2. Core principle

**Approval is Requirement-local; completion is Goal-level.**

Each Requirement is independently evaluated and independently blocks only the
Requirements that genuinely depend on it. The Goal status is computed afterwards from
Requirement/proposition coverage. A correct Artifact for Requirement A is not discarded
because independent Requirement B is unresolved.

## 3. Requirement satisfaction model

No new parallel satisfaction system was introduced. The existing structures already
carry Requirement-local traceability:

| Structure | Role |
|---|---|
| `UserRequirement` | frozen user obligation (`kind`, `value`, `bound_source_clause`, `completion`, `parent_requirement_ids`, authority) |
| `AnswerProposition` | answer slot per Requirement; carries `depends_on`, `coverage`, `supporting_claim_ids`, `supporting_artifact_ids`, `evidence_finding_ids` |
| `RequirementState` | per-Requirement Judge result (`SATISFIED`/`PARTIAL`/`UNSATISFIED`/`BLOCKED`, support artifact ids, gaps) |
| `Claim.requirement_ids` / `Claim.proposition_ids` | approved-fact → Requirement traceability |
| local support map (`receipt_requirement_ids`) | per-Artifact set of current Requirements it independently conforms to |

The key invariant is preserved: every approved Artifact is linked to the exact
Requirement(s) it was independently admitted for, through `local_support` and the
resulting `RequirementState.support_artifact_ids`.

## 4. Segmentation hint audit (task-verb regex)

A first implementation attempt reused the old finite task-verb list to decide whether an
`ANALYSIS` could be partitioned. That is exactly the lexical-gate pattern the
natural-language hardcoding audit removed, so it was deleted.

* **Callers traced.** `_is_independent_analysis_task()` is called only by
  `independent_analytical_partitions()`; that function is called only by
  `receipt_requirement_ids()` (Judge admission) and `Engine._build_request()`
  (binding a local analytical goal). No other production caller exists.
* **What `True` changes downstream.** It permits a group of Requirements to be
  evaluated against a **smaller** current-Requirement contract. It does not itself
  prove completeness, authority, `UNDERSTOOD`, `COMPLETE`, irrelevance or execution
  safety. Every partition is still checked by `receipt_conforms_goal()` against a
  contract rebuilt from current Requirements, including that task's own source review.
* **Why `True` cannot upgrade a clause.** The admission path requires an independently
  valid execution receipt (`receipt_usable`), an independent current-Requirement
  contract (`contract_for_goal`), and a `MATCH`/`PARTIAL` comparison. A task verb has no
  code path into `constraint_coverage`, authority resolution, or the receipt digest.
* **Decision.** The lexical task-verb list is **removed**. Independence requires
  governed grounding (a governed Measure or subject role) or the interpreter's
  `EXPLANATION` completion class. A task-shaped but unresolved fragment
  (`Report the same split`, `Show his numbers there`, `解释一下这个情况`) fails closed.
* **No new keywords.** Removing the task verb does not change the grounded contract for
  equivalent meaning (tests assert identical `constraint_coverage` obligations/state
  with and without the verb). No keyword was added to make a paraphrase work.

A separate, non-authoritative structural guard is retained: if one task's wording is a
substring of another's, the decomposition produced a dependent fragment and partitioning
fails closed. This is string containment, not meaning.

The three concepts are now distinct in code:

* `_analysis_has_grounding()` — semantic grounding of a task's own wording
  (necessary, never sufficient);
* `_is_independent_analysis_task()` — segmentation hint (`EXPLANATION` or grounding);
* `receipt_conforms_goal()` / `constraint_coverage()` — the independent proof of
  clause coverage and completeness.

## 5. Dependency semantics

Partitions are derived from the authoritative Requirement graph, never from an
Artifact's self-description.

* A partition exists only when the interpreter produced **≥2 distinct `ANALYSIS`
  tasks** with distinct user wording.
* `ENGINE`-shared clauses stay in every partition: `SEASON`, `TIME`,
  `GAME_POPULATION`, and `CONSTRAINT` Requirements. An unresolved shared clause blocks
  all branches.
* `MEASURE` Requirements are attributed to the task whose wording resolves the measure;
  an unattributable measure remains a global obligation in every branch.
* `ENTITY` Requirements are per-task-attributed **only** when there are ≥2 distinct
  subjects, every task names its own subject, and every subject is owned by a task.
  Otherwise every entity is shared and an unresolved identity blocks all branches
  (the fail-closed default, covering the "Entity identity unresolved → HR and AVG both
  blocked" case).
* Existing `AnswerProposition.depends_on` edges are authoritative: a partition whose
  proposition depends on a Requirement outside the partition is discarded.

## 6. Judge changes

`app/verification/judge.py`:

* `Judge.evaluate` computes `local_support = {artifact: receipt_requirement_ids(...)}`
  once, then filters Database Artifacts by non-empty local support and evaluates each
  Requirement only against the Artifacts admitted for it.
* `_claims(..., local_support)` applies a **Requirement-local** measure gate: an
  Artifact must declare the measures owned by the Requirements it was admitted for,
  not the Goal-wide measure set.
* `receipt_requirement_ids` (`app/capabilities/analytics/conformance.py`) now:
  * tries the full current Goal first;
  * otherwise uses graph-derived partitions;
  * records an `ANALYSIS` only when it has proven measure keys or governed grounding
    (when siblings exist), so a deictic fragment cannot be satisfied by an unrelated
    Artifact.

Conformance checks were **not weakened**: `receipt_usable` still validates the IR,
attestation, digests, SQL, measures, scope and conformance result; `compare` still
enforces relation, entities, role, time, population, qualification, row population,
grouping, ordering, limit and source coverage.

## 7. Artifact ownership and invalidation

An approved analytical Artifact is owned by the current Requirements it independently
conforms to (`local_support`), with its own Evidence/receipt lineage. A generic
Goal-level Artifact cannot satisfy an unresolved Requirement merely because it belongs
to the same Goal.

Invalidation rule implemented: an Artifact is only admitted for Requirements whose
current contract it satisfies **now**. If a clarification changes a Requirement's
semantics (entity, season, population, filters, role, measure), the artifact no longer
conforms to the new current contract and is not admitted. Unrelated approved Artifacts
are unaffected because they are evaluated under their own local support. Two direct
Judge tests freeze this: a changed authoritative entity invalidates A's artifact, while
a clarification that only changes an unrelated sibling keeps A. Explicit "retract this
earlier presentation artifact" events are **not** implemented; the UI retains earlier
approved tables. This is recorded as a limitation (§15).

## 8. AnswerPackage / Goal status

`AnswerPackage` already exposes per-Requirement `RequirementAnswer.coverage`, the
approved `facts`, the `unresolved` Requirements and the `limitations`. No new wire
structure was required. The decision on a new `PARTIAL` status:

* **Keep `LIMITED`** as the authoritative backend status for "at least one mandatory
  Requirement satisfied, at least one remains actionable/unresolved".
* `RequirementStatus.PARTIAL`, `CoverageState.PARTIAL` and `Artifact.status="PARTIAL"`
  already provide Requirement-level partiality with precise semantics.
* A new `GoalStatus.PARTIAL` would overlap `LIMITED` and force a wire/UI rename for no
  semantic gain. The reserved frontend `PARTIAL` remains for a future authoritative
  state and is never synthesized by the UI.

Semantics documented:

| Status | Meaning |
|---|---|
| `COMPLETE` | all mandatory Requirements/propositions satisfied |
| `LIMITED` | at least one mandatory Requirement satisfied and at least one unresolved/partial |
| `WAITING_FOR_USER` | progress requires user clarification; approved partial facts may already exist |
| `FAILED` | no useful supported result and execution cannot continue |

`WAITING_FOR_USER` with partial data is supported: `present()` emits claimed
`artifact.created` events before `answer.partial` and `clarification.required`.

## 9. Planner reuse / clarification integration

* `bind_obligations` keeps the persisted `analytical_resolution_state` ledger:
  identical resolver inputs with unchanged authoritative context are served from cache
  and `duplicates_suppressed` is recorded. New authoritative information (a
  clarification) changes the context digest and permits one bounded retry with a
  recorded `retry_reason`.
* Within one Goal, `_run_plan` stops when every CORE Need has an accepted Artifact, so
  satisfied independent Requirements are not re-planned.
* Clarification authority is unchanged: the browser submits only opaque option IDs or
  free text; the backend maps them to the original server-provided semantics and
  `Engine.ask` remains the sole interpreter. `clarification.required` remains
  backend-defined and opaque.

Cross-run artifact reuse is limited by the existing architecture: a clarification
resume creates a new Goal, so the backend may recompute an independent Requirement. The
browser retains the earlier approved table. This is recorded as a limitation (§15).

## 10. SSE / event behavior

No schema change. Already-supported sequence works for same-Goal partials:

```
artifact.created         # approved Requirement A table
answer.partial           # AnswerView with A visible and B unresolved
clarification.required   # optional B clarification
...
clarification.resolved
artifact.created
answer.completed
```

`run.failed` still emits no fact. Replay is idempotent (run-scoped IDs, reducer dedupe),
so replay after `Last-Event-ID` does not duplicate A's Artifact; the pending
clarification survives.

## 11. Frontend changes (minimal)

No semantic logic added to React. The only additions are tests:

* `frontend/tests/components.test.tsx`: a same-Goal approved table and a pending
  clarification card render simultaneously; the table survives clarification
  resolution.
* `frontend/e2e/real-api.spec.ts`: two new real-backend tests (§12).

## 12. Real same-Goal tests and evidence

All evidence is under `docs/audit/m212r4/requirement_local_partial/` (raw JSON produced
by the live Engine + real Parquet/PostgreSQL + configured DeepSeek).

| File | Query | Result |
|---|---|---|
| `compound_rbi.json` | `大谷翔平2023赛季常规赛打击表现如何？他的RBI是多少？` | `LIMITED`; `batting performance` `SATISFIED`; **1 approved TableArtifact**; `RBI` `PARTIAL`/`UNSUPPORTED` |
| `compound_hr_salary.json` | `大谷翔平2023赛季常规赛打出了多少支本垒打？他的薪水是多少？` | `LIMITED`; no table (A's own analytical candidate was unproven — fail-closed) |
| `dangerous_compound.json` | `Please report Ohtani's batting average against left-handed pitchers with two strikes in 2023` | `LIMITED`; **0 tables** |
| `dangerous_compound_zh.json` | `请报告大谷翔平2023赛季面对左投手且两好球时的打击率` | `LIMITED`; **0 tables** |
| `compound_closed_roof_zh.json` | `大谷翔平2023赛季常规赛在封闭球场的打击表现如何？` | `LIMITED`; **0 tables** (unsupported modifier blocks the compound) |
| `same_goal_clarify.json` | `大谷翔平2023赛季常规赛打击表现如何？另外，Smith在2023赛季的表现如何？` | `LIMITED`; entity resolution succeeded, so no clarification was triggered (see limitation) |

Required same-Goal outcome (mission §27) is demonstrated by `compound_rbi.json`: A
executes on real Parquet, Judge approves A, A's TableArtifact is emitted, B remains
unresolved, and the Goal is not `COMPLETE`.

### Playwright (real Python API + Vite + real Engine, no mocked semantics)

`frontend/e2e/real-api.spec.ts`:

1. single analytical table — passed;
2. earlier approved table retained across a later limited turn — passed on isolated
   re-run (an earlier full-suite run hit a 165 s expectation timeout; see limitations);
3. contextual ambiguity clarification + resume — passed;
4. **same-Goal** unsupported RBI sibling does not erase the approved table — passed;
5. compound closed-roof restriction exposes no partial table — passed.

Full suite run: `4 passed, 1 failed (timing)`; the failed test passed on isolated
re-run (`1 passed, 2.7 min`).

## 13. Regression results

| Check | Result |
|---|---|
| `python -m pytest -q` | **3181 passed, 31 skipped** |
| `pytest -q tests/test_partial_retention_m212r4.py` | **35 passed** |
| `pytest -q tests/test_frontend_api.py tests/test_frontend_schema_drift.py` | **5 passed** |
| `cd frontend && npm test` | **10 passed** |
| `npx playwright test e2e/real-api.spec.ts` | 5 workflows exercised, all passing (one required an isolated re-run for timing) |

Existing M2.12 / R / R2 / R3 / R4 semantic tests, execution-conformance tests and
frontend protocol/component tests remain green; no safety assertion was relaxed.

## 14. Safety and utility metrics

| Metric | Value |
|---|---:|
| Independent mixed Goals tested | 6 live |
| Requirement-local approved facts (live) | 1 (`compound_rbi`) |
| Correct partial Artifacts retained | 1 (batting table in `compound_rbi`) |
| Dependent Artifacts correctly blocked | 3 (`dangerous_compound`, `dangerous_compound_zh`, `compound_closed_roof_zh`) |
| Deictic/empty fragments admitted | 0 |
| Shared unresolved clause blocking all branches | enforced by shared `SEASON`/`POPULATION`/`CONSTRAINT`/entity rules |
| **Unsafe partial Artifact exposure** | **0** |
| **Incorrect COMPLETE** | **0** obs.; full suite green |
| Unnecessary independent re-execution | 0 within a Goal; across clarification turns not measurable by current architecture |
| Clarification cycles | existing E2E resolves once; no repeated identical clarification observed |
| Same-Goal partial E2E success | yes (test 4) |
| Compound safety E2E success | yes (test 5) |

## 15. Limitations

* **Same-Goal clarification with two independent subjects is not reliably triggered.**
  The interpreter often resolves the second subject (e.g. `Smith`) to a candidate, so
  no clarification is requested. Per-task entity attribution is implemented and tested
  deterministically, and the API/presenter already support "approved table +
  clarification card"; a single-message two-subject clarification depends on the live
  entity-resolution ambiguity signal and is not deterministic.
* **No explicit artifact-retraction event.** Earlier approved tables are retained; an
  A-dependent invalidation is realised by the Artifact not conforming to the new
  current contract, not by a retraction event. The frontend replacement path is tested
  only insofar as the backend omits the artifact.
* **Cross-run backend artifact reuse is limited.** A clarification resume builds a new
  Goal; the browser retains A, but the backend may recompute A on the resumed run.
* **`compound_hr_salary` is a utility miss, not a safety violation.** A's own
  interpreter candidate was unproven, so A was correctly blocked.
* **`LIMITED` is not split into a new `PARTIAL` GoalStatus.** The semantics are carried
  by Requirement-level `PARTIAL` coverage and the reserved frontend `PARTIAL`.
* **Playwright timing.** One full-suite E2E run exceeded the 165 s expectation timeout
  for the second analytical turn; the deterministic two-turn reproduction returned
  `LIMITED` in 3/3 runs and the isolated Playwright re-run passed.

## 16. Git status (at completion)

Working tree intentionally dirty; no commit was made. This work adds/edits:

* `app/capabilities/analytics/conformance.py` — graph-derived partitions, grounding
  gate, per-task entity attribution, requirement-local association, removed task-verb
  independence;
* `app/verification/judge.py` — Requirement-local support + measure gate;
* `app/semantic/goals.py` — preserve the interpreter's exact task wording
  (`source_text`) and bind each analytical task to its own clause;
* `app/capabilities/analytics/derivations.py` — accept the preserved `source_text`;
* `tests/test_partial_retention_m212r4.py` — retention matrix + segmentation-hint audit;
* `tests/test_frontend_api.py` — real partial TableArtifact projection;
* `frontend/tests/components.test.tsx` — simultaneous table + clarification;
* `frontend/e2e/real-api.spec.ts` — same-Goal partial + compound-safety workflows;
* `docs/audit/m212r4/requirement_local_partial/*.json` — live evidence;
* this document; and the M2.12R4 report reference.

Pre-existing untracked M2.11/M2.12R4/Phase5B/Web files remain untouched.

## 17. Stop-condition answers

| # | Question | Answer |
|---|---|---|
| 1 | One Requirement approved while an independent one unresolved? | Yes (`compound_rbi`). |
| 2 | Approved A visible while B waits for clarification? | Yes at the API/presenter/reducer level; see limitation on single-message trigger. |
| 3 | Overall Goal avoids false COMPLETE? | Yes (`LIMITED`; 0 incorrect COMPLETE). |
| 4 | Can a dependent partial fact be incorrectly exposed? | No; containment + shared-clause + grounding gates; `compound_*` produce 0 tables. |
| 5 | Shared unresolved clauses respected? | Yes; entities/seasons/populations/constraints stay in every partition. |
| 6 | Clarification resolves only the affected Requirement? | Backend applies authoritative clarification semantics; unrelated artifacts retained. |
| 7 | Unrelated approved Artifacts retained after clarification? | Yes in the UI; backend recompute limited by architecture. |
| 8 | Affected Artifacts invalidated when semantics change? | Yes via current-contract non-conformance; no explicit retraction event. |
| 9 | Planner avoids recomputing already-satisfied independent Requirements? | Yes within a Goal; cross-turn reuse is a documented limit. |
| 10 | Attempt history treats clarification as new authoritative information? | Yes; context digest change permits a recorded bounded retry. |
| 11 | Same clarification repeat indefinitely? | No; resolved IDs are tracked and stale responses rejected. |
| 12 | Free-text clarification authoritative? | Yes; server-side validation + Engine interpretation. |
| 13 | Same-Goal LIMITED/WAITING_FOR_USER carry approved tables? | Yes. |
| 14 | Real same-Goal supported A + unresolved B produces approved table? | Yes (`compound_rbi`). |
| 15 | Dangerous compound avoids unsafe partial? | Yes (`dangerous_compound*`, `compound_closed_roof_zh`). |
| 16 | SSE replay preserves partial artifact + pending clarification? | Yes (run-scoped IDs, reducer dedupe, pending clarification persisted). |
| 17 | Frontend tests show table and clarification simultaneously? | Yes. |
| 18 | Real Playwright same-Goal flow passes? | Yes (test 4). |
| 19 | Execution-side conformance protections unchanged? | Yes; `receipt_usable`/`compare` untouched in strength. |
| 20 | Unsafe partial Artifact exposure still zero? | Yes. |
| 21 | Incorrect COMPLETE still zero? | Yes in all evidence/tests. |
| 22 | Existing frontend clarification/table flows green? | Yes. |
