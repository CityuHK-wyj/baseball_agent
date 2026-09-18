# v0.5 Runtime Performance & Execution-Observability Audit

**Checkpoint:** `pi/v0.5-planner-convergence`
**Exact SHA:** `899957846805347453accfc34f74ab53f86ddcb3`
**Audit branch:** `pi/v0.5-runtime-performance-audit`
**Mode:** MEASURE / TRACE / DIAGNOSE ONLY. No production module was modified. No optimization,
no Web repair, no Planner/Tool/security change, no query-specific shortcut, no merge, no tag.
`main` is untouched (`35b47c4174e98460ebdbcd2ace46adfba36225c4`).

**Answer to the central question:** in the live product, user-visible latency is
overwhelmingly **model-inference latency** — specifically the configured model
(`deepseek-v4-pro`) producing very long completions. Every other runtime phase
(tool execution, DuckDB analytics, Judge, scope verification, persistence) is
sub-millisecond to low-tens-of-milliseconds. A single uncapped planner completion was
measured at **710.8 s / 15,856 completion tokens**, and a simple query triggered a
re-plan chain with **three sequential planner calls** before the audit budget was reached.
Web research is unavailable because the environment resolves every public hostname into
the `198.18.0.0/15` benchmarking range (a transparent-proxy *fake-IP* pattern) and the
application's SSRF guard correctly refuses private addresses — the network itself is
fully reachable.

---

## 1. Method, instrumentation and evidence

### 1.1 How latency was measured

All timings use `time.perf_counter()` in an **external diagnostic harness**
(`harness.py`), never log ordering. The harness wraps the *injected* collaborators and a
few module-level engine functions at instrumentation time:

* semantic interpreter, planner, response composer (each with a purpose-labelled provider);
* every registered Tool's `run`;
* the Judge (`assess_need`, `summarize`) and the state projector;
* `resolve_bindings_with_gaps`, `verify_artifact_scope`, `build_claims`,
  `validate_claims`, `obligation_coverage`;
* `_planner_context`, `_add_artifacts`, `_persist`, `_build_trace`, `_new_goal`,
  `_run_planner`;
* the DuckDB/PostgreSQL executors, the SQL guard, `compile_analytical_query`, the
  knowledge retriever, the MLB people search, the operational store;
* the web search backends and page reader.

The runtime is single-threaded and **fully serial** (no concurrent phases were observed),
so exclusive leaf phases sum to total wall clock and the remainder is reported as
`residual_orchestration_ms`. Twenty "exclusive" leaf phases are tracked; container phases
(`planner_loop`, `turn`, `execute_step`) are excluded from the sum to avoid double
counting.

### 1.2 Sample counts

| Class | Runs | Notes |
|---|---|---|
| Deterministic offline turns (scripted planner, real runtime) | **69 turns / 84 tool calls** | Cheap; many repetitions per scenario |
| Live LLM turns (uncapped) | 1 turn, aborted | semantic 92.4 s, planner **710.8 s**, replan **368.7 s**, third planner call started |
| Live LLM turns (diagnostic 110 s/call wall-clock cap) | **5 turns / 15 model calls** | Cap documented; used to bound budget |
| Tiny provider probe | 1 | 2.74 s (88 prompt tok → 12 completion tok) |
| Web/network probes | 1 full sweep | DNS/TCP/HTTP/SSRF/live-path |

The 110 s cap is enforced by a diagnostic wrapper (`_capping_provider`), not by the
product. It raises `ProviderTimeout`, so the product's *own* fallback policy engages. The
uncapped product behaviour is recorded separately in `live_uncapped_partial.log`.

### 1.3 Redaction / security

No API keys, passwords, authorization headers, cookies, proxy credentials or full hidden
prompts were printed or persisted. Structured summaries and sizes are stored; regex
redaction is applied by `EventJournal`/`recorder`. Production prompts are recorded only as
character/token counts. The SQL guard, read-only role, DuckDB sandbox, SSRF guard,
knowledge governance and permissions were not weakened.

### 1.4 Files produced (all under `docs/reviews/v05-runtime-performance/`)

```
harness.py                      external profiling wrappers (no production change)
builders.py                     offline / live instrumented runtime builders
scenarios.py                    capability-class diagnostic scenarios
run_deterministic.py            deterministic tool/recovery runs
run_live.py                     live LLM runs (optional AUDIT_CALL_CAP)
web_network_diagnostics.py      layered web/network diagnosis
tool_dossier.py                 real ToolRegistry dossier
analyze.py                      aggregate tables
profile.jsonl                   per-phase events (all turns)
timelines.jsonl                 per-turn critical-path summaries
llm_calls.jsonl                 per model call (purpose, tokens, latency)
tool_calls.jsonl                per tool call (inputs, bindings, outcomes, artifacts)
recovery.jsonl                  per-turn durable attempt / need / failure-class record
notes.jsonl                     nested observations (ir_compile, store_save)
errors.jsonl                    harness-observed exceptions (empty)
web_diagnostics.json            layered network evidence
tool_registry.json              ToolRegistry dossier
summary.json                    aggregated metrics
live_clarification.json         live semantic clarification evidence
live_uncapped_partial.log       uncapped live call log
```

---

## 2. Executive summary

* **Why is the Agent slow?** Model inference. The configured `deepseek-v4-pro` emits very
  long completions (mostly hidden reasoning counted as completion tokens). A semantic call
  of 373 prompt tokens produced **3,351 completion tokens and took 70–101 s**; a planner
  call of ~3,300 prompt tokens produced **15,856 completion tokens and took 710.8 s**.
* **What dominates latency?** LLM calls, by 2–3 orders of magnitude. In deterministic runs
  with no LLM the entire turn is **12–120 ms**; the largest non-LLM component is
  DuckDB/Parquet execution (~17–21 ms) and SQLite persistence (~15–44 ms/turn).
* **How many model calls per turn?** Semantic 1 + planner initial 1 + planner
  `add_needs` 0–6 + response composer 0–1. Observed live: **1** (clarification turn),
  **3**, **4**. Uncapped, a single simple query reached **3 sequential planner calls**
  before abort, i.e. a single question can invoke the model **4–9+ times**.
* **Which Tool calls are expensive?** None are LLM-expensive. `shared_knowledge` ≈ **99 ms**
  (regex recompilation in retrieval), `local_analytics` ≈ **17–21 ms** (Parquet scan), and
  `entity_resolution` with an ambiguous mention ≈ **1.0–1.4 s** because of an unguarded live
  MLB StatsAPI people-search call. Web research fails in **~1–145 ms** (refused before
  connecting).
* **Is latency LLM, network, database, orchestration or persistence?** LLM ≫ network tool
  > database ≈ persistence > Judge/verification >> orchestration. Judge and scope
  verification are deterministic and sub-millisecond (no LLM Judge is wired).
* **Why is Web unavailable?** DNS resolves public hosts to `198.18.0.0/15`
  (`ipaddress.is_private=True`, fake-IP transparent proxy). The SSRF guard refuses them
  before any connection. The same hosts return HTTP 200 (22–112 KB) when the guard is
  bypassed, so the search backend, TLS and proxy are healthy. The first failing boundary is
  `SSRF_POLICY_BLOCK_ON_RESERVED_RESOLUTION`. The environment is the root cause; the
  application cannot distinguish fake-IP-for-public from genuinely-private and errs safe.

---

## 3. Actual runtime timeline

### 3.1 Deterministic representative run — `local_count_parquet` (no LLM)

Waterfall (`T+` relative to the user message; exclusive leaf durations, ms):

```
T+0.00   user message received
T+0.00   resolve_entities        0.14
T+0.01   semantic (RuleBased)    0.03
T+0.01   goal_construction       0.10
T+0.02   planner_context         0.73
T+0.02   planner_initial         0.00   (scripted)
T+0.03   planner_context         0.30
T+0.03   planner_next_action     0.01
T+0.03   binding_resolution      0.00
T+0.03   persistence (pre-tool)  4.1
T+0.03   ir_compile              0.14
T+0.04   sql_guard               1.3
T+0.04   db_execute (DuckDB)    17.2
T+0.06   tool_execute           19.4   (contains the above)
T+0.06   scope_verification      0.01
T+0.06   artifact_registration   0.00
T+0.07   judge_assess            0.16
T+0.07   persistence             12.5
T+0.08   coverage_summarize      0.21
T+0.08   claim_build/validate    0.04
T+0.08   state_projection        0.04
T+0.08   response_compose        0.02
T+0.08   trace_build             0.02
T+0.09   persistence (final)     0.5
T+0.10   TOTAL                  ~43 ms
```

### 3.2 Live representative run — `live_web_unavailable` (uncapped semantic, 110 s cap)

```
T+0.000    user message received
T+0.001    semantic call started          (prompt 1,280 chars)
T+107.314  semantic call finished         (1,266 chars out; 386 / 1,553 tokens)
T+107.315  goal construction + planner context
T+107.319  planner initial call started   (prompt 11,224 chars)
T+182.021  planner initial finished       (869 chars out; 3,287 / 2,337 tokens)
T+182.022  tool admitted: web_research
T+182.167  web_research finished          SOURCE_TRANSIENT (SSRF block; 145 ms)
T+182.168  judge + projection
T+182.170  planner add_needs call started (prompt 12,162 chars)
T+292.170  planner add_needs timed out    (110 s diagnostic cap)
T+292.2    turn ended FAILED
```

### 3.3 Uncapped live run (aborted)

```
semantic        92.4 s   (3,351 completion tokens)
planner initial 710.8 s  (15,856 completion tokens)   ← 11.8 minutes for one call
planner replan  368.7 s  (11,895 completion tokens)
planner replan  started; audit budget reached
```

---

## 4. Per-stage latency

### 4.1 Deterministic offline (median ms/turn, no LLM)

| Scenario | total | persistence | tool exec | Judge | planner ctx | residual | status |
|---|---:|---:|---:|---:|---:|---:|---|
| local_count_parquet | 42.7 | 17.2 | 19.4 | 0.2 | 0.7 | 1.4 | COMPLETE |
| local_avg_velocity | 45.0 | 22.4 | 19.1 | 0.2 | 0.8 | 1.4 | COMPLETE |
| local_derived_compute | 61.3 | 35.5 | 21.3 | 0.3 | 0.9 | 2.4 | COMPLETE |
| roster_then_local | 61.4 | 36.1 | 20.5 | 0.5 | 1.0 | 2.6 | LIMITED |
| knowledge | 118.4 | 19.6 | 99.8 | 0.2 | 0.7 | 1.3 | COMPLETE |
| entity_resolution | 22.3 | 18.7 | 0.7 | 0.1 | 0.8 | 1.3 | COMPLETE |
| entity_ambiguity | 987.7 | 25.1 | 961.2 | 0.3 | 1.0 | 2.2 | FAILED |
| batting_stats | 27.7 | 24.4 | 0.2 | 0.1 | 0.7 | 1.4 | COMPLETE |
| web_fake_grounded | 18.6 | 15.2 | 0.2 | 0.1 | 0.8 | 1.4 | COMPLETE |
| web_then_entities | 37.5 | 33.2 | 0.6 | 0.3 | 0.9 | 2.0 | COMPLETE |
| web_live_unavailable | 50.6 | 15.2 | 32.9 | 0.0 | 0.7 | 1.4 | FAILED |
| db_then_web_live | 80.9 | 33.1 | 43.5 | 0.3 | 1.0 | 2.4 | LIMITED |
| web_to_db | 71.9 | 44.5 | 21.7 | 0.5 | 1.1 | 3.2 | COMPLETE |
| invalid_ir | 19.6 | 16.1 | 0.2 | 0.1 | 0.6 | 1.4 | FAILED |
| empty_result | 35.8 | 16.0 | 16.6 | 0.2 | 0.8 | 1.6 | FAILED |
| postgres_down | 22.6 | 16.0 | 1.6 | 0.0 | 0.6 | 1.4 | FAILED |
| impossible_capability | 12.1 | 10.2 | 0.0 | 0.0 | 0.6 | 0.7 | FAILED |
| recovery_vague_no_obligation | 21.4 | 18.6 | 0.2 | 0.2 | 0.7 | 1.4 | FAILED |
| recovery_with_obligation | 57.4 | 34.0 | 19.6 | 0.4 | 1.0 | 2.7 | LIMITED |
| clarification_turn t1 | 4.0 | 3.7 | 0.0 | 0.0 | 0.0 | 0.2 | WAITING_FOR_USER |
| clarification_turn t2 | 37.5 | 15.0 | 17.2 | 0.2 | 0.8 | 1.6 | LIMITED |

### 4.2 Live (diagnostic 110 s/call cap; seconds)

| Scenario | turn | total | semantic | planner initial | planner add_needs | response | model calls | status |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| live_simple_local | 1 | 100.9 | 100.9 | — | — | — | 1 | WAITING_FOR_USER |
| live_web_unavailable | 1 | 292.2 | 107.3 | 74.7 | 110.0 (cap) | — | 3 | FAILED |
| live_entity_ambiguity | 1 | 351.0 | 110.0 (cap) | 110.0 (cap) | 110.0 (cap) | 20.8 | 4 | LIMITED |
| live_followup | 1 | 318.3 | 83.8 | 110.0 (cap) | 110.0 (cap) | 14.2 | 4 | LIMITED |
| live_followup | 2 | 263.2 | 43.1 | 110.0 (cap) | 110.0 (cap) | — | 3 | FAILED |

**Judge and scope verification are not latency contributors.** `judge_assess` is
0.05–0.5 ms; `coverage_summarize` ≈ 0.2 ms. No LLM Judge is configured for the artifact
runtime (`factory.py` constructs `CoverageJudge()` → `DeterministicContextJudge`;
`JUDGE_MODEL` is defined in `config.py` but never consumed by the runtime).

---

## 5. LLM call analysis

Aggregate across 15 recorded live calls:

| Purpose | n | latency min / median / max | prompt chars median | completion tokens median | status |
|---|---:|---|---:|---:|---|
| semantic | 5 | 43.1 s / **100.9 s** / 110.0 s | 1,262 | 2,143 | 4 ok, 1 capped |
| planning | 8 | 74.7 s / **110.0 s** / 110.0 s | 12,056 | — (7 capped) | 1 ok, 7 capped |
| response | 2 | 14.2 s / 17.5 s / 20.8 s | 1,221 | 593 | 2 ok |

Uncapped evidence (same configured model/provider):

| Call | Duration | Prompt tokens | Completion tokens | Prompt chars |
|---|---:|---:|---:|---:|
| tiny controlled probe | 2.74 s | 88 | 12 | 21 |
| semantic | 92.4 s | 383 | 3,351 | 1,262 |
| planner initial | **710.8 s** | 3,341 | 15,856 | 11,374 |
| planner replan | 368.7 s | 3,675 | 11,895 | 12,670 |

**Diagnosis:** latency correlates with **completion length**, not prompt size. The model
emits thousands of (hidden-reasoning) completion tokens per call. The planner prompt is
~11–13 k chars (~2.9–3.4 k prompt tokens) and is not itself the bottleneck. No provider
retries were observed; `OpenAICompatibleProvider` performs exactly one HTTP call. The
`finish_reason` was `stop` for successful calls.

**Timeout behaviour finding:** the production `timeout` (60 s) is passed to the OpenAI SDK,
which is an httpx *per-operation* timeout, not a total-call deadline. Calls of 92.4 s,
368.7 s and 710.8 s all ran to completion under a nominal `timeout=60.0`. There is no
effective upper bound on a single model call.

---

## 6. Planner convergence context

`PlannerContext` composition is **static per runtime** and does not grow with conversation
history or artifact count in the observed runs:

| Section | Item count | Rendered chars | ~tokens (chars/4) |
|---|---:|---:|---:|
| CapabilityViews | 8 | 1,894 | ~475 |
| SchemaTableViews | 4 tables | 4,279 | ~1,070 |
| Available exports | 0 at initial plan; 1–3 after work | 39 (empty) → ~200 | ~10 → ~50 |
| AttemptViews / PlannerFeedback | 0 at initial plan; grows after failures | 23 → 175–800 | ~6 → ~200 |
| Raw query / goal / constraints | 1 | ~100–300 | ~25–75 |
| Render total (initial) | | **~6,235** | **~1,560** |

Full live planner prompts measured **11,022–13,197 chars** (template + goal + constraints +
the above).

**What is supplied:**
* **All schema information**: yes — all 4 catalog tables and every field are rendered
  (`render_schema`, 4,279 chars). No relevance selection is applied.
* **All historical artifacts**: only artifacts in the *current* conversation, but all of
  them and all their exports (`export_views`), with per-export scope/verification detail.
* **All tool outcomes**: yes — the last 12 `AttemptView`s are rendered
  (`render_feedback(max_attempts=12)`), plus gaps (max 8) and obligations.
* **Schema-only-relevant info**: no. The v0.5 "convergence surface" is comprehensive rather
  than selective. Deterministic Python construction is cheap (`planner_context` ≈ 0.7–1.1 ms);
  the cost is prompt tokens, not construction.

---

## 7. Actual number of LLM calls per user turn

Chain per turn (artifact runtime):

```
semantic interpretation (LLM)            1     always (unless scripted)
planner.initial_needs (LLM)              1     always
planner.add_needs (LLM)                  0..6  once per planner-loop iteration with gaps
response composer (LLM)                  0..1  only when claims exist
Judge (LLM)                              0     deterministic judge only
------------------------------------------------
total                                    1..9
```

Observed: **1** (clarification), **3** (web unavailable), **4** (entity ambiguity; capped
fallbacks), **4** (follow-up turn 1), **3** (follow-up turn 2). Uncapped, the simple query
reached 3 planner calls (initial + 2 replans) before the audit budget, with more loop
iterations still available (`_MAX_ITERATIONS = 6`).

---

## 8. Tool-by-Tool dossier (actual `ToolRegistry`)

Source: `tool_registry.json` (inspected from the product composition root).

| Tool | Accepts | Produces | Availability / Authority | Implementation | Underlying dependency | SSRF-guarded? |
|---|---|---|---|---|---|---|
| `shared_knowledge` | SEARCH_QUERY | KNOWLEDGE_CANDIDATE | AVAILABLE / AUTHORITATIVE | `tools_evidence.KnowledgeTool` | local SQLite knowledge store | n/a |
| `web_research` | SEARCH_QUERY, ENTITY_CONTEXT, STATISTICAL_RESULT, ANOMALY_SIGNAL, DERIVED_MEASURE | WEB_EVIDENCE | **CONFIG_REQUIRED** / OBSERVED | `tools_evidence.WebResearchTool` | DuckDuckGo Lite → Bing; `PageReader` | **yes** |
| `entity_resolution` | ENTITY_MENTION | ENTITY_MAPPING, PLAYER_ID_SET | AVAILABLE / DERIVED | `tools_evidence.EntityResolutionTool` | local dictionary → **statsapi.mlb.com people search** → evidence scan | **no** |
| `evidence_entities` | WEB_EVIDENCE, KNOWLEDGE_CANDIDATE | ENTITY_MAPPING, PLAYER_ID_SET | AVAILABLE / DERIVED | `tools_evidence.EvidenceEntityTool` | `EntityLookup` (+ statsapi for English names) | **no** |
| `roster` | TEAM_NAME, SEARCH_QUERY | TEAM_ROSTER, PLAYER_ID_SET | AVAILABLE / AUTHORITATIVE_ACTIVE_ONLY | `tools_evidence.RosterTool` | **statsapi.mlb.com** `/teams`, `/teams/{id}/roster?rosterType=active` | **no** |
| `batting_stats` | PLAYER_NAME, PLAYER_ID_SET, SEASON, DATE_RANGE | STATISTICAL_RESULT, RANKED_ENTITY_SET | AVAILABLE / SOURCE_BACKED | `tools_analytics.BattingTool` | pybaseball / Baseball Reference | **no** |
| `local_analytics` | PLAYER_ID_SET, DATE_RANGE, ANALYTICAL_QUERY | STATISTICAL_RESULT, RANKED_ENTITY_SET, DERIVED_MEASURE | AVAILABLE / DERIVED | `tools_analytics.LocalAnalyticsTool` | Safe IR → catalog → SQL → DuckDB (PARQUET) / PostgreSQL | n/a (SQL guard) |
| `compute` | STATISTICAL_RESULT, DERIVED_MEASURE, RANKED_ENTITY_SET | DERIVED_MEASURE, STATISTICAL_RESULT, RANKED_ENTITY_SET | AVAILABLE / DERIVED | `tools_analytics.ComputeTool` | pure Python | n/a |

Observed per-tool runtime (all runs; durations are milliseconds):

| Tool | n | outcome codes | median ms | exports produced |
|---|---:|---|---:|---|
| `local_analytics` | 41 | SUCCESS 26, UNKNOWN_FIELD 9, EMPTY_RESULT 3, INTERNAL_FAILURE 3 | 18.40 | STATISTICAL_RESULT, RANKED_ENTITY_SET, DERIVED_MEASURE |
| `web_research` | 16 | SUCCESS 8, SOURCE_TRANSIENT 8 | 6.92 | WEB_EVIDENCE |
| `shared_knowledge` | 7 | SUCCESS 7 | 101.00 | KNOWLEDGE_CANDIDATE |
| `entity_resolution` | 8 | SUCCESS 5, IDENTITY_AMBIGUOUS 3 | 0.82 | ENTITY_MAPPING, PLAYER_ID_SET |
| `evidence_entities` | 5 | SUCCESS 5 | 0.54 | ENTITY_MAPPING, PLAYER_ID_SET |
| `compute` | 3 | SUCCESS 3 | 0.13 | DERIVED_MEASURE |
| `roster` | 3 | SUCCESS 3 | 0.13 | TEAM_ROSTER, PLAYER_ID_SET |
| `batting_stats` | 3 | SUCCESS 3 | 0.23 | STATISTICAL_RESULT, RANKED_ENTITY_SET |
| `ghost_capability` (unregistered) | 3 | UNSUPPORTED_CAPABILITY 3 | 0.00 | — |

### 8.1 What each Tool actually receives and returns (traced)

* **`local_analytics`** receives `{"analytical_query": <Safe IR>, "start", "end"}` plus
  explicit binding refs. It returns an `analytics` artifact whose `structured_data` holds
  `columns`, `rows`, `source_kind`, `sql`, `ir_digest`; exports `STATISTICAL_RESULT`
  (`{"columns","rows"}`), `RANKED_ENTITY_SET` (id/name/value) when an identifier group key
  exists, and `DERIVED_MEASURE` when a derived selection exists.
* **`web_research`** receives `{"query"}` (+ bound upstream values appended to the query)
  and returns a `web_evidence` artifact with `findings` (url/title/snippet/grounded/
  accepted/evidence_span), `text_content` containing **only grounded page spans**, and a
  `WEB_EVIDENCE` export. Mixed grounded/ungrounded results are `PARTIAL`.
* **`shared_knowledge`** receives `{"query", "as_of"?, "statuses"?}` and returns a
  `knowledge` artifact with `matches` (knowledge_id/type/title/summary/authority/score) and
  a `KNOWLEDGE_CANDIDATE` export; ACTIVE-only by default.
* **`entity_resolution`** receives `{"mentions"}` and returns an `entity_mapping` artifact
  with `mapping`, `ambiguous`, `unresolved`, plus `ENTITY_MAPPING` and `PLAYER_ID_SET`.
* **`evidence_entities`** receives `{"focus"}` + upstream evidence refs and returns an
  `entity_mapping` with focus-directed, span-grounded players, preserving ambiguity.
* **`roster`** receives `{"team"}` and returns a `team_roster` artifact (active roster)
  with `TEAM_ROSTER` and `PLAYER_ID_SET`.
* **`batting_stats`** receives `{"season"|"start"/"end", "metric", "names"?, "player_ids"?}`
  and returns a `batting_stats` artifact with `STATISTICAL_RESULT` and `RANKED_ENTITY_SET`.
* **`compute`** receives `{"op", "left_ref"/"right_ref"/"field"/"label"|"input_ref"}` and
  returns a `derived`/`ranked` artifact exporting `DERIVED_MEASURE`/`RANKED_ENTITY_SET`.

---

## 9. `local_analytics` detailed trace

For every `local_analytics` call the harness recorded the IR, resolved fields, compiled
SQL, guard result, DB timing, row count and outputs. Representative:

| Phase | Median | Notes |
|---|---:|---|
| IR validation (`AnalyticalQuery.model_validate`) | included in `tool_execute` | exact-error on malformed IR |
| `ir_compile` (catalog + SQL) | **0.14–0.21 ms** | deterministic compiler |
| `sql_guard` (AST read-only) | **1.3–2.1 ms** | allowlist/read-only check |
| `db_execute` (DuckDB Parquet) | **17.2–21.1 ms** | dominant local component |
| artifact construction/exports | < 0.1 ms | |
| Judge / verification | < 0.5 ms | |

Concrete compiled SQL (PARQUET, windowed, safe):
```sql
SELECT pitcher AS pitcher, COUNT(*) AS pitches
FROM read_parquet('.../mlb_statcast_*.parquet')
WHERE (game_date BETWEEN DATE '2021-06-01' AND DATE '2021-06-30')
GROUP BY pitcher ORDER BY pitches DESC LIMIT 5
```
Composed entity-set SQL (roster → analytics, real value flow):
```sql
... WHERE (batter IN (SELECT ...)) ...
```
IR planning/validation/compile is negligible; **DB execution is essentially the whole
`local_analytics` cost**, and even that is ~20 ms against an 8-file Parquet archive with a
cold in-memory DuckDB per call.

Empty result (`2021-01-01..2021-01-05`) correctly returned `EMPTY_RESULT` → `VALID_EMPTY`,
artifact `EMPTY`, Judge `UNSATISFIED`. Invalid field (`invented_velocity`) returned
`UNKNOWN_FIELD` → `UNKNOWN_SCHEMA`, artifact `INVALID`. PostgreSQL (server down) returned
`INTERNAL_FAILURE` (see finding TOOL CONTRACT-1).

---

## 10. Roster / entity / knowledge traces

* **Entity resolution** (`Aaron Judge`, `Bryce Harper`): resolved locally in **0.7 ms**;
  `PLAYER_ID_SET` and `ENTITY_MAPPING` produced. Scope verification recorded
  `entity: MISMATCH` (requested mention surface vs canonical key) while the Judge returned
  `SATISFIED` — see finding OBSERVABILITY-2.
* **Entity ambiguity** (`Luis`): `IDENTITY_AMBIGUOUS` produced with both candidates
  preserved; **but the resolve path made an unguarded live `statsapi.mlb.com` people-search
  call**, adding **~1.0–1.4 s** (98% of the turn). The MLB registry lookup is not behind
  the SSRF guard.
* **Roster**: fake provider in deterministic runs (0.13 ms). In the live product the same
  path calls `statsapi.mlb.com` over the network; the guard is **not** applied there, so it
  succeeds through the same fake-IP proxy that blocks the guarded web tool.
* **Shared knowledge**: returned ACTIVE entries and a `KNOWLEDGE_CANDIDATE` in ~99 ms;
  cProfile shows the cost is **43,100 `_mentions()` calls and ~39,780 `re.compile()`
  invocations per 20 searches** (dynamic regex recompilation), not SQLite I/O or ranking.

---

## 11. Compute trace

`local_analytics` produced `DERIVED_MEASURE` (`hard_rate = 100·hard/n`) and a bound
`STATISTICAL_RESULT`; the downstream `compute` Need (op `MEAN`) consumed the bound exports
and produced a new `derived` artifact exporting `DERIVED_MEASURE` in **0.10–0.13 ms**.
Values flow from the upstream product (the derived export value is the numeric input); the
linkage is not lineage-only. Both Needs were `SATISFIED`; the turn was `COMPLETE`.

---

## 12. Artifact production (what each Tool genuinely outputs)

| Scenario | Tool | Artifact kind / status | Exports | confidence |
|---|---|---|---|---|
| local_count_parquet | local_analytics | analytics / OK | STATISTICAL_RESULT, RANKED_ENTITY_SET | 0.85 |
| local_derived_compute | local_analytics | analytics / OK | + DERIVED_MEASURE | 0.85 |
| local_derived_compute | compute | derived / OK | DERIVED_MEASURE | 0.80 |
| roster_then_local | roster | team_roster / OK | TEAM_ROSTER, PLAYER_ID_SET | 0.95 |
| roster_then_local | local_analytics | analytics / OK | STATISTICAL_RESULT, RANKED_ENTITY_SET | 0.85 |
| knowledge | shared_knowledge | knowledge / OK | KNOWLEDGE_CANDIDATE | 0.70 |
| entity_resolution | entity_resolution | entity_mapping / OK | ENTITY_MAPPING, PLAYER_ID_SET | 0.80 |
| entity_ambiguity | entity_resolution | entity_mapping / PARTIAL | ENTITY_MAPPING | 0.00 |
| batting_stats | batting_stats | batting_stats / OK | STATISTICAL_RESULT, RANKED_ENTITY_SET | 0.80 |
| web_fake_grounded | web_research | web_evidence / OK | WEB_EVIDENCE | 0.60 |
| web_then_entities | web_research → evidence_entities | web_evidence / OK → entity_mapping / OK | WEB_EVIDENCE → ENTITY_MAPPING, PLAYER_ID_SET | 0.6 |
| web_to_db | web_research → evidence_entities → local_analytics | … analytics / OK | … STATISTICAL_RESULT, RANKED_ENTITY_SET | 0.85 |
| invalid_ir | local_analytics | analytical_diagnostic / INVALID | — | 0.00 |
| empty_result | local_analytics | analytics / EMPTY | STATISTICAL_RESULT, RANKED_ENTITY_SET | 0.20 |
| postgres_down | local_analytics | (no artifact) | — | — |

Note: entity-oriented tools declare `PLAYER_ID_SET` only when at least one numeric id is
resolved; the ambiguity case correctly produced no `PLAYER_ID_SET`.

---

## 13. Verification / Judge / State feedback path

Exact safe feedback path traced per Tool execution:

```
ToolOutcome (recovery_code, detail, artifacts)
  → Need.unsatisfied_inputs += code
  → Artifact (status OK/PARTIAL/EMPTY/INVALID)
  → ScopeVerification per dimension (deterministic, from execution receipt)
  → CoverageJudge.assess_need (deterministic + DeterministicContextJudge; may downgrade)
  → StateProjector.project_need / project_goal (terminal truth)
  → PlannerFeedback.attempts (AttemptView: outcome, failure class, retryable, hint)
  → next Planner action (initial prompt / add_needs prompt)
```

What survives: outcome code, failure class, retryability, bounded detail, artifact ids,
scope-verification statuses, obligation coverage, gaps, conflicts, unavailable
capabilities, budget. What is lost: raw tool payloads and the tool's internal exception
message (bounded to 300–500 chars); the Planner sees a classification, not the raw error.
Whether the Planner sees the important reason: yes for typed reasons
(`UNKNOWN_FIELD`, `INPUT_INCOMPATIBLE`, `IDENTITY_AMBIGUOUS`, `SOURCE_TRANSIENT`), which are
rendered into the replan prompt; but see ORCHESTRATION-1 (the loop may not reach replanning
at all).

---

## 14. Web / network diagnosis (layered)

Evidence in `web_diagnostics.json`.

| Layer | Result |
|---|---|
| Proxy env | **none set** (`HTTP(S)_PROXY`/`ALL_PROXY`/`NO_PROXY` all unset) — interception is transparent |
| `/etc/resolv.conf` | `nameserver 10.255.255.254` (WSL-generated) |
| DNS (shell `getent` and Python `getaddrinfo` **agree**) | `lite.duckduckgo.com→198.18.0.95`, `html.duckduckgo.com→198.18.0.96`, `www.bing.com→198.18.0.89`, `statsapi.mlb.com→198.18.0.83`, `api.deepseek.com→198.18.0.31`, `example.com→198.18.0.110` |
| Address class | all `198.18.0.0/15`, `ipaddress.is_private=True`, `is_reserved=False`, `is_global=False` (RFC 2544 benchmarking range; Clash/Surge *fake-IP* pattern) |
| TCP connect (raw) | succeeds to all hosts in **5–7 ms**, peer = the fake-IP |
| HTTP GET **without the app guard** | `example.com` 200/559 B; DDG Lite 200/22,287 B; Bing 200/112,686 B; StatsAPI 200/23,219 B |
| App SSRF guard (`_host_is_safe`) | `False` for **every** public host above; `True` only for genuinely private/loopback test URLs |
| `WebResearchTool.search` | fails in 58.75 ms (guard refuses before connecting) |
| `PageReader.read("https://example.com/")` | returns `""` (guard refuses the initial URL) |
| `WebResearchTool.research` | fails; `WEB_RESEARCH_UNAVAILABLE` → `SOURCE_TRANSIENT` |

**First failing boundary:** `SSRF_POLICY_BLOCK_ON_RESERVED_RESOLUTION` — the SSRF guard
refuses a public hostname because the environment's transparent proxy rewrites DNS to a
private/benchmarking range. **Search itself works** (HTTP 200, parseable HTML) when the
guard is bypassed. **Page retrieval works** at the network layer but is refused by the
guard.

### 14.1 Retries / timeouts on the Web path

* No nested retries: `WebResearchTool.search` tries 2 DDG URLs then Bing — but each is
  refused by the guard in ~1 ms, so this is not a latency multiplier here. Total observed
  16–145 ms.
* `_Transport.timeout = 15 s`; `MLBTeamRosterProvider.timeout = 12 s`; people-search 12 s.
* `requests` itself has no configured retry adapter.

### 14.2 Separate search / page / extraction measurements

| Step | Result |
|---|---|
| Search request latency (guard-off) | ~0.9–1.3 s: DDG 200/22 KB, Bing 200/113 KB |
| Number of search hits | not reached in-product (search refused pre-network) |
| Page fetch latency (guard-off) | not exercised in-product; `PageReader` refuses pre-fetch |
| Pages fetched / blocked / failed | 0 fetched, all blocked by SSRF |
| Evidence-span extraction | not reached |
| User-latency cause | **model interpretation** and **SSRF block**, not search/fetch/retries |

---

## 15. Reserved-address behaviour (#12 investigation)

* **Still true.** Public hostnames resolve to `198.18.0.x`, which `ipaddress` classifies as
  private; the guard rejects them.
* **Hostname:** `lite.duckduckgo.com` → `198.18.0.95` (same for Bing/StatsAPI).
* **Resolver path:** `socket.getaddrinfo` inside `_host_is_safe` (and `_Transport.get`).
* **Shell vs Python:** identical (`getent ahosts` and Python return the same addresses).
* **Proxy config effect:** no env proxy; a transparent resolver/proxy is rewriting DNS.
* **Search vs PageReader:** both use `_Transport` + `_host_is_safe` (same path).
* **Distinguishing environment vs application:** the environment is the cause — the real
  network is reachable and returns 200 through the same addresses, and the guard's decision
  is correct given a private-looking resolution. The application *cannot* distinguish a
  fake-IP-for-public from a genuinely-private host, so it errs safe; that is defensible.
  The application-level inconsistency is that **other network paths bypass the guard
  entirely** (statsapi people-search, roster, batting, LLM), so the same environment both
  blocks and allows network access depending on which module initiates it.

---

## 16. Recovery behaviour

E.g. `recovery_with_obligation` (invalid IR, then a corrected need):

```
What failed        local_analytics INVALID_IR → UNKNOWN_FIELD (failure class UNKNOWN_SCHEMA)
Retryable          no (requires_replan)
Affected obligation SEASON (2021) — uncovered
Partial evidence   none (artifact INVALID)
Capabilities left  local_analytics (still available), knowledge, web, etc.
Planner feedback   AttemptView(UNKNOWN_FIELD/UNKNOWN_SCHEMA, hint "choose a catalog field…")
Next action        add_needs → new Need need-local-fixed (valid field)
Runtime did        created another Need, executed it, produced STATISTICAL_RESULT
Final state        LIMITED (recovered evidence; obligation not reflected in satisfies_obligations)
```

Observed recovery inventory:

| Failure | Runtime action |
|---|---|
| Unknown capability (`ghost_capability`) | attempt recorded `UNSUPPORTED_CAPABILITY`, then a synthetic gap "does not produce usable evidence"; no blind retry; status FAILED |
| Invalid IR (`UNKNOWN_FIELD`) | replan triggered (when obligations exist) → corrected Need → SUCCESS |
| Web SSRF block (`SOURCE_TRANSIENT`) | classified retryable; **not** retried within the turn; turn FAILED/LIMITED |
| Empty result | `EMPTY_RESULT`/`VALID_EMPTY`; not treated as an error; Judge `UNSATISFIED`; FAILED |
| Ambiguous identity | `IDENTITY_AMBIGUOUS` preserved; no invented identity; FAILED |
| PostgreSQL down | `INTERNAL_FAILURE` (misclassified; see TOOL CONTRACT-1) |

**Answer to "what did the system actually do to solve the problem?"** For the recoverable
analytical error it created a new Need and re-executed a corrected Tool; for the web and
ambiguity failures it stopped with a truthful non-COMPLETE state. It never retried the same
failed action.

### 16.1 ORCHESTRATION-1 — closed core Need suppresses replanning

With a vague request (no extractable obligations), a structurally failed **CORE** analytical
Need is excluded from `missing` *before* the `recoverable` gate in
`CoverageJudge.summarize`, so `core_goal_supported` becomes `True`, the planner loop
breaks, `add_needs` is never called, and the turn ends FAILED without attempting a
corrected plan. Evidence: `recovery_vague_no_obligation` → 1 tool call, no
`planner_add_needs` phase; `recovery_with_obligation` → `planner_add_needs`, 2 tool calls,
recovered. This is the difference between "the planner chose not to recover" and "the
scheduler never asked it to".

---

## 17. Useless / duplicate work observed

* **Repeated full planner context construction** per loop iteration (0.7–1.1 ms each; cheap).
* **Whole-conversation snapshot persisted 3–7× per turn**, including a redundant
  pre-side-effect snapshot and a post-tool snapshot; median payload 8.7 KB, max 36 KB.
* **Replan LLM calls that duplicate most of the initial prompt** (~11 k → ~12–13 k chars)
  and re-serialize the same capabilities/schema.
* **SSRF-blocked web retry across 2 DDG backends + Bing** for a known policy block
  (16–145 ms; small but pure waste).
* **Live semantic clarification on a simple query** ("How many pitches…" → question about
  the Pittsburgh *Pirates*, an entity never mentioned), consuming ~100 s and returning no
  answer; the clarification is then re-run on the next turn.
* **Judge re-evaluation** of unchanged evidence on `_refresh_assessments` (sub-ms).
* **Response composer invoked when claims exist** but the answer is already known; the
  LLM call adds 14–21 s.
* **Entity ambiguity makes an unnecessary live MLB registry call** (~1.3 s) even though the
  local dictionary already returned ambiguous candidates.

None of these were optimized.

---

## 18. Clarification-turn behaviour

* Deterministic: turn 1 = 4.0 ms (`semantic` + persist only) → `WAITING_FOR_USER`.
  Turn 2 = 37.5 ms and **re-runs** semantic interpretation, goal reconstruction, planner
  context, and tool execution. It does **not** resume a cheap partial plan.
* Live: turn 1 = **100.9 s** (1 semantic call, then clarification, no planning);
  the follow-up turn 2 = **263.2 s** with a new semantic call (43.1 s) and two capped
  planner calls. So a short clarification/follow-up answer triggers **full semantic
  re-interpretation and full Planner reconstruction**, not state resumption.

---

## 19. Critical path

For the live product the critical path is a **serial chain of model calls**:
`semantic → planner.initial_needs → (planner.add_needs → tool → judge → …)×N → response`.
Tools, Judge, verification, persistence and DB are off the critical path in relative terms
(tens of milliseconds vs tens-to-hundreds of seconds). For the offline product the critical
path is `DuckDB execution ≈ persistence > orchestration`.

---

## 20. Persistence overhead

| Metric | Value |
|---|---:|
| `store_save` calls | 300 (69 turns) ≈ 4.3/turn (range 3–7) |
| payload size | median 8,745 chars; max 36,084 |
| per-save latency | median 4.6 ms; max 14.7 ms |
| per-turn persistence | 10–44 ms |
| storage | SQLite (`SqliteOperationalStore`), `runtime_conversation` JSON snapshot |

Persistence is real but **not material to user latency** on the live path (0.01–0.04 s of a
100–350 s turn). It is material for the offline path (~30–50% of a 20–80 ms turn). A
`PERSISTENCE_ERROR` is surfaced to the event journal, not swallowed.

---

## 21. Schema / convergence overhead

Python construction of the convergence surface (`planner_context`) is **0.7–1.1 ms** and is
not a bottleneck. The serialized prompt cost is ~6,235 chars (~1.5 k tokens) of the
~11–13 k-char planner prompt. The LLM *processing* cost of that context is the expensive
part and is not separable from completion latency in this configuration.

---

## 22. Findings

Legend: severity = blocker/high/medium/low. Category per the requested taxonomy.

| # | Category | Severity | Finding | Evidence |
|---|---|---|---|---|
| 1 | MODEL PROVIDER | **blocker** | Planner completion of 710.8 s / 15,856 completion tokens; a simple query issues 3+ sequential planner calls. This dominates all user latency. | `live_uncapped_partial.log` |
| 2 | MODEL PROVIDER | **high** | Provider `timeout` (60 s) is an httpx per-operation timeout, not a total-call deadline; 92 s / 369 s / 711 s calls all complete under `timeout=60`. | `llm_calls.jsonl`, direct probe |
| 3 | MODEL PROVIDER | **high** | Semantic layer returned a clarification about the **Pittsburgh Pirates** for a query containing no such entity, consuming ~100 s and no answer. | `live_clarification.json` |
| 4 | MODEL PROVIDER | medium | Semantic call emits ~2,100–3,400 completion tokens for ~400-token answers (hidden reasoning), inflating latency ~30×. | `llm_calls.jsonl` |
| 5 | RECOVERY | **high** | Structurally failed CORE Need is excluded from `missing` before the `recoverable` gate, so `core_goal_supported=True`, the loop breaks, and `add_needs` is never called when no obligations are extractable. | `recovery_vague_no_obligation` vs `recovery_with_obligation` |
| 6 | NETWORK | **high** | Web search is blocked before the network by the SSRF guard because DNS resolves public hosts to `198.18.0.0/15`; first boundary `SSRF_POLICY_BLOCK_ON_RESERVED_RESOLUTION`. | `web_diagnostics.json` |
| 7 | NETWORK | medium | The same environment is reachable without the guard (HTTP 200); the app uses **inconsistent** network paths — `entity_resolution`/`roster`/`batting`/LLM bypass the guard, `web_research`/`PageReader` enforce it. | `web_diagnostics.json` |
| 8 | TOOL CONTRACT | **high** | PostgreSQL connection failure yields `ToolOutcome(recovery_code="OperationalError")`, which the taxonomy maps to `INTERNAL_FAILURE` instead of `SOURCE_TRANSIENT`/`RETRYABLE_SOURCE`. | `postgres_down` (3/3) |
| 9 | PERFORMANCE | medium | `shared_knowledge` costs ~99 ms, ~95% of it regex recompilation in `KnowledgeRetriever._mentions`. | cProfile (43,100 `_mentions`, 39,780 `re.compile` per 20 searches) |
| 10 | PERFORMANCE | medium | Ambiguous entity resolution triggers an unguarded live `statsapi.mlb.com` people-search (~1.3 s) even though the local dictionary already returned candidates. | `entity_ambiguity` (987.7 ms median) |
| 11 | OBSERVABILITY | medium | `verify_artifact_scope` is called with `need.required_scope or artifact.requested_scope`, but the Judge's `verified_verdict` uses only `need.required_scope`; when a Need has no required scope they disagree (stored `entity:MISMATCH` vs Judge `SATISFIED`). | `entity_resolution` rows |
| 12 | OBSERVABILITY | **high** | The artifact runtime has **no timing instrumentation**: `EventJournal` records no durations, `RunMetrics.duration_ms` is never wired into this runtime. Per-stage latency required external wrappers. | `events.py`, `metrics.py`, `engine.py` |
| 13 | ORCHESTRATION | medium | Clarification/follow-up turns re-run full semantic interpretation and full Planner reconstruction; no cheap state resumption. | `clarification_turn` t1 vs t2; `live_followup` t1 vs t2 |
| 14 | ORCHESTRATION | medium | Up to 3–7 whole-conversation snapshots persisted per turn (median 8.7 KB), including redundant pre-tool writes. | `notes.jsonl` `store_save` |
| 15 | PERFORMANCE | low | Deterministic response composer is invoked even when the answer is already known; when it is the LLM composer it adds 14–21 s. | `live_*` response calls |
| 16 | NETWORK | low | Web tool tries 2 DDG URLs + Bing for a known SSRF policy block (16–145 ms pure waste). | `tool_calls.jsonl` |
| 17 | TOOL CONTRACT | low | `roster` returns PARTIAL scope for population by design, so a successful roster never renders the whole turn COMPLETE without other evidence. | `roster_then_local` |
| 18 | OBSERVABILITY | low | `PlannerContext` has no section timings and no explicit "which artifacts/attempts were selected and why" record; selection behavior is only inferable from rendered sizes. | `convergence.py` |

---

## 23. Optimization opportunities (recommendations only — NOT implemented)

Ranked by expected latency impact × correctness risk × architectural risk × complexity.

| Rank | Opportunity | Expected impact | Correctness risk | Architectural risk | Complexity |
|---|---|---|---|---|---|
| 1 | **Bound total model-call time** (real total-deadline, not httpx per-op) and fail over to the existing deterministic fallbacks. | Very high (caps 100–700 s calls) | Low (fallback policy already exists) | Low | Low |
| 2 | **Reduce planner `add_needs` amplification**: require a material plan change before another LLM replan; add a replan budget that is not "one call per loop iteration". | Very high (fewer 6–12 min calls) | Medium (must not block genuine recovery) | Medium | Medium |
| 3 | **Cap/stream completion length** for semantic/planner/composer (e.g., request compact output; treat over-long output as a parse failure). | High | Medium | Low | Low |
| 4 | **Fix the closed-Core-Need gate** so `recoverable` actually guards the exclusion of closed Needs (deterministic; no security impact). | Medium (enables recovery) | Medium | Low | Low |
| 5 | **Cache `_mentions` regexes** (or precompute token sets) in knowledge retrieval. | Medium for knowledge turns (~95 ms) | Low | Low | Low |
| 6 | **Make the entity-ambiguity path local-first** and only consult the network when the local dictionary is empty. | Medium (~1.3 s/turn) | Low | Low | Low |
| 7 | **Fix the executor error-code taxonomy** so source/connection failures map to `SOURCE_TRANSIENT`. | Medium (correct recovery) | Low | Low | Low |
| 8 | **Add native per-phase timing to the artifact runtime** (EventJournal durations or a metrics sink). | Observability, not latency | Low | Low | Medium |
| 9 | **Reduce persistence writes per turn** (one checkpoint per durable transition; avoid redundant pre-tool snapshot). | Low–Medium offline | Medium | Medium | Medium |
| 10 | **Reconcile engine scope verification with Judge verdict input** (single source of truth for `requested`). | Correctness | Medium | Medium | Low |

**Deterministic, low-risk (no correctness/Judge/security regression):** 1, 5, 6, 7, 8, and
the persistence/`_mentions` items.
**Risk of regressing Judge/scope/security guarantees:** 2, 3, 4, 10 — these touch the
recovery/verification contract and need explicit invariant tests.

---

## 24. Recommended next development round (described, not performed)

1. Add a **total-deadline provider wrapper** with provider fallback metrics, keeping the
   existing `ProviderTimeout` semantics, and measure the fallback rate.
2. Bound planner **replanning** to materially different plans and record, per turn, why a
   replan happened (deterministic, no chain-of-thought).
3. Add **first-class timing to `EventJournal`/metrics** so per-phase latency is visible
   without external wrappers.
4. Fix the **closed-Core-Need / `recoverable` gate** with property tests that assert a
   failed CORE Need does not yield `core_goal_supported` without verified evidence.
5. Normalize **source-failure codes** across executors and tools.
6. Make knowledge retrieval **regex-cache-correct** and entity ambiguity **network-optional**.
7. Keep the **SSRF guard unchanged**; record the fake-IP environment as an operator
   concern, and document the deliberate inconsistency (guarded web vs unguarded registry
   calls) for a future decision — not in this audit.

---

## 25. Direct answers to the required questions

1. **Per-phase duration?** See §4. Non-LLM phases are 0.0–44 ms; LLM phases are 14–711 s.
2. **% of latency from LLM?** ~99%+ on the live path; ~0% in deterministic runs.
3. **Model calls in simple/clarification/analytical/multi-tool turns?** 1 / 3 / 4 / 4
   observed (plus 0–6 replans).
4. **Slowest LLM call?** The planner `initial_needs`/`add_needs` (up to 710.8 s).
5. **Planner context size / dominant section?** ~6,235 chars initial render; schema views
   dominate (4,279 chars), then capabilities (1,894).
6. **Does clarification resume state?** No; it re-runs semantic interpretation and planning.
7. **What does every Tool do internally?** §8.
8. **What does every Tool receive?** §8.1.
9. **What does every Tool return?** §8.1 / §12.
10. **Which returned data becomes exports?** §12.
11. **Which outputs are rejected by verification/Judge?** INVALID IR diagnostics, EMPTY
    analytics, ambiguous-only entity mappings, roster population PARTIAL, the web SSRF
    failure.
12. **Do rejected tool calls consume latency?** Negligible (<1 ms – 1.3 s), except the
    ambiguity network call.
13. **`local_analytics` time split?** DB ≈ 17–21 ms; guard 1–2 ms; compile 0.1–0.2 ms;
    post-processing < 0.1 ms.
14. **Is PostgreSQL/DuckDB material?** DuckDB (Parquet) ~20 ms/call — small; PostgreSQL was
    down and yielded a misclassified failure.
15. **Exact network step causing Web failure?** SSRF guard refusing fake-IP private
    addresses before connection.
16. **Is Web search working?** Yes at the network layer (HTTP 200); blocked by the guard.
17. **Is page retrieval working?** Network yes; blocked by the guard in-product.
18. **DNS differences between paths?** Shell, Python, search and PageReader agree; only the
    guard decision differs from the unguarded `requests` paths.
19. **Is the SSRF guard correctly blocking an environment anomaly?** Yes — the environment
    is the cause; the app's other network paths are inconsistently unguarded.
20. **Are retries/timeouts multiplying Web latency?** No; 16–145 ms total.
21. **Does ToolOutcome preserve enough for recovery?** Typed reason yes; raw detail is
    bounded. The larger gap is ORCHESTRATION-1 (replanning may not run).
22. **After a Tool fails, what does the runtime do?** Records a durable attempt, classifies,
    emits a gap, and either replans (if obligations exist) or stops truthfully.
23. **Does recovery invoke another LLM call?** Yes — `add_needs` is a planner LLM call.
24. **Are impossible actions retried?** No; `UNSUPPORTED_CAPABILITY` is not re-proposed.
25. **Useless work?** §17.
26. **Response composition latency after the answer is known?** 14–21 s (LLM) or <1 ms
    (deterministic).
27. **Persistence contribution?** 10–44 ms/turn offline; negligible live.
28. **Top five latency opportunities?** §23 ranks 1–5.
29. **Deterministic optimizations without weakening correctness?** §23 low-risk set.
30. **Which risks Judge/scope/security regression?** §23 medium/higher-risk set.

---

## 26. Observability gaps (recorded, not fixed)

* No per-stage timing in the artifact runtime (`EventJournal` has no durations; `RunMetrics`
  is unused by this runtime).
* `PlannerContext` records no section timings or selection rationale.
* Provider-level token usage exists (`ModelResponse.usage`) but is not persisted per call.
* The web path reports a generic `all web search backends failed` and loses the first
  SSRF refusal reason; the root cause is only visible by re-invoking `_host_is_safe`.
* `verify_artifact_scope` vs `verified_verdict` input mismatch is not surfaced anywhere.

## 27. Confirmation

No production module, prompt, schema, guard, role, tool, planner, Judge, persistence or
security boundary was modified. All changes are confined to
`docs/reviews/v05-runtime-performance/`. `main` remains at
`35b47c4174e98460ebdbcd2ace46adfba36225c4`.

`RUNTIME_PERFORMANCE_AUDIT_COMPLETE — READY_FOR_OPTIMIZATION_DESIGN`
