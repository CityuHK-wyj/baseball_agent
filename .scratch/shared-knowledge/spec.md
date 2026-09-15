# Shared Knowledge Base (V1)

Status: IN_PROGRESS

Source: user development request 2026-09-15. Builds on the frozen architecture and the
Shared Context slice (ADR 0008, 0010). Shared Knowledge stays a capability, never a
Retrieval/Knowledge/RAG agent (D057).

## Goal

Turn Shared Knowledge from a minimal Context/Registry slice into a persistent, sourced,
versioned MLB domain knowledge base that the agent can inspect, rebuild and query.

## Non-negotiable architecture rules

- `Shared Knowledge != Retrieval Agent`. It is infrastructure behind `ContextSource`.
- `Persist broadly, retrieve narrowly.` / `System State != LLM Context`.
- `Collected Knowledge != Approved Knowledge`: web-fetched material is staged
  `COLLECTED` and promoted only through deterministic validation.
- Analytics data plane (`baseball_analytics`) stays read-only and separate. Knowledge
  lives in its own logical store/schema, never in the analytics or runtime object tables.
- Do not dump copyrighted full text; store rule numbers, names, structured summaries,
  conditions, official URLs and short necessary quotations only.
- Versioning and `effective_from`/`effective_to`/`as_of` are mandatory for rules, CBA,
  rosters, ballparks and League structure.

## Storage

- Dev/local: SQLite at `.runtime/knowledge.db` (gitignored, rebuildable).
- Production: PostgreSQL `knowledge` schema behind the same `KnowledgeStore` Protocol.
- Seed + manifests are committed under `knowledge/` so the store is reproducible:
  `source manifests + seed packs + ingestion code => knowledge store`.
- Snapshot tables version the whole store for atomic refresh.

## Knowledge domains (V1)

1. Official Baseball Rules (current 2026 edition) — rule structure + concept items.
2. MLB competition / roster / transaction rules (CBA-sensitive with effective dates).
3. Bilingual baseball glossary (EN/ZH) including metrics.
4. Metric definitions + Statcast terminology (single canonical source feeding
   `MetricRegistry`).
5. Pitch terminology, plate discipline, qualification rules.
6. All 30 current MLB teams, divisions, leagues, ballparks, franchise lineage.
7. Notable player profiles (active + historical) and entity aliases.
8. Awards and Hall of Fame knowledge.
9. League / competition context (season, postseason, draft, deadlines) with `as_of`.
10. Scouting / analysis concepts (marked as concepts, not official definitions).
11. Community source directory (web-verified activity + `last_verified`).
12. Trusted source registry with authority levels and `best_for`.

## Authority model

`OFFICIAL > AUTHORITATIVE_REFERENCE > TRUSTED_ANALYTICS > TRUSTED_MEDIA > COMMUNITY > UNVERIFIED`

Community sources are never factual authority for rules or official facts; they are
allowed for sentiment, nicknames and discussion topics.

## Retrieval

Structured lookup first: canonical key, alias, structured filters, token/full-text.
Optional semantic retrieval only later, and never as truth source. Ranking considers
authority, entity match, token relevance, temporal validity, freshness and language.

## Deliverables

- Models: `KnowledgeSource`, `KnowledgeItem`, `KnowledgeRelation`, `KnowledgeSnapshot`.
- Store: SQLite + PostgreSQL, versioned, snapshot/atomic activation.
- Source registry, ingestion + validation pipeline, live refresh (MLB Stats API, OBR PDF).
- `KnowledgeContextSource`, entity-dictionary and metric-registry projections.
- CLI: `knowledge status|sources|search|show|refresh|validate`.
- Tests, `docs/usage/knowledge-base.md`, `docs/development/shared-knowledge.md`, ADR 0019.
