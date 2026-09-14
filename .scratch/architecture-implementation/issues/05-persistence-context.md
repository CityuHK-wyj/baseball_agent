# Operational persistence and context projection

Status: OPEN
Blocked by: 04-planning-finalization.md

Use independent Operational PostgreSQL for runtime metadata, and a replaceable filesystem payload store. Separate domain versions, artifacts and attempt events; checkpoints reference a consistent recovery coordinate. Resume interrupted read-only work idempotently. Cross-run retrieval uses CompletionReports and accepted artifacts with user-confirmed context; Planner receives bounded indexes and assessment summaries with explicit expansion. Test interruption, duplicates, stale versions, lineage and context isolation.
