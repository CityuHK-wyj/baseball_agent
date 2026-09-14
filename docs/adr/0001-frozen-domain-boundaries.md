# Preserve the frozen domain boundaries

Status: accepted

Following blog decisions D034–D066 (2026-09-14) and the development request, keep definitions separate from runtime state and make Initial Requirements an immutable baseline owned before planning. Planner may add supporting requirements, but completion is gated only by initial requirements; assessed usability belongs to an Artifact/Requirement pair, never to the Artifact itself.

The existing package layout is retained. State updates and context retrieval are infrastructure, not additional business agents. Analytics PostgreSQL, DuckDB and Parquet are read-only; legacy ingestion and snapshot-writing functions must not be exposed to the runtime. Operational persistence will use a separate control plane with independently stored payloads and checkpoint coordinates, rather than full-state dumps.

This replaces the legacy direct-tool loop as the intended architecture, not as a claim of implementation. Domain contracts will be introduced in tested vertical slices; no speculative orchestration framework or vector database is required to formalize them.
