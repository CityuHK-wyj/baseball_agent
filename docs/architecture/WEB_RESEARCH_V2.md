# Web Research v2 — Architecture Proposal

Status: **historical architecture proposal / accepted design reference, not the current implementation inventory.**
Architecture Baseline v1: parts of this design were subsequently implemented;
Goal-wide M3 enforcement/cache remain planned and active browser networking is
unavailable. Current symbols/ownership/limits are in
[Architecture Overview](ARCHITECTURE_OVERVIEW.md),
[Domain Concept Registry](DOMAIN_CONCEPT_REGISTRY.md) and
[Implementation Status](../engineering/IMPLEMENTATION_STATUS.md).
Dated observations and proposal details below retain their original scope.
Author: architecture design pass
Baseline: Phase 5B pragmatic acceptance
Reviewed HEAD: `7ad65b2fdc5e0b87355c122a23864ed547a172f3`
Date: 2026-09-27 (Asia/Shanghai)
Revision: 2 — architecture review feedback applied (Goal-level budget; deadline model;
responsibility boundary; fact vs document freshness; body/offset version contract; four
milestones and new metrics). No production code changed.

> This document is design only. No production code, tests, contracts or ADRs were changed.
> Every provider claim below was produced by a live probe from this environment on
> 2026-09-27 unless explicitly marked otherwise. Observed timings are single-run
> observations, not p50/p95 guarantees.

---

## 0. Scope, mission and non-goals

**Mission.** Substantially improve the Agent's ability to *discover and retrieve* high-value
MLB information, prioritizing research capability, answer completion and reasonable latency,
while preserving the accepted Phase 5B contracts and the single Runtime flow:

```text
User Message -> Semantic -> Goal/Requirements -> Planner -> Tool Execution
             -> Artifacts -> Judge -> Answer
```

**Explicit non-goals** (from the kickoff, restated as constraints on this design):

- No redesign of the accepted Phase 5B Judge.
- No second Evidence Verifier.
- No new Agent framework.
- No parallel orchestration graph.
- No relaxation of strict evidence or database contracts.
- No named-player or named-event shortcuts anywhere in production code.
- No immediate large provider expansion.

**Architecture questions answered in this document.** Section 12 contains a single
question→answer table; the full reasoning is in Sections 3–9.

---

## 1. Current Web Research: evidence-based limitations

This section is grounded in the actual code (`app/capabilities/web_research.py`,
`app/capabilities/web_search.py`), the Phase 5B reports, the web/source inventory
(`docs/audit/WEB_SOURCE_INVENTORY.md`) and the persisted latency diagnostics under
`.runtime/diagnostics/`.

### 1.1 What exists today

| Layer | Implementation |
|---|---|
| Planner-visible tool | one capability `web_research` (`accepts: SEARCH_QUERY`, `produces: WEB_EVIDENCE`) |
| Search backends | `EndpointSearchBackend` (unconfigured), `FeedSearchBackend` (4 publisher RSS feeds), `WikipediaSearchBackend`, `GoogleNewsRSSBackend` (discovery-only) |
| Fetch | one `httpx.get` per URL, SSRF guard, BeautifulSoup extraction, 20 000-char cap, `MIN_PAGE_TEXT=200` |
| Spans | `evidence_spans()` = sentences ≥25 chars sharing a query token, max 6, fallback = first 600 chars |
| Verification | `verify_span()` (ADR 0022/0025), deterministic gate + optional LLM refinement, budget `_LLM_VERIFY_BUDGET=6` |
| Budgets | `max_fetches=4`, outer `max_rounds≤5`, `max_needs=8`; no research-specific budget object and no Goal-level accounting across Planner rounds |
| Diagnostics | `LatencyTracker` stages `web:search`, `web:fetch`, `web:retrieve`, `web:verify`, `tool:web_research`, `goal_end_to_end` |

### 1.2 Limitation catalogue

**L1 — No real broad web search.**
The only general-index provider is `EndpointSearchBackend`, and `WEB_SEARCH_ENDPOINT` is
empty by default. `FeedSearchBackend` searches four recent publisher feeds by token overlap;
`GoogleNewsRSSBackend` produces JS-redirect links and is explicitly `DISCOVERY_ONLY_ENGINES`;
`WikipediaSearchBackend` returned HTTP 403 during the Phase 5B audit (it now answers — see
§4). Result: discovery is bounded by *whatever recent articles those four feeds happen to
carry*. Historical, long-tail, non-English, player-specific and follow-up retrieval is weak,
which is exactly the honest `LIMITED` outcome the 1994-strike test produced.

**L2 — Query planning is shallow.**
`_build_queries()` emits at most 3 queries composed from (subject join), (explicit query),
and (subjects + objective + target + search terms), then optionally one LLM call
(`_generate_queries`) *only when the Planner supplied no explicit query*. There is:

- no entity-alias expansion beyond what the interpreter/entity-resolution already emitted;
- no per-facet query construction (event vs metric vs interview vs fresh news);
- no historical date injection driven by a structured time scope (only an LLM instruction:
  "For historical questions include the relevant year");
- no explicit "original interview" / quotation targeting;
- no mapping from *missing mandatory propositions* to query facets.

**L3 — Candidate ranking ignores the requested factual relationship.**
`_search()` interleaves per-backend hit lists round-robin, then `_retrieve()` fetches the
first `max_fetches=4` non-discovery hits in that order. The only relevance signal is
`_mentions_subject()`, a subject-token overlap check on title+spans. A page whose *title*
contains the subject but whose body discusses something else can consume the entire fetch
budget before a better ranked page is reached. There is no relationship-aware scoring
(subject **and** metric/event **and** scope) and no cross-provider score normalization.
`FeedSearchBackend` does rank within one feed by token-fraction/longest-token, but that
signal is discarded when results are interleaved with other backends.

**L4 — Retrieval is fully sequential and slow.**
`_search()` loops `queries × backends` sequentially; `FeedSearchBackend.search()` itself
loops 4 feeds sequentially. With 3 queries and 6 backend objects that is up to
`3 × (4 feed fetches + 1 wiki + 1 google-news) = 18` sequential HTTP calls at a 15 s timeout
each, plus a second "alternate query" pass, plus up to 4 sequential page fetches. There are
no retries and no concurrency.

**L5 — Measured latency is already the dominant cost of a web goal.**
From 36 diagnostic records with a `tool:web_research` span (2026-09 baseline; the large
majority are live network runs, and the `min` column is pulled toward zero by a few
replayable fixture runs):

| Stage | p50 | p90 | max |
|---|---:|---:|---:|
| `web:search` | 42.4 s | 80.9 s | 214.1 s |
| `web:fetch` | 20.4 s | 41.9 s | 68.1 s |
| `web:verify` | 22.1 s | 49.9 s | 59.0 s |
| `tool:web_research` | 84.4 s | 158.4 s | 290.3 s |
| `semantic` | 2.9 s | 4.0 s | 4.6 s |
| `composer` | 2.4 s | 3.2 s | 4.1 s |

Search alone is the single largest stage. This confirms the problem is retrieval
engineering, not the semantic/judge/composer path.

**L6 — Failure classification and provider state are fragile.**
Every backend swallows exceptions and returns `[]`, recording only `last_error`.
`_search()` appends an error string when `last_error` is set, and `SEARCH_BACKEND_UNAVAILABLE`
requires `len(backend_errors) >= attempted` (call count, not distinct providers). A provider
that 403s is therefore often reported as a plain `NO_RESULTS`, indistinguishable from
"genuinely no news". `FeedSearchBackend.last_error` is shared mutable state overwritten per
feed, so the reported failing provider is not reliable.

**L7 — Fetch has no fallback ladder and no cache.**
Blocked/JS-only/paywalled/empty pages are terminal per URL. There is no retry with backoff,
no alternate-URL selection for the same document, no main-content/readability extractor, no
`articleBody`/JSON-LD fallback, no archive/reader path, and no HTTP cache. Repeated
same-subject research refetches everything.

**L8 — Freshness is not modelled and historical capability is mislabelled.**
`publication_date` is guessed from four regex patterns over the first 20 000 bytes; RSS
`<pubDate>` is discarded by `FeedSearchBackend`. There is no "published within N days"
filter and no as-of semantics. `historical_capable` is a keyword heuristic over the query
strings/objective (`(19|20)\d{2}` or words like "history"), not a real statement about the
retrieved providers — so the artifact can claim historical capability it does not have.

**L9 — Span selection is lexical and can miss the answer.**
`evidence_spans()` selects the first 6 sentences sharing a raw query token, else the first
600 chars. It has no notion of the requested metric/relationship, no passage windowing for
coreference across sentences, and no date/entity-aware passage scoring. Relevant facts in a
long article are often outside the selected spans, producing `INSUFFICIENT_EVIDENCE` even
when the page did contain the answer.

**L10 — No structured-source interoperability for research.**
`dynamic_fact` implements only StatsAPI `currentTeam`; the audit found it broken and it was
later repaired, but there is still no path for schedule, season stats, standings,
transactions, injuries, Retrosheet/Lahman history or Savant CSVs into research. The single
`web_research` tool never consults a structured provider, and the Planner has no bounded way
to reach one.

**L11 — Caching and cross-turn reuse are absent.**
Artifacts live for one turn. There is no retrieval cache, no per-URL/`content_hash` reuse,
and no way to avoid re-fetching an unchanged historical article on the next question.

**L12 — Only one research budget knob, and no Goal-level accounting.**
`max_fetches` bounds pages per request, but nothing bounds search rounds, total provider
calls, parallel fetches, or LLM calls *for research as a whole*. The outer `max_rounds`
bounds Planner rounds, not research work inside a round; because a repeated `web_research`
call would receive a fresh quota, a multi-round Goal could multiply latency and cost (§7).

---

## 2. Design principles

1. **One Planner-visible tool, many internal sources.** Planner keeps seeing exactly one
   `web_research` capability. All provider routing, ranking, fetching and budget live behind
   its `execute()` boundary. This prevents "an excessive number of tools" from leaking to
   the Planner and keeps the Need graph unchanged.
2. **Reuse the accepted contract surface.** `SearchBackend`/`SearchHit`/`FetchedPage`
   (`web_search.py`) are extended, not replaced. `RetrievedDocument`, `EvidenceSpan`,
   `EvidenceFinding`, `Artifact`, `Export`, `Scope`, `ToolRequest`/`ToolOutcome` are
   unchanged. `verify_span`, the Judge, consistency and diagnostics semantics are untouched.
3. **Deterministic routing and ranking, bounded LLM use.** Routing, candidate scoring,
   dedup, caching and freshness are deterministic. The LLM is consulted only for query
   phrasing and semantic passage verification, each under an explicit budget.
4. **Fail typed and honest.** A missing page, a blocked provider or an exhausted budget
   yields a typed outcome and a `PARTIAL`/`LIMITED` goal, never fabricated support.
5. **Measure before optimising.** Every phase adds observability first; performance claims
   are only made after the benchmark harness (§10) runs.

---

## 3. Proposed architecture

### 3.1 Diagram

```text
                            Planner (unchanged)
                                   |
                     one capability |  accepts SEARCH_QUERY -> produces WEB_EVIDENCE
                                   v
+--------------------------------------------------------------------------------------+
|  WebResearchTool.execute(request, ctx)                      (app/capabilities/)     |
|                                                                                      |
|  (1) ResearchContextBuilder                                                          |
|        objective, subjects/aliases, propositions (mandatory prose), scope,           |
|        freshness class, entity refs, missing-answer facets                           |
|                                   |                                                  |
|  (2) GoalResearchBudget (shared per Goal) + ResearchBudget (per call)  (§7)          |
|                                   v                                                  |
|  (3) QueryPlanner (1 LLM call max, deterministic fallback)                            |
|        variants tagged: ENTITY | METRIC | EVENT | INTERVIEW | FRESHNESS               |
|                                   |                                                  |
|  (4) ResearchRouter (deterministic; §3.3)                                            |
|        proposition.kind + scope + freshness  ->  ordered SourcePlan                  |
|                                   |                                                  |
|  (5) SourceExecution (bounded parallel; §3.4)                                        |
|        +----------------+  +------------------+  +-----------------+  +------------+ |
|        | BROAD_SEARCH   |  | PUBLISHER_FEEDS  |  | ENCYCLOPEDIA    |  | STRUCTURED | |
|        | provider iface |  | (reuse existing) |  | Wikipedia live  |  | StatsAPI.. | |
|        +----------------+  +------------------+  +-----------------+  +------------+ |
|        per-provider timeout, retry, rate limit, typed error, latency span            |
|                                   |                                                  |
|                        SearchHit[] + StructuredRecord[]                              |
|                                   v                                                  |
|  (6) CandidateRanker (deterministic; §5)                                             |
|        relationship-aware score(subject, metric/event, scope, trust, freshness)      |
|                                   |                                                  |
|  (7) FetchPipeline (bounded parallel; §6)                                            |
|        HTTP cache -> fetch -> extraction ladder -> fallback URL -> typed failure     |
|                                   |                                                  |
|  (8) SpanSelector (proposition-aware; §5.3)                                          |
|                                   v                                                  |
|  (9) EvidenceVerifier = existing verify_span()  (ADR 0022/0025, UNCHANGED)           |
|                                   |                                                  |
|        WEB_EVIDENCE Artifact  (documents, spans, findings, coverage, provenance,     |
|                                provider + budget + cache metadata)                   |
+--------------------------------------------------------------------------------------+
                                   |
              existing Judge -> AnswerProjection -> Composer -> consistency
```

### 3.2 Layer placement

New package `app/web/` (all internal, none registered with the Planner):

```text
app/web/
  sources/
    base.py           # SourceDescriptor + SearchProvider/SourceAdapter protocols (extends SearchBackend)
    broad_search.py   # configurable generic search providers (Brave/Bing/GoogleCSE/Tavily/Exa/SerpAPI/Mojeek/DDG)
    publisher.py      # wraps existing FeedSearchBackend
    encyclopedia.py   # Wikipedia search + page (search backend already exists)
    structured.py     # official baseball structured adapters (small, opt-in)
  router.py           # ResearchRouter
  query_plan.py       # ResearchQueryPlan + QueryPlanner
  ranking.py          # CandidateRanker
  fetch.py            # FetchPipeline, extraction ladder, retry, cache integration
  cache.py            # layered retrieval cache + freshness classes
  budget.py           # ResearchBudget + ResearchBudgetTracker
```

`app/capabilities/web_search.py` keeps its public names (`SearchBackend`, `SearchHit`,
`FetchedPage`, `WebSearchConfig`, `build_backends`, `fetch_page`, `evidence_spans`,
`assert_safe_url`) so existing tests and monkeypatches continue to work; the new code
composes them.

### 3.3 ResearchRouter (Q3)

Routing is a deterministic function, not an LLM decision and not a new Planner tool:

```text
route(propositions, scope, freshness, available_sources) -> SourcePlan[]
```

| Question shape (proposition kind / signal) | Routed work | Primary research sources | Notes |
|---|---|---|---|
| `VALUE` / statistical over a local-DB scope | `local_analytics` (+ `python_compute` for derived measures) — **not** `web_research` | — | The router records a local-data path; `web_research` is never asked to compute aggregates |
| `VALUE` requiring an authoritative external **record** (award, contract, historical split) | `web_research` retrieving one official record as evidence | `STRUCTURED` record lookup, `ENCYCLOPEDIA` | Record retrieval only; any arithmetic over records is `python_compute` (§3.5) |
| `EXPLANATION` / historical (`time_scope` year ≤ current) | narrative research | `ENCYCLOPEDIA`, `REFERENCE`, `BROAD_SEARCH`, `PUBLISHER_FEEDS` | Historical is `IMMUTABLE` fact freshness |
| `EXPLANATION` / current (`high_risk`: injury/roster/trade/current) | current-state research | `PUBLISHER_FEEDS`, `STRUCTURED` roster/transaction records | `VOLATILE` fact freshness; must carry `as_of` |
| Operational relation fact (current team / roster / status) | `dynamic_fact` — not `web_research` | — | Existing capability and its `as_of` contract |
| `FACT`/`EVENT` with interview/quote intent | narrative research | `BROAD_SEARCH` (interview token), `PUBLISHER_FEEDS`, `ENCYCLOPEDIA` | Query facet `INTERVIEW` |
| `COMPARISON` | `local_analytics` + `python_compute` (+ narrative corroboration) | `web_research` for explanation only | Never merge scopes; Judge owns completion |
| Definition/`shared_knowledge` | existing `shared_knowledge` tool | `ENCYCLOPEDIA` | Router defers to the existing knowledge path |

Routing never inspects player or event names. It reads only `AnswerProposition.kind`,
`time_scope`, `population`, the existing `is_high_risk()`/freshness markers, and the
registered source capabilities.

### 3.4 SourceExecution (Q9)

- Runs a bounded `ThreadPoolExecutor` (or `asyncio`) over the `SourcePlan`.
- Default parallelism `max_parallel_queries = 4`; per-source semaphores enforce politeness
  (e.g. 1 concurrent request per host, configurable min interval).
- Each provider call is wrapped in timeout + bounded retry (jittered backoff) for transient
  failures; permanent failures (401/403/404, policy) are not retried.
- Every call records a `web:source:<id>` latency span and a typed `SourceOutcome`
  (`OK | EMPTY | RATE_LIMITED | AUTH_REQUIRED | BLOCKED | TIMEOUT | ERROR`).
- Determinism: results are merged in a fixed `(source_rank, score, url)` order, never in
  completion order, so a concurrent run is reproducible.
- Structured source calls with no canonical id use name search + resolution; calls requiring
  a canonical id still wait on the existing Binder/`entity_resolution` dependency — the
  source adapter never bypasses the Binder.

### 3.5 Analytical vs research responsibility boundary

`web_research` is a **retrieval and evidence** capability. It never becomes a statistics
engine, and official structured sources are **retrievers of authoritative records**, not
analytical executors.

Two distinct compute owners must never be conflated:

- **`local_analytics` owns raw-data aggregation.** Any aggregate/group/rank/delta/rate over
  the local PostgreSQL/Parquet *source data* is compiled through Safe IR, measure
  verification, scope checks and `EnumerationContract`. This is the only place raw rows are
  turned into metrics.
- **`python_compute` owns bounded temporary computation over already-accepted Artifacts.**
  It combines, joins, differences or transforms the *exports of accepted artifacts*; it does
  not read source tables and must not become a general compute engine.

Cross-record computation therefore **leaves Web Research entirely**: a structured lookup or
narrative retrieval may produce records, but it never computes the answer.

| Concern | Owner | Must not |
|---|---|---|
| Aggregation over raw DB/Parquet source data (aggregate, group, rank, delta, rate) | `local_analytics` (Safe IR + measure verification + enumeration) | `python_compute` must not read source tables; `web_research` must not compile IR or aggregate |
| Bounded temporary computation over accepted Artifacts (combine, join, difference, transform) | `python_compute` (`EnumerationContract`/`GlobalOperation` gates) | must not become the default path for all computation; must not touch raw sources |
| Current operational relation facts (team membership, roster/status) | `dynamic_fact` (existing, `as_of`) | not a stats engine; not duplicated inside `web_research` |
| Authoritative external **record** lookup (single player-season line, award, contract, schedule fact) | a structured source adapter **inside** `web_research`, used only when local data cannot serve the obligation | no aggregation, no derived measures, no cross-entity ranking; records are retrieved as evidence |
| Narrative / historical / explanatory retrieval | `web_research` (encyclopedia, reference, feeds, broad search) | no numeric computation |

Rules:

1. A structured adapter may only **fetch and render** records (§6.2). It returns a canonical
   body whose spans are admissible `DOCUMENT_BODY` evidence; the existing verifier decides
   support unchanged.
2. If an obligation needs aggregation over raw data, the Planner uses `local_analytics`; if it
   needs bounded arithmetic over accepted artifacts, the Planner uses `python_compute` under
   the Phase 5A enumeration/scope contracts. Neither is delegated to `web_research`.
3. **Do not force all computation into Python.** Raw-data aggregation remains in
   `local_analytics` (SQL is the correct engine for population scans); `python_compute` is the
   exception for cross-artifact work, not the rule. `web_research` fails closed
   (`UNSUPPORTED_OPERATION`/`INSUFFICIENT_ENUMERATION`) rather than computing.
4. The router does **not** move a local statistical question into `web_research`; it records
   the local path, and the Planner keeps using `local_analytics`/`dynamic_fact`.
5. No new Planner-visible capability is introduced: structured lookup remains internal to
   `web_research`.

---

## 4. Provider comparison (live-probed 2026-09-27)

Probes were executed from this repository's environment with user agent
`BaseballAgent/0.1 (research; mailto:baseball-agent@example.com)`. HTTP status and body size
are reported. An endpoint returning a website is **not** treated as available: only a
successful, parseable response counts.

### 4.1 Broad / general web search

| Provider | Endpoint | Auth | Live result (no key) | Coverage | Rate limit (documented) | Notes |
|---|---|---|---|---|---|---|
| Brave Search API | `api.search.brave.com/res/v1/web/search` | API key | **422** (key required) | independent web index | plan-dependent | good latency; paid/free tier |
| Bing Web Search | `api.bing.microsoft.com/v7.0/search` | API key | **401** | large general index | plan-dependent | Azure subscription |
| Google Programmable Search | `googleapis.com/customsearch/v1` | API key + `cx` | **403** | Google-class index | 100/day free | requires engine config |
| Tavily | `api.tavily.com/search` | API key | **401** | LLM-oriented search+extract | plan-dependent | returns extracted content |
| Exa | `api.exa.ai/search` | API key | **404** (path/auth) | neural/keyword | plan-dependent | embeddings-style |
| SerpAPI | `serpapi.com/search` | API key | **401** | SERP scrape | plan-dependent | paid, proxy-wrapped |
| Mojeek | `mojeek.com/search` | none | **200** (5.5 KB HTML) | independent index | ToS/politeness; scrape | parseable HTML |
| Marginalia | `search.marginalia.nu/search` | none | **200** (37 KB) | non-commercial index | polite use | niche, not MLB-complete |
| DuckDuckGo Instant Answer | `api.duckduckgo.com` | none | **200** JSON (18 related topics) | entity summaries only | unknown | not a full web result set |
| DuckDuckGo HTML / Lite | `html./lite.duckduckgo.com` | none | **202** challenge page, 0 result links | — | anti-bot | not reliable programmatically |
| Google News RSS | `news.google.com/rss/search` | none | **200** (130 KB) | news aggregation | unknown | already discovery-only |

**Conclusion.** No broad-search API is usable out of the box without a key, and the
unauthenticated HTML engines are bot-hostile. The design must therefore: (a) keep a
provider-neutral `SearchProvider` interface so a keyed provider can be configured without
code changes, (b) default to the sources that actually work today (publisher feeds +
Wikipedia + Google News discovery), and (c) **not hardcode a paid preference** — the choice
is configuration, and the benchmark (§10) decides if a key is worth buying.

### 4.2 Official / structured baseball sources

| Source | Endpoint probed | Auth | Live result | Usable data | Notes |
|---|---|---|---|---|---|
| MLB StatsAPI | `/api/v1/people/660271?hydrate=currentTeam` | none | **200** (1.2 KB JSON) | identity, current team | works |
| MLB StatsAPI | `/api/v1/teams?sportId=1` | none | **200** (23 KB JSON) | teams | works |
| MLB StatsAPI | `/api/v1/seasons?sportId=1` | none | **200** (0.8 KB) | seasons | works |
| MLB StatsAPI | `/api/v1/schedule?...date=...`, `/transactions`, `/standings`, `/stats`, hydrated `/people` | none | **406 Not Acceptable** | — | environment egress returns 406 for these query shapes; **must not be assumed available** |
| MLB.com news RSS | `mlb.com/feeds/news/rss.xml` | none | **403** | — | was live 2026-09-21; now blocked. Source fragility |
| FanGraphs blog feed | `blogs.fangraphs.com/feed/` | none | **200** (221 KB) | recent analysis | live |
| CBS Sports MLB feed | `cbssports.com/rss/headlines/mlb/` | none | **200** (36 KB) | recent news | live |
| Yahoo Sports MLB feed | `sports.yahoo.com/mlb/rss/` | none | **200** (333 KB) | recent news | live |
| Baseball Savant CSV | `baseballsavant.mlb.com/statcast_search/csv` | none | **200** (663 KB) | Statcast exports | live; heavy |
| Retrosheet | `retrosheet.org/gamelogs/gl2023.zip` | none | **200** (473 KB) | historical game logs | live; download/parse |
| Retrosheet site | `retrosheet.org/` | none | **200** | schedules/box scores | live |
| Baseball Reference | `baseball-reference.com/players/o/ohtansh01.shtml` | none | **200** (823 KB HTML) | history/stats narrative | ToS limits scraping; low rate |
| SABR | `sabr.org/` | none | **200** | historical research | live |
| Seamheads | `seamheads.com/` | none | **200** | historical/negro leagues | live; 5.4 s |

**Conclusion.** Official structured coverage is real but **partial and
environment-sensitive**. StatsAPI must be adopted as an *optional capability* whose absence
degrades gracefully — never as an assumed source. Historical narrative has strong free
coverage (Wikipedia, Retrosheet, BRef, SABR), which directly addresses the 1994-strike
gap.

### 4.3 Encyclopedia / reference

| Source | Endpoint probed | Live result | Notes |
|---|---|---|---|
| Wikipedia Action API | `en.wikipedia.org/w/api.php?list=search` | **200** (892 B JSON) | now works with the project UA; old audit's 403 no longer reproduces |
| Wikipedia REST summary | `en.wikipedia.org/api/rest_v1/page/summary/...` | **200** (2.8 KB) | clean entity summary |
| Wikipedia page HTML | `en.wikipedia.org/wiki/Shohei_Ohtani` | **200** (1.94 MB) | fetch + extract works |
| Wikidata | `wikidata.org/w/api.php?action=wbsearchentities` | **200** (1.0 KB) | entity/alias graph for alias expansion |
| Wikimedia pageviews | `wikimedia.org/api/rest_v1/metrics/...` | **200** (791 B) | popularity signal, optional |

**Wikipedia is the single most important correction to the Phase 5B baseline.** The old
inventory classified it "NOT LIVE (403)". It is live today and is the natural home for
historical and explanatory research. Its capability must be probed at runtime, not
hardcoded from a stale audit.

---

## 5. Query planning and candidate ranking (Q4, Q5)

### 5.1 Minimal internal contract: `ResearchQueryPlan`

This is an **internal** dataclass inside `app/web/`, not a Planner tool and not a new
Artifact kind.

```python
@dataclass(frozen=True)
class QueryVariant:
    text: str
    intent: str            # ENTITY | METRIC | EVENT | INTERVIEW | FRESHNESS | GENERAL
    target_proposition_ids: tuple[str, ...] = ()
    time_scope: dict = field(default_factory=dict)
    source_hint: tuple[str, ...] = ()   # e.g. ("encyclopedia","reference")

@dataclass(frozen=True)
class ResearchQueryPlan:
    objective: str
    subjects: tuple[str, ...]
    aliases: tuple[str, ...]
    variants: tuple[QueryVariant, ...]
    freshness: str = "MIXED"          # IMMUTABLE | VOLATILE | MIXED
    generated_by: str = "deterministic"  # deterministic | llm
    facets_missing: tuple[str, ...] = ()  # mandatory propositions with no facet yet
```

### 5.2 Query generation

Deterministic base plan (always produced):

1. **ENTITY facet** — each subject plus each known alias (from `entity_references`,
   `ENTITY_MAPPING`, and the local player directory / Wikidata aliases when available).
2. **METRIC facet** — subject + canonical metric label/aliases for each mandatory `VALUE`
   proposition (from `measures.py` vocabulary; no free invention).
3. **EVENT facet** — subject + event terms + normalized historical date/population from
   `AnswerProposition.time_scope` / `population` (e.g. `1994`, `postseason`).
4. **INTERVIEW facet** — only when the interpreter/Planner signals an interview/quote/
   first-person objective: subject + (`interview`, `said`, `quote`, `scouting report`,
   `one-on-one`). This is general, not player-specific.
5. **FRESHNESS facet** — for `VOLATILE` questions: subject + `injury|roster|trade|status`
   plus current-year token, marked `source_hint=("publisher_feeds","broad_search")`.
6. **MISSING facet** — for every mandatory prose proposition with no query yet, one variant
   built from its `required_information` tokens; recorded in `facets_missing` so coverage
   gaps are auditable.

Optional single LLM call (`QueryPlanner.refine`) may add at most 2 phrasings and MUST
preserve the subject prefix; it is skipped when a deterministic plan already covers all
mandatory propositions, or when no LLM is configured, or when the budget forbids it. This
is a refinement of the existing `_generate_queries`, not a replacement agent.

### 5.3 Candidate ranking (relationship-aware)

`CandidateRanker.score(hit, variant, proposition, source)` returns a deterministic float
from lexical signals only (no embeddings, per project non-goal):

```text
score =      w_subject  * coverage(subject/aliases, title+snippet)
           + w_required * coverage(required_information + metric/event terms, title+snippet)
           + w_scope    * coverage(year/population tokens, title+snippet)
           + w_intent   * variant_intent_match(hit, variant)
           + w_trust    * trust_weight(source)          # higher = MORE trusted
           + w_fresh    * freshness_match(source, question_freshness)
           - w_penalty  * discovery_only(hit)
           - w_penalty  * duplicate_domain
```

- `coverage` uses the existing tokenizer/inflection rules (`_mentions`) so behavior is
  consistent with the verifier.
- **Authority direction is fixed.** `trust_weight` is a float where a *larger* value means a
  *more* trusted source: `1.00` official, `0.85` encyclopedia/reference, `0.55` publisher
  news, `0.25` open web/aggregator, `0.00` discovery-only. The earlier `trust_tier` integer
  (`1=official … 4=open web`) was dangerous because it entered an additive bonus and would
  have rewarded lower-authority sources with a larger score. The descriptor now exposes
  `trust_weight`, and a unit test asserts that an official source outranks an open-web
  source for an identical lexical match. `freshness_match` follows the same convention
  (larger = better match).
- Weights are named constants in one module, covered by tests, and tunable by config.
- The ranker produces a stable order (ties broken by URL) so concurrent search is
  reproducible.
- Span selection (`SpanSelector`) is upgraded from "first 6 token-sharing sentences" to a
  scored passage selector that prefers sentences containing the **required information**
  and the **requested scope**, then widens to adjacent sentences for coreference. Offsets
  and the `body[start:end] == text` invariant are preserved exactly (Phase 5B §12.7).

**This is the core answer to Q5:** the requested factual relationship, not subject keyword
presence, drives both which documents are fetched and which passages are verified.

---

## 6. Fetch, extraction and fallback (Q6)

The proposed module name `app/web/fetch.py` is not an existing implementation module.
The current ladder lives in `app/web/fetch_ladder.py`; the full target below would add
cache/retry behavior beyond the present implementation. Proposed bounded ladder:

1. **Cache lookup** (§9) — a fresh cache entry skips the network entirely.
2. **Primary fetch** — `httpx.get` with the SSRF guard (`assert_safe_url`) and the existing
   `FetchedPage` contract; add redirect-count/size/time bounds and conditional headers
   (`If-None-Match`, `If-Modified-Since`).
3. **Transient retry** — bounded retries with jittered backoff for timeouts, connection
   errors and 429/5xx; honor `Retry-After`. Permanent 401/403/404/410 are not retried.
4. **Extraction ladder** — (a) existing BeautifulSoup extractor; (b) main-content/
   readability heuristic (largest text block) to strip boilerplate; (c) `application/ld+json`
   `articleBody` / OpenGraph `og:description`; (d) JSON/XML passthrough for feeds/APIs. Each
   step records which extractor produced the text.
5. **Alternate-URL fallback** — the ranker keeps multiple provider URLs for the same logical
   document; if URL A is `BLOCKED`/`NO_CONTENT`, URL B is attempted (never fabricated).
6. **Typed terminal failure** — `BLOCKED | EMPTY | PAYWALL | JS_REQUIRED | TIMEOUT |
   POLICY_BLOCKED | FETCH_FAILED`, recorded on the `RetrievedDocument` so the failure is
   auditable (existing behavior) and surfaced to the router for provider fallback.
7. **Provider fallback** — if an entire source fails (`RATE_LIMITED`/`AUTH_REQUIRED`/
   `BLOCKED`), the router promotes the next source in the `SourcePlan`; a provider outage
   never silently becomes `NO_RESULTS` (fixes L6).

`MIN_PAGE_TEXT`, SSRF policy, offset replay and the `body_text` contract are unchanged.

### 6.1 Raw response → extracted body → evidence span: version and offset contract

Three versioned artifacts, each with its own identity. The offset contract applies **only
within one extracted-body version**.

1. **Raw response** — `raw_bytes`, `raw_content_hash = SHA-256(raw_bytes)`, `final_url`,
   `content_type`, `encoding`. This is the stable network identity and is stored on the
   existing `RetrievedDocument.content_hash`.
2. **Extracted document body** — `body_text = extract(raw_bytes, extractor,
   extractor_version)`, deterministic and replayable, with `body_hash = SHA-256(body_text)`.
   `RetrievedDocument.body_text` is exactly this. Changing the extractor or its
   normalization rules is a **new body version**: `body_hash` changes and old spans no
   longer apply.
3. **Evidence span** — `span.text`, `start`, `end` are character offsets into the *same
   version* of `body_text`, enforcing `body_text[start:end] == span.text`, with
   `span_hash = SHA-256(span.text)` (existing Phase 5B §12.7). Offsets are **never** offsets
   into raw bytes.

Contract rules:

- A persisted `EvidenceSpan` is valid only against the `(document_id, body_hash,
  extractor_version)` it was created from. The Judge's replay (`finding.span_id -> span ->
  document -> body_text[start:end]`) is unchanged because `body_text` is the same object.
- Cache and replay reuse must match `body_hash` and `extractor_version`; a mismatch
  re-extracts and produces new spans rather than reusing stale offsets (§9).
- `raw_content_hash` (existing `content_hash`) is kept for provenance and change detection;
  `body_hash` and `extractor_version` are recorded as additive metadata on the cache entry
  and the artifact. No evidence contract is changed.
- Structured sources follow the same rule by rendering their response into a deterministic
  canonical body (§3.5), so `source_type=DOCUMENT_BODY` and the existing admissibility rule
  (ADR 0022/0025) hold without modification.

### 6.2 Official structured records: raw fields, identity, source, as_of, conversion version

When an official structured source (for example an MLB StatsAPI record) is used as research
evidence, the adapter preserves the record's provenance and never lets normalized text
impersonate something it is not. Every structured record carries a `StructuredRecord`
envelope (internal to `app/web/`; additive artifact metadata, not a new evidence contract):

```python
@dataclass(frozen=True)
class StructuredRecord:
    record_id: str            # stable identity: source + endpoint + key fields
                              # (e.g. player id + season + stat group)
    source_id: str            # e.g. "statsapi"
    source_url: str           # canonical request URL (credentials never included)
    as_of: str                # the point in time the record asserts
    conversion_version: str   # renderer version that produced body_text, e.g. "statsapi-record-v1"
    raw_fields: dict          # original provider fields, verbatim; no invented values
```

Rules:

1. **Raw fields are preserved verbatim.** The adapter never rewrites, estimates or drops a
   provider field the record carries. Only credential redaction and known-size/known-shape
   hygiene apply, and only at the diagnostic boundary.
2. **Record identity is explicit.** `record_id` is derived deterministically from source +
   endpoint + key fields, so the same record always maps to the same identity and can be
   replayed.
3. **`source` and `as_of` are explicit.** The canonical API URL is the document URL,
   `source_id` is the provider, and `as_of` is the provider's effective date (or the fetch
   time for inherently-current records).
4. **The normalized `body_text` is a labeled rendering — not a news article and not the
   unconverted official original.** The canonical body begins with a deterministic provenance
   header naming `source_id`, `record_id`, `as_of`, `source_url` and `conversion_version`. A
   `conversion_version` change produces a new body version (`body_hash`, §6.1).
5. `RetrievedDocument.source_quality` is set to `official_structured_record:<source_id>`
   (never `news`/`publisher_feed_or_wikipedia`), `EvidenceSpan.location` is
   `structured:<record_id>`, and the raw record plus its `StructuredRecord` metadata is stored
   in the Web Artifact under `structured_records`.
6. **Admissibility is unchanged.** The rendered body is a fetched `DOCUMENT_BODY` with
   `retrieval_status=OK` and exact offsets, so `verify_span` (ADR 0022/0025) still decides
   support. The provenance fields exist only to prevent the rendering from being *presented*
   as an unconverted official original or as journalism; they add no new evidence authority.

---

## 7. Research Budget (Q8)

Two budget scopes. The Goal-level budget is the anti-double-spend authority; the per-call
budget cannot exceed what remains at the Goal level.

### 7.1 Goal-level shared budget (`GoalResearchBudget`)

**Target ownership (not current enforcement).** The `GoalResearchBudget` would be held
by the execution context / Goal State (the
per-Goal `ExecutionContext` created by the Engine), keyed by `goal_id`, and created exactly
once when the Goal is created. `WebResearchTool` receives a handle and may only **consume**
from it: it cannot create, re-create, raise or reset it. A Planner **replan** — repairing a
terminal Need, adding a second `web_research` Need, or repeating a research round — reuses
the same budget instance, so a multi-round Goal can never recover spent quota. There is no
per-tool-call copy that could reset on replan.

| Field | Default | Meaning |
|---|---:|---|
| `goal_provider_calls` | 24 | total search/structured provider calls for the whole Goal |
| `goal_documents_fetched` | 10 | total page fetches for the whole Goal |
| `goal_llm_query_calls` | 2 | total query-planning LLM calls |
| `goal_llm_verify_calls` | 12 | total verification LLM calls |
| `goal_search_rounds` | 3 | total router rounds |
| `goal_soft_deadline_ms` | 30 000 | shared-clock point for the Goal |
| `goal_hard_deadline_ms` | 45 000 | hard stop for all research in the Goal |

### 7.2 Per-call budget (`ResearchBudget`), clamped to the remainder

One `ResearchBudget` is created per `WebResearchTool.execute` and threaded through every
layer. Its effective values are always clamped to the remaining Goal budget:

```text
effective = min(tool_default, goal_remaining)
```

| Field | Proposed default | Meaning |
|---|---:|---|
| `max_search_rounds` | 2 | provider-fallback rounds in this call |
| `max_queries_per_round` | 4 | query variants executed per round |
| `max_provider_calls` | 8 | provider calls in this call |
| `max_documents_fetched` | 4 | page fetches in this call |
| `max_parallel_queries` | 4 | concurrent source queries |
| `max_parallel_fetches` | 4 | concurrent page fetches |
| `max_llm_query_calls` | 1 | query-planning LLM calls |
| `max_llm_verify_calls` | 6 | verification LLM calls (unchanged from `_LLM_VERIFY_BUDGET`) |
| `provider_timeout_ms` | 6 000 | one search/structured HTTP call |
| `fetch_timeout_ms` | 10 000 | one page HTTP call |
| `soft_deadline_ms` | 30 000 | stop launching new work; drain in-flight |
| `hard_deadline_ms` | 45 000 | cancel in-flight; return verified partial |
| `max_bytes_per_doc` | 2 000 000 | unchanged |

### 7.3 Concurrency counting and cancellation settlement

- Counters are reserved **before** a concurrent task is launched:
  `try_reserve(kind, n) -> granted`. A task may receive fewer than requested, or zero.
- A reserved unit is charged only when its request actually starts. If the executor rejects
  or cancels a task before its HTTP request begins, `settle(kind, granted, used=0)` releases
  the reservation.
- After a request starts, `settle(kind, granted, used=completed)` records the actual number
  of requests made. Retries count as provider calls (they consume quota and latency).
- On hard-deadline cancellation, settlement runs in a `finally` block for every future:
  reserved-but-unstarted calls are released; started calls are counted as used (the server
  may still complete them).
- Counters never decrease below the number of calls that actually started; only reservations
  are released. The tracker is one lock-protected object, so concurrent increments are
  atomic and the Goal total is exact.

### 7.4 Deadline model: provider timeout, soft deadline, hard deadline

Three distinct clocks, not one:

1. **Provider timeout** (`provider_timeout_ms` / `fetch_timeout_ms`) — the maximum for a
   **single** network call. Exceeded → typed `TIMEOUT`, one bounded retry if budget allows,
   then the source/candidate fails while other work continues.
2. **Soft deadline** (`soft_deadline_ms`) — a shared-clock point at which the pipeline
   **stops launching new work** (provider calls, fetches, LLM verifications) but lets
   already in-flight work finish inside a grace window. It is a graceful stop, not an error.
3. **Hard deadline** (`hard_deadline_ms`) — a shared-clock point at which in-flight work is
   **cancelled** and the tool finalizes with whatever is already verified. It must be
   strictly below the latency target it protects.

The Goal-level `goal_soft_deadline_ms`/`goal_hard_deadline_ms` bound the accumulated research
across Planner rounds; the per-call deadlines are `min(tool_default, goal_remaining)` so a
late round cannot extend the Goal clock.

### 7.5 Returning verified partial results

On deadline or counter exhaustion the tool never discards verified work and never guesses:

- Documents already fetched stay in the artifact with their real provenance.
- `verify_span` runs on every span already extracted; only spans whose fetch/offset contract
  holds are admissible (§6.1).
- Findings already computed are retained; unverified candidates are omitted.
- The artifact is `status=PARTIAL`; the outcome is `BUDGET_EXCEEDED` (`retryable=False` when
  the **Goal** budget is exhausted, `retryable=True` for a per-call soft stop while Goal
  budget remains); `budget_report` records which limit fired.
- The Judge receives the same artifact/evidence shape and decides coverage exactly as today;
  a partial artifact can still satisfy some mandatory propositions and yield `LIMITED`,
  never a fabricated `COMPLETE`.

### 7.6 Consistency with the latency objectives

Budgets and latency targets are derived from the same numbers:

- `provider_timeout_ms=6 s`, `fetch_timeout_ms=10 s` and `max_parallel_*=4` bound a single
  call's worst case.
- `soft_deadline_ms=30 s` sits **above** the `tool:web_research` p50 objective (≤25 s), so
  normal runs are never deadline-limited; `hard_deadline_ms=45 s` sits **below** the p95
  objective (≤50 s), so the tail degrades to a verified partial result instead of overrunning.
- `goal_soft_deadline_ms=30 s`/`goal_hard_deadline_ms=45 s` bound accumulated research across
  rounds, keeping `goal_end_to_end` p95 (≤65 s) reachable.
- These are **derived objectives**, not measured facts. The §10 harness validates them; the
  defaults move only with measured evidence (a budget that cannot meet the p95 objective is a
  reported finding, not a hidden overrun).

### 7.7 In-flight request semantics

When a call is in flight while a deadline or cancellation fires, the behavior is exact:

**Actual timeout.** Every provider call carries `provider_timeout_ms`/`fetch_timeout_ms` as
its own wall-clock bound. A call that exceeds it is aborted, recorded as typed `TIMEOUT`, and
counted as used. The shared soft/hard deadlines are evaluated independently of the per-call
timeout: a call whose own timeout would extend past the hard deadline is not launched after
the soft deadline, and is cancelled at the hard deadline.

**Cancellation.** At the hard deadline the coordinator cancels outstanding futures
(`Future.cancel()` / a cooperative cancel flag for the HTTP client). Already-completed futures
are left untouched. Results that have not completed by the finalization point are **not
admitted**, even if they complete a moment later, so the artifact is a snapshot of the
verified state and the run is reproducible.

**Result admission.** Only results that (a) completed before the finalization point and (b)
pass the existing admissibility/offset contract (§6.1) are admitted. A late or cancelled
result is dropped, never merged retroactively. Documents already fetched remain in the
artifact as provenance even when no span from them is admitted. No partial HTTP body is ever
treated as a document.

**Budget settlement.** Settlement runs in a `finally` for every future: reserved-but-unstarted
calls are released; started calls are counted as used whether they succeeded, failed, timed
out or were cancelled. Counters never fall below the number of calls that actually started.
The reservation/settlement invariant is the same as §7.3, applied across the whole Goal.

---

## 8. Concurrency and dependencies (Q9)

| Operation | Concurrent? | Must wait for |
|---|---|---|
| Query planning | no (one bounded call) | Goal propositions (already built) |
| Source queries across providers | **yes** (pool ≤ `max_parallel_queries`) | query plan |
| Structured calls needing canonical id | no | existing `entity_resolution` Binder dependency |
| Structured calls accepting a name | yes | query plan |
| Page fetches | **yes** (pool ≤ `max_parallel_fetches`) | ranking |
| Deterministic span verification | **yes** | fetched documents |
| LLM span verification | bounded pool (default 1–2) or sequential | deterministic gate passed |
| Judge / Composer / consistency | no | all artifacts |
| Cross-Goal work | never | one Runtime flow |

Concurrency is strictly **inside** `web_research.execute`. The Runtime's `_execute_pending`
loop remains serial and untouched; the accepted single flow is preserved. Result merging is
order-stable so concurrency cannot change coverage or claims.

---

## 9. Caching and freshness (Q10)

Two independent axes. They must not be conflated.

- **Fact freshness** — how current the *state of the world* must be for the requested fact.
  Derived from the Goal: `VOLATILE` (current injury/roster/trade/standings, or a current-year
  `VALUE`), `IMMUTABLE` (past-season statistics, historical events, encyclopedia facts), or
  `MIXED`. It decides whether an older document is still acceptable.
- **Document freshness** — when the *document* was published and last retrieved, from
  `publication_date` and `retrieved_at`. A document can be old yet still describe a current
  fact (an updated roster page) or new yet irrelevant. It is measured, never inferred from
  fact freshness.

A cache entry is usable only when **both** hold: its fact class tolerates the age, and for
`VOLATILE` it has been revalidated within TTL and carries a valid `as_of`.

| Fact class (from Goal) | Document age signals | Cache policy |
|---|---|---|
| `IMMUTABLE` (historical/explanatory) | `publication_date`, `retrieved_at` | TTL 7–30 days; may serve without revalidation; original provenance preserved |
| `VOLATILE` (current injury/roster/trade/standings) | must be revalidated now | TTL 5–15 min; conditional request; never serve stale without `as_of` |
| `MIXED` | per-URL | per-URL class |

### 9.1 Cache lookup key vs content hash

- **Lookup key = canonical URL** (plus `extractor_version`). The hash of a body cannot be
  known before fetching, and two different URLs may share identical content, so the cache is
  URL-keyed: `cache[url] -> versioned entry`.
- **Content hash is a version/validation token, not the lookup key.**
  `raw_content_hash = SHA-256(raw response bytes)` identifies the server version;
  `body_hash = SHA-256(body_text)` identifies the extraction version (§6.1). Conditional
  revalidation (`ETag`/`If-None-Match`, `Last-Modified`/`If-Modified-Since`, or a raw-hash
  comparison on a 200) decides whether the cached version is still current.
- A URL may hold several historical versions; only the newest is served. Two URLs with equal
  bytes remain two entries — dedup by hash happens later at the artifact level, never by
  dropping a URL from the cache.

### 9.2 Time fields (a cache hit never rewrites them)

- `retrieved_at` — the moment the body was actually fetched from the network. Preserved
  across cache hits.
- `revalidated_at` — the moment a conditional request confirmed the cached body is still the
  server's current version (cache metadata only; not an evidence field).
- `as_of` — the time the fact is asserted to hold. For `VOLATILE` facts this is the
  revalidation/fetch time; for historical sources it may be the publication date or an
  explicit dataset as-of. A cache hit never rewrites `retrieved_at` or `as_of`; it records
  `cache=hit|revalidate|miss`.

### 9.3 Storage

In-process LRU plus optional disk store under `.runtime/web_cache/` with `0700`/`0600`
permissions (consistent with diagnostics), Git-ignored, no request headers or credentials.
Bodies stay out of diagnostics. Cache entries store `extractor` + `extractor_version`;
version mismatch invalidates spans and forces re-extraction (§6.1). The cache respects the
SSRF guard.

**Milestone note.** M1 establishes only the body-version metadata and the cache *contract*
(§6.1); the full URL-keyed versioned cache, revalidation and TTL behavior described here are
implemented in M3. **No cache behavior is active in M1.**

---

## 10. Latency, cost and benchmark plan

### 10.1 Stage distinction (already instrumented, extended)

Existing spans: `web:search`, `web:fetch`, `web:retrieve`, `web:verify`, `tool:web_research`,
`semantic`, `planner`/`planning_and_tools`, `judge`, `composer`, `consistency`,
`goal_end_to_end`. Add: `web:source:<id>` (per provider), `web:rank`, `web:cache`,
`research:llm_query`, and a `research_metrics` diagnostic record
(provider calls, fetches, cache hit/miss, budget usage, LLM tokens per research role).

The kickoff's categories map as:

| Requested category | Existing/extended span |
|---|---|
| search latency | `web:search` (sum) + `web:source:<id>` (per provider) |
| fetch latency | `web:fetch` + `web:rank` + `web:cache` |
| evidence verification latency | `web:verify` (and `research:llm_verify` count) |
| LLM reasoning latency | `semantic`, `planner`/`planning_and_tools`, `composer` |
| repeated Planner rounds | `llm_call_stats.planner_rounds` + `planning_and_tools` |
| Goal end-to-end | `goal_end_to_end` |

### 10.2 Proposed performance objectives (to be validated, not claimed)

Baseline (36 real research goals, 2026-09): `tool:web_research` p50 ≈ 84 s, p90 ≈ 158 s;
`web:search` p50 ≈ 42 s. The dominant cost is *sequential* search. Proposed targets for
Web Research v2, each to be measured by the harness before being treated as met:

| Metric | Proposed target (p50 / p95) | Rationale |
|---|---|---|
| `web:search` (bounded, parallel; ≤8 provider calls/call, ≤24/Goal) | ≤ 8 s / ≤ 20 s | 4–6× fewer serial round-trips |
| `web:fetch` (≤6 docs, parallelism 4) | ≤ 8 s / ≤ 20 s | parallel + cache |
| `web:verify` (≤6 LLM calls) | ≤ 20 s / ≤ 40 s | unchanged semantics, fewer candidates |
| `tool:web_research` | ≤ 25 s / ≤ 50 s | sum of the above |
| `goal_end_to_end` (web goal) | ≤ 35 s / ≤ 65 s | plus semantic/judge/composer |

These are **objectives**, not promises. No claim is made that they are achievable until
the harness in §10.3 produces repeated measurements. If a keyed broad-search provider is
configured, the harness decides whether it improves p95 enough to justify cost.

The deadline defaults in §7 are chosen from these same targets: `provider_timeout_ms=6 s`
and `fetch_timeout_ms=10 s` bound a single call; `soft_deadline_ms=30 s` sits above the p50
target, so normal runs are never truncated; `hard_deadline_ms=45 s` sits below the p95
target, so the tail degrades to a **verified partial result** rather than overrunning. A
budget that cannot meet the p95 target is reported as a measured finding, not hidden.

### 10.3 Benchmark harness

A reproducible harness (not part of the runtime path) records, for a fixed representative
task set: stage latencies, provider call counts, fetched/blocked counts, cache hit rate,
LLM calls and tokens per research role, and goal status. It runs offline fixtures and live
runs separately, and writes a machine-readable report. Performance regressions become test
assertions only after a stable baseline exists.

### 10.4 Cost

Research LLM calls per web Need: ≤1 query plan + ≤6 verify = ≤7, plus the unchanged
semantic/planner/composer calls. Per call: ≤8 provider calls and ≤4 fetches; per Goal the
shared quota caps these at ≤24 / ≤10 across all rounds. Token usage is already captured per
provider response by `LLMClient` and persisted (`llm_usage`); the extension is to attribute
it to research roles. Caching reduces repeat-question cost.

### 10.5 Metrics vs the Phase 5B baseline

All metrics are computed from persisted diagnostics/artifacts plus the benchmark harness,
over the same **frozen representative task set**, and reported with sample counts.
"Phase 5B baseline" means re-measured on that set at the reviewed HEAD (`7ad65b2`), not
taken from this document.

| Metric | Definition | Direction |
|---|---|---|
| **Answer Completion Rate (ACR)** | fraction of tasks whose mandatory `AnswerProposition`s are all `SATISFIED` (`GoalStatus.COMPLETE`) at the current evidence standard; a task counts as complete only if the answer expresses every mandatory result | ↑ |
| **Evidence Precision (EP)** | of admitted `SUPPORTED` findings, the fraction whose passage genuinely establishes the proposition under subject/metric/scope (rubric-adjudicated sample, or deterministic re-check for structured facts); `1 - EP` is the false-support rate | ↑ (≈1.0 for admitted support) |
| **Information Gain (IG)** | per research call: count of mandatory propositions moved `UNSUPPORTED/PARTIAL -> SATISFIED` (coverage delta), plus unique verified facts per provider call and per fetched document | ↑ |
| **Latency** | `web:source:<id>`, `web:search`, `web:fetch`, `web:verify`, `tool:web_research`, `goal_end_to_end` p50/p95 over repeated runs | ↓ |
| **Retrieval waste** | blocked/empty/JS/paywalled fetches and typed provider errors per task | ↓ |
| **Cache efficiency** | hit/revalidate/miss rate and its latency effect on repeated questions | ↑ hit rate |

Reporting rules:

- ACR and EP are never traded against each other silently: raising ACR by admitting weaker
  support is a regression, not progress.
- IG is measured against the pre-research artifact state of the same Goal (what the Planner
  already had), so "more text" is not information gain.
- Latency is reported per stage; a p50 improvement that worsens p95 is reported as such.
- No metric is asserted before the harness runs; §10.2 remains objectives until then.

---

## 11. Risks, tradeoffs and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| No-key search engines are bot-hostile / ToS-risky | unreliable discovery; legal/politeness | configurable provider interface; prefer feeds + Wikipedia + official; no scraping of challenge pages; per-host rate limits |
| Environment-sensitive providers (StatsAPI 406, MLB.com 403) | false "available" assumptions | runtime capability probe + typed fallback; never assume; record per-request status |
| Deterministic lexical ranking misses paraphrase | honest insufficiency persists | relationship-aware features + optional LLM verify; no embeddings (non-goal); honest `LIMITED` |
| Parallel fetching violates rate limits | blocked/quota | per-host concurrency 1 + min interval + backoff |
| Stale volatile cache serves wrong injury/roster | factual harm | freshness classes; volatile always revalidated; `as_of` required |
| Structured adapters expand scope | delivery risk | one adapter family, opt-in, small surface, graceful absence |
| Budget too tight / too loose | truncation vs latency | centralized tunable budget + benchmark; degradation is typed, never silent |
| Extraction ladder misreads boilerplate | wrong span | offset/replay invariant + verifier gate unchanged; extractor recorded |
| Internal routing becomes a hidden monolith | maintainability | pure functions + unit tests; one module per concern |
| Accidentally duplicating verifier/Judge | contract break | verifier and Judge files untouched; new code only feeds candidates |
| Aliases/historical dates treated as answers | fabrication | aliases only shape queries; only fetched body spans ground claims |

**Tradeoff summary.** We improve recall by adding sources and parallelism, and we control
the resulting risk with deterministic ranking, freshness classes, per-host politeness and a
central budget. We deliberately accept that broad, keyless search is weaker than a paid
index and that some questions remain honestly `LIMITED`.

---

## 12. Architecture questions → answers

| # | Question | Answer | Where |
|---|---|---|---|
| 1 | Broad search, official sources, feeds and structured APIs without too many Planner tools | Keep **one** `web_research` capability; add an internal Source Registry + Research Router behind `execute()`. Planner surface unchanged. | §2, §3 |
| 2 | Provider interface actually necessary | Extend the existing `SearchBackend`/`SearchHit`/`FetchedPage` with a `SourceDescriptor` (kind, capabilities, auth, freshness, trust, rate limit) and a generic `SearchProvider`. Reuse `FeedSearchBackend`, `WikipediaSearchBackend`, `fetch_page`. No new parallel interface hierarchy. | §3.2, §4 |
| 3 | Route statistics vs narrative | `local_analytics`+`python_compute` own computation; `dynamic_fact` owns operational facts; `web_research` only retrieves narrative and authoritative **records**. Deterministic router keyed on proposition kind/scope/freshness. | §3.3, §3.5 |
| 4 | Useful queries incl. aliases, dates, interviews, missing requirements | `ResearchQueryPlan` with ENTITY/METRIC/EVENT/INTERVIEW/FRESHNESS/MISSING facets, aliases from entity refs/directory, structured dates from `time_scope`, explicit `facets_missing`; one bounded LLM refinement. | §5.1–5.2 |
| 5 | Rank by requested relationship, not subject keywords | Deterministic `CandidateRanker` scoring subject + required-information + scope + intent + **`trust_weight` (higher = more trusted)** + freshness; proposition-aware `SpanSelector`. | §5.3 |
| 6 | Fetch/extraction/fallback when blocked/incomplete | Cache → primary fetch (SSRF) → transient retry/backoff → extraction ladder → alternate URL → typed failure → provider fallback; explicit raw→`body_text`→span version/offset contract (§6.1). | §6 |
| 7 | Verifier consumes documents without duplicating Web Research | `verify_span`, `EvidenceFinding`, Judge and consistency are **unchanged**; Web Research only supplies better-ranked spans and more propositions within the same budget. | §3.1, §13 |
| 8 | Research budget | **Goal-level shared** `GoalResearchBudget` (across all Planner rounds) + per-call `ResearchBudget` clamped to the remainder; provider timeout / soft deadline / hard deadline; concurrency reservation/settlement; typed verified partial result. | §7 |
| 9 | What can be concurrent vs dependent | Source queries and page fetches concurrent and bounded; LLM verify bounded; canonical-id structured calls wait on the Binder; Judge/Composer serial. | §8 |
| 10 | Caching and freshness historical vs current | Independent **Fact freshness** and **Document freshness**; URL-keyed cache with content hash as a version token; `retrieved_at`/`as_of` preserved across hits. | §9 |

---

## 13. Minimal domain and tool contracts

**Unchanged (accepted Phase 5B contracts):** `Artifact`, `Export`, `Scope`,
`RetrievedDocument`, `EvidenceSpan`, `EvidenceFinding`, `AnswerProposition`, `ToolRequest`,
`ToolOutcome`, `Claim`, `AnswerProjection`, `verify_span`, Judge, consistency.

**New internal contracts (not Planner-visible, not Artifact kinds):**

```python
# app/web/sources/base.py
@dataclass(frozen=True)
class SourceDescriptor:
    source_id: str
    kind: str                 # BROAD_SEARCH | PUBLISHER_FEEDS | ENCYCLOPEDIA | REFERENCE | STRUCTURED
    discovery: bool           # can return fetchable URLs
    full_body: bool           # returns/extracts page bodies
    structured: bool          # returns machine-readable records
    historical: bool
    current: bool
    auth: str = "none"        # none | api_key
    trust_weight: float = 0.25  # HIGHER = MORE trusted (1.00 official … 0.00 discovery-only)
    latency_class: str = "medium"
    rate_limit_per_min: int | None = None

class SearchProvider(Protocol):
    descriptor: SourceDescriptor
    def search(self, query: str, *, limit: int, language: str) -> list[SearchHit]: ...
```

```python
# app/web/budget.py
@dataclass
class GoalResearchBudget:            # created once per Goal; shared across Planner rounds
    goal_provider_calls: int = 24
    goal_documents_fetched: int = 10
    goal_llm_query_calls: int = 2
    goal_llm_verify_calls: int = 12
    goal_search_rounds: int = 3
    goal_soft_deadline_ms: int = 30_000
    goal_hard_deadline_ms: int = 45_000

@dataclass
class ResearchBudget:                # per execute(); clamped to the remaining Goal budget
    max_search_rounds: int = 2
    max_queries_per_round: int = 4
    max_provider_calls: int = 8
    max_documents_fetched: int = 4
    max_parallel_queries: int = 4
    max_parallel_fetches: int = 4
    max_llm_query_calls: int = 1
    max_llm_verify_calls: int = 6
    provider_timeout_ms: int = 6_000
    fetch_timeout_ms: int = 10_000
    soft_deadline_ms: int = 30_000
    hard_deadline_ms: int = 45_000
    max_bytes_per_doc: int = 2_000_000
```

`ResearchQueryPlan`, `QueryVariant` and `CandidateRanker` are internal dataclasses/functions
in `app/web/`. The `WEB_EVIDENCE` artifact adds *metadata* keys (`sources`, `cache`,
`budget_report`, `source_outcomes`) without changing its kind, exports or the evidence
records the Judge reads. `RetrievedDocument.body_text` remains the replay source; additive
metadata records `raw_content_hash` (= existing `content_hash`), `body_hash` and
`extractor_version` so a cache/replay can prove it reused the same body version (§6.1).
Structured records are rendered into a deterministic canonical body so they remain admissible
`DOCUMENT_BODY` evidence. Existing tests that assert on `documents`, `evidence_spans`,
`evidence_findings`, `proposition_coverage`, `retrieval_status` continue to hold.

---

## 14. ADR decisions (0026–0030, accepted)

| ADR | Title | Decision summary |
|---|---|---|
| **0026** | Single-capability, multi-source Web Research | `web_research` remains the only Planner-visible research tool; a Source Registry + deterministic Research Router live behind it; the analytical-vs-research responsibility boundary is fixed (`local_analytics`/`python_compute` compute, `dynamic_fact` owns operational facts, `web_research` retrieves narrative and authoritative records). |
| **0027** | Relationship-aware retrieval and evidence versioning | Deterministic query facets; relationship-aware candidate ranking with a monotone `trust_weight` (higher = more trusted); bounded parallel search/fetch; order-stable merging; and the Raw response → extracted `body_text` → `EvidenceSpan` version/offset contract. |
| **0028** | Research Budget: Goal-level quota and deadline model | One `GoalResearchBudget` shared across all Planner rounds plus a per-call `ResearchBudget` clamped to the remainder; distinct provider timeout, soft deadline and hard deadline; concurrency reservation and cancellation settlement; typed verified partial results. |
| **0029** | Fact vs Document freshness and URL-keyed caching | Fact freshness (state of the world) and document freshness (publication/retrieval) are separate axes; the cache is URL-keyed with the content hash as a version/validation token; `retrieved_at` and `as_of` are never rewritten by a cache hit. |
| **0030** | Official structured sources and the retrieval-vs-computation boundary | Structured baseball APIs are optional record retrievers behind `web_research`, probed at runtime and rendered to canonical admissible bodies; any arithmetic over records flows through `python_compute` under the accepted enumeration contract. |

ADR 0026–0030 do not amend 0021–0025; they explicitly preserve the responsibility boundary.
The formal records are `docs/adr/0026-…` … `docs/adr/0030-…`, indexed in `docs/adr/README.md`,
and specified in `docs/SPEC.md` §13. Implementation proceeds under the M1 tickets in
`docs/architecture/WEB_RESEARCH_V2_M1_TICKETS.md`.

---

## 15. Phased implementation plan (four measurable milestones)

Guiding rule: each milestone must produce a **measured** retrieval/completion/latency delta
against the frozen representative task set, using only sources that already work **without
authentication**.

Source tiers:

- **Tier A — keyless, working now (M1–M3 depend on these):** Wikipedia (search + body, live),
  FanGraphs/CBS/Yahoo publisher feeds (live), Google News RSS (discovery only),
  Retrosheet/BRef/SABR/Seamheads (reference/historical), StatsAPI `/people`, `/teams`,
  `/seasons` (partial), Savant CSV. MLB.com RSS is currently 403 and must not be assumed.
- **Tier B — broad-search APIs requiring a key (M4 only, config-gated):** Brave, Bing,
  Google Programmable Search, Tavily, Exa, SerpAPI. Not enabled until M1–M3 metrics show the
  gap they would close.

| Milestone | Scope | Measurable outcome |
|---|---|---|
| **M1 — Contracts, instrumentation, real Wikipedia baseline** | per-provider spans + `research_metrics`; typed `SourceOutcome` + failure classification (L6); **necessary contracts only**: minimal `SourceDescriptor` (incl. `trust_weight` declared), `StructuredRecord`/body-version metadata (`body_hash`, `extractor_version`), `GoalResearchBudget` *type*; benchmark harness + frozen task set; enable **real Wikipedia search + body retrieval/verification for historical `EXPLANATION` questions** | measured old-vs-new baseline for **Answer Completion, Evidence Precision and Latency** on the frozen task set, including at least one real historical question grounded in a fetched Wikipedia body; **no key required**. Full cache behavior is deferred to M3; the `trust_weight` ranking benefit is verified in M2 |
| **M2 — Relationship-aware retrieval** | `ResearchQueryPlan` facets (ENTITY/METRIC/EVENT/INTERVIEW/FRESHNESS/MISSING), `CandidateRanker`, proposition-aware `SpanSelector`, bounded parallel search/fetch, retry/backoff | EP and IG improve on the frozen task set; **the `trust_weight` ranking benefit is measured here**; `web:search`/`web:fetch` latency drops materially; concurrency deterministic |
| **M3 — Budget, deadlines, full cache** | implement Goal-level shared quota + per-call clamp + in-flight semantics; provider/soft/hard timeouts; typed verified partial results; **full URL-keyed versioned cache** and fact vs document freshness | no Goal overruns the hard deadline; volatile facts never served stale; repeated-question latency and cost drop; budget tests pass |
| **M4 — Optional breadth (config-gated)** | one Tier B broad-search provider behind the `SearchProvider` interface; one official structured adapter family | measured ACR/EP/IG/latency delta decides whether either is enabled by default; absence degrades gracefully |

Each milestone: full offline suite green, live probe record, no Judge/verifier change, no
contract relaxation. Rollback via `WEB_V2_ENABLED=false` (and per-milestone flags). The order
is deliberate: M1–M3 prove measurable gains with keyless sources before any authenticated
provider is introduced.

M1 deliberately stops at **contracts, instrumentation, the real Wikipedia retrieval path and
the baseline**. It does not implement query facets, relationship ranking, parallel fetch or
cache behavior; those are M2/M3. `trust_weight` is declared as a contract in M1 but its
ranking effect is not claimed until M2 measures it.

---

## 16. Acceptance criteria (representative MLB research tasks)

Language-general; no production code may branch on a specific player/event name.

1. **Current injury/status question.** Given a current injury/roster question, the run
   either cites a fetched body span with a publication date and an `as_of`, or returns a
   typed `SOURCE_UNAVAILABLE`/`INSUFFICIENT_EVIDENCE` with `LIMITED`. No snippet-only support.
2. **Historical explanation (e.g. a past work stoppage).** At least one encyclopedia/reference
   body is retrieved and every claim resolves to a span; if coverage is partial the answer is
   honestly `LIMITED`, and `historical_capable` reflects real sources, not a query keyword.
3. **Statistical question.** Routes to `local_analytics`/`dynamic_fact`; the narrative path is
   never the primary source for a required `VALUE`, and `web_research` computes nothing; if the
   local or structured source is unreachable the failure is typed and an alternative is still
   considered.
4. **Interview / original-quote question.** The query plan contains an `INTERVIEW` facet and
   ranking prefers pages containing quotation markers; support still requires a verified span.
5. **Alias / multilingual question.** Known aliases appear in the executed queries (asserted
   from diagnostics), and the subject is not mangled.
6. **Budget behaviour.** Under an artificially tiny budget the tool returns a `PARTIAL`
   artifact with `BUDGET_EXCEEDED` and no fabricated support; provider calls/fetches do not
   exceed the configured maxima.
7. **Evidence chain (Phase 5B invariant).** Every persisted `EvidenceFinding.span_id`
   resolves to a persisted `EvidenceSpan` of an `OK` document with
   `body[start:end] == span.text`; deterministic hard conflicts still freeze findings and the
   LLM still cannot upgrade them.
8. **Concurrency determinism.** The same inputs yield the same ranking, fetched set and
   coverage regardless of source/fetch completion order.
9. **Freshness.** A volatile *fact* is never served from a stale cache entry; a historical
   query can be served from the long-TTL cache with the original `retrieved_at` and `as_of`
   preserved. Fact freshness and document freshness are evaluated independently (§9).
10. **Diagnostics.** Per-provider latency, provider/fetch counts, cache hit/miss, budget
    usage and research LLM token usage are recorded; no credentials, bodies, prompts or
    user text are persisted (Phase 5B §12.7 redaction still holds).
11. **Non-regression.** The accepted Phase 5B Judge, verifier, consistency, Binder, SQL
    guard, measure verification and enumeration tests all still pass unchanged.
12. **Performance (measured, not asserted).** The benchmark reports stage latencies for the
    representative set; objectives in §10.2 are marked met/not-met with the raw numbers.
13. **Responsibility boundary.** No `web_research` path aggregates, groups, ranks or computes
    derived measures; structured records are retrieved, not analysed, and any arithmetic goes
    through `python_compute` (§3.5).
14. **Body version contract.** Every admitted span satisfies
    `body_text[start:end] == span.text` for the stored `body_hash`/`extractor_version`; a
    version mismatch forces re-extraction instead of offset reuse (§6.1).
15. **Goal-level budget.** A multi-round Goal cannot exceed `GoalResearchBudget`; a later
    `web_research` call receives only the remaining quota, and cancelled work is released by
    settlement (§7.3).
16. **Metrics.** ACR, EP, IG and latency are reported against the re-measured Phase 5B
    baseline on the frozen task set; ACR and EP are never traded against each other silently
    (§10.5).

---

## 17. What this proposal deliberately does NOT do

- It does not add Planner tools, capabilities or a second orchestration graph.
- It does not move raw-data aggregation out of `local_analytics`, and it does not force all
  computation into `python_compute`; `web_research` computes nothing (§3.5).
- It does not modify `verify_span`, `EvidenceFinding`, the Judge, consistency or diagnostics
  policy semantics.
- It does not add named-player/named-event shortcuts.
- It does not commit to a paid provider or to an embeddings/RAG stack.
- It does not claim achievable latency before the benchmark runs.
- It stops at design: no Web Research v2 code is written by this task.

**Status:** architecture approved; ADR 0026–0030 accepted (see §14 and `docs/adr/`); SPEC §13
updated; M1 tickets issued. Implementation begins under TDD with M1 only.

---

## Appendix A — M1 executable tickets

The M1 tickets are specified in `docs/architecture/WEB_RESEARCH_V2_M1_TICKETS.md` (inputs,
outputs, tests, non-goals, completion conditions). Summary:

| Ticket | Title |
|---|---|
| **M1-T1** | Per-provider spans + `research_metrics` |
| **M1-T2** | Typed `SourceOutcome` + failure classification |
| **M1-T3** | Benchmark harness + frozen task set + old/new baseline |
| **M1-T4** | Minimal `SourceDescriptor` contract + keyless Wikipedia source adapter |
| **M1-T5** | Real Wikipedia historical retrieval + body verification |
| **M1-T6** | Body-version metadata (`body_hash`/`extractor_version`) |
| **M1-T7** | Trust-direction + classification regression tests |

**M1 constraints:** no Judge/verifier/Composer change; no new Planner-visible tool; no
authenticated provider; no query-facet / ranking / parallel / cache behavior (M2/M3).
**M1 exit:** real historical Wikipedia retrieval with body verification, plus a measured
old-vs-new **Answer Completion / Evidence Precision / Latency** baseline on the frozen task set.
