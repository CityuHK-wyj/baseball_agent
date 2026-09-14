# Operational persistence and context projection

Status: OPEN
Blocked by: 04-planning-finalization.md

Use independent Operational PostgreSQL for runtime metadata, and a replaceable filesystem payload store. Separate domain versions, artifacts and attempt events; checkpoints reference a consistent recovery coordinate. Resume interrupted read-only work idempotently. Cross-run retrieval uses CompletionReports and accepted artifacts with user-confirmed context; Planner receives bounded indexes and assessment summaries with explicit expansion. Test interruption, duplicates, stale versions, lineage and context isolation.

## Comments

- Prerequisite seams now exist: `Artifact.payload_ref` is a storage coordinate, `ArtifactRegistry`
  holds metadata only, `RunResult` exposes attempt/assessment history, and ADR 0005 defers the
  operational schema choice to this ticket.
- Not started. No resume/checkpoint behavior is demonstrated yet; do not claim recoverability.
