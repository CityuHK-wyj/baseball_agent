# Shared Knowledge base

The Baseball Agent keeps long-lived MLB domain knowledge in a persistent **Shared
Knowledge** store: official playing rules and their concepts, roster/transaction rules,
a bilingual glossary and metric definitions, all 30 current teams and ballparks, notable
player identities, awards, trusted sources and the community source directory.

Shared Knowledge is a capability, not an agent. It answers deterministic lookups and feeds
`ContextService`; it performs no reasoning and is never the planner.

## Where it is stored

| Environment | Location |
| --- | --- |
| Local / dev / tests | SQLite at `.runtime/knowledge.db` (override `KNOWLEDGE_STORE_PATH`) |
| Production | PostgreSQL **`knowledge` schema** (separate from `baseball_analytics`) |
| Rebuildable source | committed manifests `knowledge/sources/*.json` + seed `knowledge/seed/*.json` |
| Generated DB | never committed (`.runtime/` is gitignored) |

The store is separate from both the read-only analytics plane and the agent runtime object
tables. You can delete `.runtime/knowledge.db` and rebuild it at any time.

## Inspect it

```bash
# counts by type, freshness and the latest snapshot
python3 -m app.cli knowledge --seed status

# the source registry with authority levels and last-checked dates
python3 -m app.cli knowledge sources
python3 -m app.cli knowledge sources --community

# search by term, alias, key or a Chinese name
python3 -m app.cli knowledge search "balk"
python3 -m app.cli knowledge search "道奇"
python3 -m app.cli knowledge search "wRC+"
python3 -m app.cli knowledge search "qualified hitter" --as-of 2021-06-01

# one item with provenance, version history and relations
python3 -m app.cli knowledge show TEAM:LAD
python3 -m app.cli knowledge show TXN:designated_for_assignment

# validate the committed seed packs
python3 -m app.cli knowledge validate
```

`--seed` rebuilds the store from the committed manifests and packs before running the
command. Without it, the store is used as-is; if it is empty it is seeded automatically.

## Refresh it

```bash
python3 -m app.cli knowledge refresh teams       # live: MLB Stats API
python3 -m app.cli knowledge refresh rules       # live: official 2026 OBR PDF
python3 -m app.cli knowledge refresh glossary    # reloads the committed seed pack
python3 -m app.cli knowledge refresh players     # reloads the committed seed pack
python3 -m app.cli knowledge refresh community   # reloads the committed seed pack
python3 -m app.cli knowledge refresh all
```

A refresh fetches, normalizes, validates, stages as `COLLECTED` and only then activates.
If a source is unreachable it fails loudly and the current active knowledge is untouched.
Live refresh currently covers reference data (teams, ballparks, divisions, leagues) and
verifies the rulebook edition/numbering; the glossary, players and community packs are
curated and reloaded from seed.

## What it contains

- **Rules** — the 2026 Official Baseball Rules structure (1.00–9.00 plus every numbered
  section), rule concepts (balk, infield fly, dropped third strike, force play,
  obstruction, interference, batting out of turn, DH, pitch timer, three-batter minimum,
  extra-inning runner, ...) and competition/transaction rules (26/40-man rosters, options,
  DFA, waivers, Rule 5, service time, arbitration, qualifying offer, CBT, injured lists,
  two-way player, posting, ...) with effective dates for CBA-sensitive items.
- **Glossary** — standard batting/pitching/fielding stats, sabermetrics (wOBA, wRC+, WAR
  with **provider-specific** fWAR/bWAR/WARP, FIP/xFIP/SIERA, RE24, WPA, ...), Statcast
  metrics (exit velocity, launch angle, barrel, xBA/xSLG/xwOBA/xERA, bat speed, sprint
  speed, OAA, pop time, ...), pitch types, plate discipline and qualification rules.
- **Identity** — all 30 teams with bilingual, commonly-used Chinese names, divisions,
  leagues, ballparks (sponsorship name plus common name and former names), franchise
  lineage and notable player profiles (active identities verified live from the Stats API,
  plus historical figures).
- **Sources** — an authority-ranked registry (`OFFICIAL > AUTHORITATIVE_REFERENCE >
  TRUSTED_ANALYTICS > TRUSTED_MEDIA > COMMUNITY > UNVERIFIED`) and a community directory
  recording platform, topics, language, activity status and last verification.

## What it intentionally does NOT store

- Live statistics (today's AVG, current streak, this season's OPS) — those are data-plane
  queries.
- Current roster/IL status as a durable fact — team affiliation is stored with an `as_of`
  date and is refreshed, but the live roster is a data query.
- Full copyrighted rulebook or article text — only rule numbers, names, structured
  summaries, conditions, official URLs and short necessary quotations.
- Raw web search output, every Reddit post or every pitch.
- Low-confidence community nicknames as if they were canonical names.

## Freshness and provenance

Every item records its sources, the authority of the strongest source, when it was last
verified and how long that kind of knowledge stays trustworthy (official rules: a season;
CBA rules: agreement-triggered; teams/ballparks: annual; community creators: 90 days).
`knowledge status` reports stale items, and unverified community profiles are kept
`UNVERIFIED` rather than presented as fact.

## How the agent uses it

`KnowledgeContextSource` implements the `ContextSource` seam, so the Planner and Response
receive bounded, authority-ranked knowledge with provenance through the same
`ContextService` boundary as the metric and schema registries. Entity resolution and the
metric registry are projections of the knowledge store, so there is one canonical
definition of a team's identity or a metric's meaning.

See [../development/shared-knowledge.md](../development/shared-knowledge.md) for the
developer view and [../adr/0019-shared-knowledge-base.md](../adr/0019-shared-knowledge-base.md)
for the decision record.
