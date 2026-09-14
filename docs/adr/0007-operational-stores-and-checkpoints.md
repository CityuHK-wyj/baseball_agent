# Separated operational stores with checkpoint coordinates

Status: accepted

## Context

D054–D056 and ticket 05 require the agent's runtime data to be physically separate
from the baseball analytics data plane, large payloads to live outside the database,
and a checkpoint to be a *consistent recovery coordinate* rather than a dump of the
whole `AgentState`. The brief also requires that an interrupted `RUNNING` execution is
not blindly assumed to still be running, and that already-persisted artifacts are
reused instead of re-executed. No operational PostgreSQL is available in this
environment, so the design must be testable locally and swappable later.

## Decision

- `app/persistence/store.py`: an `OperationalStore` Protocol with a local
  `SqliteOperationalStore`. It stores versioned JSON objects (runs, objectives,
  requirements, plans, states, artifact metadata, assessments, reports) keyed by
  `(kind, object_id)` and append-only checkpoints. Idempotent saves do not bump the
  version; a changed payload does. SQLite is the first, replaceable implementation.
- `app/persistence/artifacts.py`: an `ArtifactStorage` Protocol with
  `LocalFilesystemArtifactStorage`. A single path-segment artifact reference is
  validated, storage is content-addressed by SHA-256, and re-putting a reference with
  different bytes is rejected because artifacts are immutable.
- `app/models/checkpoint.py`: `Checkpoint` records `recovery_position`,
  `state_version_refs`, `active_work_refs` and `pending_request_refs`. Positions cover
  run start, plan accepted, artifact assessed, waiting for user, planner terminal,
  objective terminal and finalization.
- `app/persistence/recorder.py`: `RunRecorder` is the write side. It writes the
  artifact payload before saving any state that references it, records assessments,
  states, executions, CompletionReport and ResponsePackage, and takes checkpoints
  whose `state_version_refs` are derived from stored state versions.
- `app/persistence/resume.py`: `ResumeService.build_plan` reads the latest checkpoint,
  reclassifies `PENDING`/`RUNNING` executions as `INTERRUPTED` (persisting the change
  so resume is idempotent), lists reusable artifacts, and reports whether a terminal
  Planner decision is preserved.

## Alternatives

- A single serialized `AgentState` checkpoint: rejected by D054 and the brief; it
  couples unrelated state domains and copies payloads.
- Storing payloads in the operational database: rejected by D056.
- Operational PostgreSQL immediately: deferred. No such database is available here,
  and the Protocol keeps the swap to PostgreSQL a one-implementation change.

## Consequences

- Ticket 05 is a foundation plus a tested resume slice; the `Orchestrator` is **not**
  yet wired to the recorder. That wiring is the next task and must persist payload
  bytes from the tool layer (today `ToolResult` carries artifact metadata, not bytes).
- `TaskExecution.status` gained `RUNNING` so an interrupted run is representable.
- `SqliteOperationalStore` is single-writer and intended for local/dev; concurrent
  runs are not yet a supported scenario.
