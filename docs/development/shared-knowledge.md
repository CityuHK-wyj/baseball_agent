# Shared Knowledge (developer guide)

Shared Knowledge is infrastructure, not an agent. It is a persistent MLB domain knowledge
base that answers rule, terminology, identity and source questions deterministically, and
feeds `ContextService`. Read [../adr/0019-shared-knowledge-base.md](../adr/0019-shared-knowledge-base.md)
for the rationale and [../usage/knowledge-base.md](../usage/knowledge-base.md) for the CLI.

## Where things live

```text
app/models/knowledge.py          frozen contracts (KnowledgeItem, KnowledgeSource, ...)
app/knowledge/store.py           persistent store: SQLite dev + PostgreSQL 'knowledge' schema
app/knowledge/freshness.py       freshness policies -> maximum age in days
app/knowledge/registry.py        SourceRegistry + authority ladder
app/knowledge/retrieval.py       KnowledgeRetriever (lookup / filter / rank / traverse)
app/knowledge/ingestion.py       validate -> stage -> activate -> supersede
app/knowledge/refresh.py         live refresh (MLB Stats API, OBR PDF) + seed reload
app/knowledge/fetch.py           retrying HTTP helper (standard library only)
app/knowledge/loader.py          load manifests + seed packs, rebuild the store
app/knowledge/service.py         KnowledgeBase facade
app/knowledge/entities.py        EntityDictionary / MetricRegistry projections
app/context/knowledge_source.py  ContextSource over the store
knowledge/sources/*.json         committed source manifests
knowledge/seed/*.json            committed structured knowledge packs
```

Storage locations:

- dev/test: SQLite at `$KNOWLEDGE_STORE_PATH` (default `.runtime/knowledge.db`,
  gitignored, rebuildable);
- production: PostgreSQL `knowledge` schema behind `PostgresKnowledgeStore`;
- analytics (`baseball_analytics`) and the runtime object tables are never touched.

## Data model

`KnowledgeItem` is the unit. `canonical_key` is the stable identity; `knowledge_id` is
namespaced (`RULE:...`, `TXN:...`, `TEAM:LAD`, `PLAYER:660271`, ...). Items carry:

- `knowledge_type`: RULE, TERM, METRIC, TEAM, DIVISION, LEAGUE, BALLPARK,
  PLAYER_PROFILE, AWARD, LEAGUE_STRUCTURE, TRANSACTION_RULE, SCOUTING_CONCEPT, SOURCE,
  COMMUNITY_CREATOR, HISTORICAL_CONTEXT, ALIAS.
- `source_refs` + `source_authority`: provenance and the strongest authority among them.
- `effective_from` / `effective_to` / `as_of`: temporal validity.
- `last_verified_at` + `freshness_policy`: staleness.
- `verification_status`: `COLLECTED` (staged), `VERIFIED`, `UNVERIFIED`.
- `status`: `COLLECTED`, `ACTIVE`, `HISTORICAL`, `SUPERSEDED`, `UNVERIFIED`.
- `version`: bumped on every content or status change; the full history is kept.

`KnowledgeRelation` gives the small graph (`PLAYS_FOR`, `MEMBER_OF`, `HOME_BALLPARK`,
`DEFINED_BY`, `PROVIDED_BY`, `SUPERSEDES`, `COVERS`, `REFERS_TO`, `FORMERLY_KNOWN_AS`,
`PART_OF`).

## Ingestion lifecycle

```text
fetch / seed  ->  normalize  ->  validate  ->  stage COLLECTED  ->  activate ACTIVE
                                                       |                    |
                                             invisible to retrieval   supersede removed items
```

`KnowledgeIngester.ingest(pack)` runs this. Validation is per type: teams must match the
exact 30-franchise set and carry league/division/abbreviation; rules must reference a rule
number; metrics must declare a definition and a provider; community profiles must carry a
structured profile; every `source_ref` must resolve. Fatal problems abort the pack; item
problems are rejected individually and reported in the `KnowledgeDiff`.

A refresh never mutates active knowledge before validation succeeds, and activation is
versioned, so the previous active state can be inspected in `knowledge_versions`.

## Retrieval and context

`KnowledgeRetriever` chooses the cheapest correct path:

1. canonical id / canonical key / exact alias;
2. structured filters (type, status, language, entity, tags, authority floor, as-of);
3. token search as a candidate filter;
4. relation traversal.

Ranking adds authority, entity match, token overlap, temporal validity, freshness and
language. `KnowledgeContextSource` projects matches into `ContextItem`s with a
`provenance_ref` of `knowledge:<id>` and a freshness rank, so the Planner and Response
receive bounded knowledge through the same boundary as the registries. Planner context is
bounded (`max_items`); the Response sees accepted products plus critical knowledge only.

## Single source of truth for identity and metrics

- `entity_dictionary_from_knowledge(store)` builds the `EntityDictionary` from TEAM,
  LEAGUE and PLAYER_PROFILE items. Adding a team or player to the store makes it
  resolvable; aliases are not maintained anywhere else.
- `metric_registry_from_knowledge(store)` builds the `MetricRegistry` from METRIC items.
  Metric canonical keys are lowercase snake_case, so the registry and the knowledge base
  cannot disagree about a metric.

Community nicknames are deliberately excluded from entity aliases. They are `ALIAS` items
with `structured_payload.confidence = "low"` and a `REFERS_TO` relation, so they surface
through retrieval/clarification instead of auto-resolving.

## Adding knowledge

1. Add or reuse a source in `knowledge/sources/*.json` with an explicit authority level.
2. Add items to the relevant `knowledge/seed/*.json` pack, or write a fetcher in
   `app/knowledge/refresh.py` if a structured source exists.
3. Run `python -m app.cli knowledge validate` and `python -m app.cli knowledge --seed status`.
4. Add a domain test under `tests/knowledge/`.
5. If the change is architectural, record it in an ADR.

Do not copy full copyrighted text. Store rule numbers, names, structured summaries,
conditions, official URLs and short necessary quotations.
