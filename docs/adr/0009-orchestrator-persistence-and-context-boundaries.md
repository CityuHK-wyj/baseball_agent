# Orchestrator persistence integration and context boundaries

Status: accepted

## Context

ADR 0007 delivered persistence services but left the Orchestrator unwired, and ADR 0008
delivered `ContextService` without a consumer. The brief requires persisting in safe
order (payload/artifact before any state that references it), checkpoints at defined
positions, a resumable full vertical flow that reuses artifacts without duplicate
execution, and strict context boundaries: the Planner gets bounded knowledge and
summaries (not upstream history), while the Response gets only accepted products and
critical knowledge.

## Decision

- `ToolResult` and `ExecutionOutcome` may carry an optional `payload` (bytes) and
  content type. The payload is the only large value that moves through execution; it
  is written to `ArtifactStorage`, never inlined into state.
- `Orchestrator` takes an optional `RunRecorder`, an optional `ContextService` and an
  optional fixed `run_id`. With no recorder the behavior is unchanged.
- Recording order within a round: persist payload → artifact metadata → assessment →
  requirement states → objective state → execution references → checkpoint. A payload
  write failure propagates and aborts before any assessment or state is written, so no
  state can reference an artifact that failed to persist.
- Checkpoints are taken at `PLAN_ACCEPTED`, `ARTIFACT_ASSESSED`, `PLANNER_TERMINAL` and
  `FINALIZATION`. `RunRecorder.record_artifact` additionally writes the metadata only
  after a successful payload write.
- `ResumeService.rehydrate(run_id)` returns a `RestoredRun` view over independently
  persisted domains (artifacts, assessments, requirement states, objective state). It is
  not a merged `AgentState` blob. `Orchestrator.run(..., restored=...)` registers the
  artifacts, restores assessments and requirement states, and re-latches a terminal
  Planner decision. A resumed satisfied run therefore performs zero executions.
- Context boundaries: `KNOWLEDGE_KINDS` defines what may flow through Shared Context.
  The Planner receives a bounded `ContextPackage` (`purpose=PLANNER`) plus requirement
  states, accepted artifact index and assessment summaries, and an execution summary.
  The Response is built from accepted assessments only, and its
  `critical_shared_knowledge` comes from a `purpose=RESPONSE` package, which excludes
  attempts, routing, drafts, judge reasoning, rejected evidence, unused RAG and
  superseded plans.

## Alternatives

- Persist inside the Planner/Judge: rejected; it spreads write logic across agents.
- Serialize the whole `RunResult` as one checkpoint: rejected; it merges state domains
  and copies payloads.
- Let the Response read the ContextService directly: rejected; the Response must receive
  a projected package, not a query interface.
- Require payload bytes from every tool: rejected; synthetic and metadata-only tools may
  legitimately have none, so payload is optional and only persisted when present.

## Consequences

- The full run → persist → checkpoint → resume → reuse flow is tested end to end with
  deterministic tools.
- `RunResult.assessments` and `CompletionReport.final_artifact_refs` are derived from the
  AssessmentService, so restored assessments are included in a resumed run's summary.
- Still outstanding: an Operational PostgreSQL implementation of `OperationalStore`, and
  real Metric/Schema Registry sources behind `ContextSource`.
