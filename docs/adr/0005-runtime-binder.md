# ADR 0005 — Runtime Binder Owns Artifact Resolution

Status: accepted

## Decision
`Need B depends_on Need A` plus semantic `input_requirements` is resolved by the Runtime
Binder against the accepted exports of Need A. Artifact ids, export ids, versions and
scope compatibility are resolved by the runtime. A `{need_id}:{EXPORT}` string that does
not resolve is `PSEUDO_REFERENCE` and is rejected.

## Rationale
Planner-generated pseudo references caused empty inputs and silent wrong answers.

## Consequences
- A declared required input with no compatible export blocks the Need with
  `INPUT_UNRESOLVED` instead of running with empty input.
- Binding compatibility checks entity namespace, population, game types, season and time
  overlap, not just export type.
