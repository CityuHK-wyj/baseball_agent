# Operational persistence and context projection

Status: PARTIAL
Blocked by: None

Use independent Operational PostgreSQL for runtime metadata, and a replaceable filesystem payload store. Separate domain versions, artifacts and attempt events; checkpoints reference a consistent recovery coordinate. Resume interrupted read-only work idempotently. Cross-run retrieval uses CompletionReports and accepted artifacts with user-confirmed context; Planner receives bounded indexes and assessment summaries with explicit expansion. Test interruption, duplicates, stale versions, lineage and context isolation.

## Comments

- Delivered (ADR 0007, 0009): `app/persistence/store.py` (`OperationalStore` +
  `SqliteOperationalStore`), `app/persistence/artifacts.py` (`ArtifactStorage` +
  `LocalFilesystemArtifactStorage`), `app/models/checkpoint.py`, `app/persistence/recorder.py`
  (`RunRecorder`), `app/persistence/resume.py` (`ResumeService`, `RestoredRun`).
- Orchestrator is wired: optional `RunRecorder` + `ContextService`; safe record order
  (payload → artifact → assessment → states → execution refs → checkpoint); checkpoints
  at `PLAN_ACCEPTED`, `ARTIFACT_ASSESSED`, `PLANNER_TERMINAL`, `FINALIZATION`; and
  `run(..., restored=...)` reuses artifacts and preserves a terminal decision.
- Context is wired (ADR 0008, 0009): Planner receives a bounded `purpose=PLANNER`
  package plus summaries; Response receives accepted evidence and `purpose=RESPONSE`
  critical knowledge only.
- Tests: `tests/persistence/` including `test_orchestrator_persistence.py` (full run →
  persist → checkpoint → interrupt → resume → reuse, planner-terminal survival), and
  `tests/context/test_context_wiring.py` (Planner/Response boundaries).
- Remaining: an Operational PostgreSQL implementation of the same `OperationalStore`
  Protocol, real Metric/Schema Registry `ContextSource`s, and cross-run context
  isolation tests.
