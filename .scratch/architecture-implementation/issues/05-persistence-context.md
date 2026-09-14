# Operational persistence and context projection

Status: PARTIAL
Blocked by: None

Use independent Operational PostgreSQL for runtime metadata, and a replaceable filesystem payload store. Separate domain versions, artifacts and attempt events; checkpoints reference a consistent recovery coordinate. Resume interrupted read-only work idempotently. Cross-run retrieval uses CompletionReports and accepted artifacts with user-confirmed context; Planner receives bounded indexes and assessment summaries with explicit expansion. Test interruption, duplicates, stale versions, lineage and context isolation.

## Comments

- Delivered (ADR 0007): `app/persistence/store.py` (`OperationalStore` +
  `SqliteOperationalStore`), `app/persistence/artifacts.py` (`ArtifactStorage` +
  `LocalFilesystemArtifactStorage`), `app/models/checkpoint.py`, `app/persistence/recorder.py`
  (`RunRecorder`), `app/persistence/resume.py` (`ResumeService`).
- Tests: `tests/persistence/` — artifact round-trip/path escape/immutability, object
  versioning, append-only checkpoints, file reopen, interrupted-run reclassification,
  artifact reuse, terminal survival, idempotent resume, finalization products.
- Remaining: wire `RunRecorder`/`ResumeService` into the `Orchestrator`; persist tool
  payload bytes (today `ToolResult` carries metadata only); Operational PostgreSQL
  implementation of the same Protocol; Shared Context projection and cross-run
  retrieval/context isolation tests.
