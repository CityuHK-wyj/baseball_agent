# Feature Engine metric artifacts and Source Mapping execution

Status: accepted

## Context

D020/D021/D037 and posts 01/02/09 require `MetricDefinition` + `SourceMapping` to carry
execution knowledge, a deterministic Feature Engine whose output is a normal Artifact
with lineage, and routing that does not hard-code physical columns in the Planner. The
contracts existed but nothing consumed them.

## Decision

- `app/features/metrics.py`: `FeatureEngine` computes named `FeatureComputation`s over
  raw rows and returns a `FeatureResult` whose `artifact` is `artifact_type="FEATURE"`,
  carries `lineage=(input_artifact_id,)`, `Provenance(source_kind="FEATURE")`, and a
  descriptor mirrored from the input (entities, constraints, granularity, population,
  observed time range). It never returns a private format and never judges the result.
  Unknown computations and empty samples fail loudly rather than producing a silent 0.
- `app/agent/source_mapping.py`: `SourceMappingResolver` turns a task's required
  `data_keys` into an `ExecutionRoute`:
  - **DIRECT** — a configured tool provides the mapped source kind;
  - **CALCULATED** — the Feature Engine computes it (`required_source_kind="FEATURE"`);
  - **NO_MAPPING** — an unmapped key or a mapped source with no configured tool.
- `Router.route(..., execution_route=...)`: a DIRECT/CALCULATED route becomes a
  system-derived capability constraint, so it outranks Planner preference but still
  yields to user hard constraints and policy. NO_MAPPING blocks routing outright.
- The Planner remains at the semantic level (D015); only the resolver and Router touch
  physical sources.

## Alternatives

- Planner emits physical columns/SQL: rejected by D015.
- Feature Engine output stored in a private shape: rejected by D037.
- Treating a Source Mapping as a soft preference: rejected; a mapping is execution
  knowledge, not a preference.

## Consequences

- Feature Engine and Source Mapping are deterministic and testable with no database.
- The Orchestrator does not yet build `ExecutionRoute`s per task; that wiring is the
  next step and must pass the requirement's `data_keys` through the resolver.
- `SourceMappingResolver` treats a task requiring both DIRECT and CALCULATED metrics as
  DIRECT-first; a mixed-route plan is a future refinement.
