# Persistent Shared Knowledge base

Status: accepted

## Context

ADR 0008 deliberately implemented Shared Context as a deterministic retrieval capability
with in-memory sources, and ADR 0010 added the Metric and Schema registries as
`ContextSource`s. That left the long-lived MLB domain knowledge — rules, terminology,
teams, players, sources, community — to the model's prior knowledge or ad-hoc web
searches. The brief requires a persistent, sourced, versioned knowledge base that is
inspectable, rebuildable and separate from the analytics data plane.

D057 still forbids a Retrieval/Knowledge/RAG agent, and the brief requires
`System State != LLM Context` and `Collected Knowledge != Approved Knowledge`.

## Decision

- `app/models/knowledge.py`: `KnowledgeSource`, `KnowledgeItem`, `KnowledgeRelation`,
  `KnowledgeSnapshot`, `KnowledgeQuery`, `KnowledgeMatch`, `KnowledgeDiff`. Items carry a
  stable `canonical_key`, aliases, authority, `effective_from`/`effective_to`, `as_of`,
  `last_verified_at`, a freshness policy, a verification status and a version.
- `app/knowledge/store.py`: a `KnowledgeStore` Protocol with a dialect-agnostic SQL
  implementation. SQLite (`knowledge_*` tables) is the local/dev and test store;
  `PostgresKnowledgeStore` writes the same tables into a dedicated `knowledge` schema and
  never touches `baseball_analytics`. Items are versioned (a `knowledge_versions`
  history), relations and snapshots are stored.
- `app/knowledge/registry.py`: `SourceRegistry` with the authority ladder
  `OFFICIAL > AUTHORITATIVE_REFERENCE > TRUSTED_ANALYTICS > TRUSTED_MEDIA > COMMUNITY >
  UNVERIFIED` and a `best_for` specialisation map (data only; not routing logic).
- `app/knowledge/freshness.py`: declarative maximum ages per policy. Freshness is
  metadata, never an LLM judgement.
- `app/knowledge/retrieval.py`: `KnowledgeRetriever` answers canonical-id/alias lookups,
  structured filters, token search and relation traversal. Ranking is deterministic and
  considers authority, entity match, token relevance, temporal validity, freshness and
  language. No embeddings are involved.
- `app/knowledge/ingestion.py`: fetch/normalize -> validate -> stage (`COLLECTED`,
  invisible to retrieval) -> activate. Activation supersedes domain items that
  disappeared, so a refresh replaces a category atomically instead of mixing facts.
- `app/knowledge/refresh.py`: live refresh from the official MLB Stats API (teams,
  ballparks, divisions, leagues) and rulebook verification from the official OBR PDF,
  with the committed seed packs as the reproducible base.
- `app/context/knowledge_source.py`: `KnowledgeContextSource` implements the existing
  `ContextSource` Protocol, so knowledge reaches the Planner and Response through the
  same bounded projection as the other registries. `KNOWLEDGE` was added to
  `Orchestrator.KNOWLEDGE_KINDS`.
- `app/knowledge/entities.py`: the knowledge store is the single identity authority; the
  `EntityDictionary` and `MetricRegistry` are projections of it, not second copies.

## Alternatives

- A separate Retrieval/Knowledge/RAG agent: rejected by D057 and by the architecture.
- Vector search as the primary path: rejected. Structured lookup and full-text come first;
  embeddings, if ever added, are a rebuildable auxiliary index and never the truth source.
- Storing the knowledge in the runtime object tables or in `baseball_analytics`: rejected.
  Knowledge is neither run state nor analytics data.
- Dumping full rulebook/article text: rejected on copyright and maintenance grounds.
  Items store rule numbers, names, structured summaries, conditions, official URLs and
  short necessary quotations only.
- Hard-coding sources and knowledge in Python: rejected; manifests under
  `knowledge/sources/` and seed packs under `knowledge/seed/` are committed and the store
  is rebuildable from them.

## Consequences

- The knowledge store is a first-class persistent artifact with provenance, freshness and
  versioning; every item can answer "why do I believe this, from where, checked when".
- Web-fetched material is staged `COLLECTED` and only activated after deterministic
  validation; unverified community profiles remain `UNVERIFIED` and never outrank official
  facts.
- Community nicknames are `ALIAS` items with low confidence, not entity aliases, so they
  do not auto-resolve.
- CBA-sensitive and competition rules carry effective dates so a 2026 rule is not applied
  to a 2005 season by accident.
- Retrieval is still deterministic and explainable; the seeded knowledge is inspected and
  refreshed through `python -m app.cli knowledge ...`.
