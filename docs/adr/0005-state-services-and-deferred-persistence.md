# Definition/state services with in-memory orchestration and deferred persistence

Status: accepted

## Context

D058 and D063 require that `RequirementState` and `ObjectiveState` exist from the
moment their definitions are created and are updated by state services, not by
sub-agents, and not as an artifact-pipeline by-product. D055/D056 and the brief
require a separate Operational PostgreSQL control plane and object storage for large
payloads. Those persistence decisions must not be guessed before the domain slice is
proven, and the brief warns against building ten half-finished modules at once.

## Decision

- `app/state/services.py` implements state projection as **pure functions**:
  `derive_requirement_state`, `derive_objective_state`, `optional_gaps` and
  `unmet_core_requirements`. They take definitions, assessments and the previous
  projection, and return a new projection. They are not agents: no scheduling, no LLM,
  no I/O.
- `RequirementCatalog` (ADR 0001) already materializes initial states alongside
  definitions; the orchestrator keeps the current projections in memory for a run.
- `Artifact.payload_ref` is a storage coordinate; artifacts and assessments never
  inline payload bytes. `ArtifactRegistry` stores metadata and lineage only.
- Operational persistence, checkpoints and cross-run Shared Context are **explicitly
  deferred** to ticket 05. The first slice uses in-memory stores and a deterministic
  synthetic tool so the loop is testable without credentials or analytics access.
- The choice of Operational PostgreSQL schema, retention and payload storage backend
  is therefore still open and must be recorded in a later ADR before implementation.

## Alternatives

- Persist during the first slice: rejected; it would freeze a schema before the domain
  contracts are reviewed and would block on a runtime database that is not available.
- Put state updates inside the Planner/Judge: rejected by D058/D063; it creates hidden
  coupling and makes transitions unauditable.
- A single `AgentState` blob: rejected by D043.

## Consequences

- The loop runs end-to-end in tests with no network, database or credentials.
- Resume/checkpoint behavior is not yet demonstrated; `StopReason` and independent
  stores are shaped so persistence can be added without changing domain contracts.
- Ticket 05 must define: operational schema, checkpoint coordinates, interrupted-run
  handling (`INTERRUPTED`, not blindly `RUNNING`), idempotent replay and Shared Context
  projection bounds.
