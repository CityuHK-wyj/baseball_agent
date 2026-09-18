# v0.5 planner-convergence exploration findings

Status: internal self-directed exploration for `pi/v0.5-planner-convergence`. This file and
`explore.py` / `explore.jsonl` are review artifacts. None of the exploratory wording is
used in production prompts, branches or tests that assert answer text.

## How the exploration was run

- Live product runtime (`build_runtime(use_llm=True)`) with the configured model and live
  PostgreSQL/Parquet where available.
- Thematically diverse, internally invented queries (never supplied by the user, never
  requested as a holdout set): simple DB, roster→DB composition, ranking, DB→Web,
  ambiguous identity, impossible/future, contradictory, multilingual, missing capability,
  valid zero-result, malformed, changed constraint.
- Evaluation follows the project's evaluation principle: preserve the Goal, identify
  obligations, select feasible capabilities, use real schema, bind the intended Artifact,
  make values flow, let failures change the plan, keep the Judge independent, and make the
  final state truthful. "Did it answer?" is not the metric.

## Live observations

### 1. Simple DB request — planner invented an entity and mis-typed an export reference

Query (invented): count pitches in a month, no team named. The live semantic layer invented
a team, and the planner produced a `web_research` → `evidence_entities` →
`local_analytics` chain whose `analytical_query.entity_set.export_ref` was a **Need id**,
not an export id. `local_analytics` failed `INPUT_UNRESOLVED` and the run ended `FAILED`.

Two general, non-overfitting corrections followed:

- `local_analytics` now falls back to the compatible export the engine already bound
  explicitly from the declared dependency when a model-supplied `export_ref` does not
  resolve. This is still explicit binding (the dependency was declared), never ambient
  injection. Property test: `test_unresolvable_entity_set_ref_recovers_from_bound_export`.
- The planner prompt now states that `entity_set.export_ref` must be an exact export id
  from the available-exports list, and the available-exports list is rendered into both the
  initial and replan prompts.

The invented entity itself is a model-quality limitation, not a runtime convergence defect:
the runtime did not fabricate evidence for it and reported the failure. This is recorded as
a known limitation.

### 2. Contradictory request — safe clarification

A request with mutually exclusive thresholds returned `WAITING_FOR_USER` with a
clarification rather than fabricating or silently choosing. Safe.

### 3. Impossible future request — typed, non-COMPLETE, truthful

A request for a future season returned:

- `Goal.conflicts = [FUTURE_RESULT/UNKNOWN]` and a `USER_CONFLICT` event;
- `core_goal_supported = false`, status `FAILED` (not `COMPLETE`);
- obligation coverage all `MISSING`, with gaps explicitly saying
  `user requirement is not satisfiable: UNKNOWN FUTURE_RESULT`;
- live web failures classified as `SOURCE_TRANSIENT`, not silently promoted.

This validates the typed-conflict and recovery-class requirements end to end.

### 4. Live infrastructure notes

- Parquet live validation re-run and passed (compiled `FULL` coverage, window predicate
  present, execution `OK`, result matched an independent DuckDB reference).
- PostgreSQL read-only executor reachable (a bounded `COUNT(*)` succeeded).
- The configured model was very slow (~3–4 minutes per planner completion), so only a small
  number of live sessions were run. Property tests cover the convergence dimensions
  deterministically and cheaply.
- Live web research was unavailable in this sandbox and was correctly reported as
  `SOURCE_TRANSIENT`; SSRF/streaming controls were not relaxed to obtain a green result.

## Dimension matrix covered (deterministic property tests + live samples)

| Dimension | Where covered |
|---|---|
| simple vs compositional | `test_db_values_parameterize_a_later_web_query`, composition suite |
| single-source vs multi-tool | `test_unrelated_web_entities_do_not_contaminate_population`, `test_local_analytics_feeds_compute` |
| DB-first vs Web-first | `ValueFlowTests`, `test_web_to_entity_resolution_to_sql` |
| historical/current | roster/v0.4 invariants (`active_roster` vs historical mismatch) |
| ranking/comparison/derived | IR semantics + qualification tests |
| ambiguous identity | `test_ambiguous_entity_is_preserved_not_guessed` |
| missing capability | `test_capability_absence_differs_from_data_absence`, `test_structurally_impossible_capability_is_not_repeated` |
| impossible request | `ContradictionTests`, live future sample |
| contradictory request | `test_contradictory_thresholds_are_impossible`, live contradiction sample |
| malformed request | `test_unknown_semantic_handle_is_still_rejected`, malformed sample (clarification) |
| multilingual / mixed | `MultilingualConvergenceTests` |
| follow-up / changed constraint | `RuntimeConversation` goal revision + `changed_constraint` scenario |
| unsupported analytical operation | `QualificationTests`, `test_failure_classes_are_distinct` |
| valid zero-result | `test_valid_empty_is_not_execution_failure`, `EMPTY_RESULT` attempts |

## Failures still observed

- The live model produced an entity that was not in the request. The runtime refused to
  fabricate evidence and ended `FAILED`, but planner input quality remains model-dependent.
- The live model used a Need id where an export id was required. The runtime now recovers
  when an explicit dependency export exists; when no compatible export exists it still
  fails with a structured `MISSING_ENTITY_SET` gap.
- Live web research was unavailable in the environment; this is a provider capability
  limitation, correctly classified, not a runtime defect.
