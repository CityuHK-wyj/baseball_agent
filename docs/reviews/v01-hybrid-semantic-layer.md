# Hybrid semantic layer — implementation report

Independent re-review: **FINAL_REVIEW_BLOCKED**. The claims below are historical
implementation claims; [the final hybrid review](v01-final-hybrid-review.md) records
real-model containment counterexamples despite passing representative cases.

Branch: `pi/v0.1-llm-semantic-parser`, created from
`codex/v0.1-semantic-recheck @ eae9875e86632f509b0351ea31da040ef151bc7e`.
The exact reviewed tree at the start is `64c1d4f3c7bed1e91b4b5fc8f0760e7179313662`.
`main` is untouched; no release or tag was created. The exact final SHA is reported in the
delivery message; a report cannot contain its own final tree hash.

This report is an implementation claim for independent re-review. It is not adoption or
release approval.

## Commits

| Commit | Purpose |
| --- | --- |
| `72867a8` | Exact compound count states and ownership-safe deterministic extraction |
| `7917bfc` | Constrained LLM semantic candidate, extractor and validator |
| `899a00e` | Route analytical semantics through the hybrid validator |
| `31fa09b` | Semantic evaluation corpus and compositional hybrid gate |
| `05928f9` | Rank-by phrasing and empty-result diagnostics; live compound E2E |
| `079da57` | Bounded semantic constraint summary in the trace |
| `delivery` | Live LLM semantic verification and the live-only defect fixes it exposed |

## Architecture

```
raw query
  -> deterministic date/year/literal pre-parse
  -> SemanticExtractor (deterministic OR LLM-backed)
       -> SemanticCandidate (closed, typed, evidence)
  -> deterministic SemanticValidator (authoritative)
       -> canonical typed constraints + SemanticProvenance
  -> Planner -> Router -> FieldMapping -> guarded read-only execution
  -> Assessment -> Response
```

The LLM never controls physical columns, SQL, source selection, permissions, artifact
acceptance, Judge vetoes or final validation. It emits only a `SemanticCandidate`.

### LLM provider / interface

`LLMSemanticExtractor` depends on the existing provider-agnostic `ModelProvider` Protocol
(`app/llm/provider.py`), not a vendor SDK. `OpenAICompatibleProvider` is unchanged and
still the only vendor-SDK importer. Credentials stay in the environment
(`DEEPSEEK_API_KEY`); no key is in source or committed. `build_pipeline(...,
llm_provider=...)` selects the LLM extractor; with no provider the deterministic
extractor is used.

### Structured output model

`app/models/semantic_candidate.py` defines `SemanticCandidate` with closed `kind`,
`operator`, `aggregation`, `direction`, `game_types` and `event_population` enums, exact
`CountState` lists, and `EvidenceSpan` (text plus optional offsets). `extra="forbid"`
means a model cannot smuggle unknown fields. `SemanticProvenance` records
`kind`/`key`/`evidence_text`/offsets/origin per canonical constraint.

The prompt (`SEMANTIC_PROMPT`) receives only bounded vocabulary: metrics, operators,
aggregations, directions, game types, event populations, location definitions, pitch
families, units and the count-state rule. It never receives schema, rows, artifacts or SQL
examples.

### Deterministic validation rules

- closed vocabulary / required fields per kind;
- evidence non-empty for explicit constraints and present in the query; offset match when
  supplied;
- evidence/metric compatibility: a qualification phrase (`BBE`, `batted ball`,
  `balls in play`) cannot ground a numeric metric, and exit/pitch velocity cues must
  agree with the claimed metric;
- numeric ownership: one evidence text/span cannot be claimed by two clauses with
  different meaning (`DUPLICATE_EVIDENCE_OWNERSHIP`);
- contradictions: multiple rankings/aggregations, conflicting count states, incompatible
  explicit populations, incompatible explicit locations;
- explicit population beats any inferred/default population;
- defaults are inserted only after validation confirms a dimension was absent;
- material ambiguity (for example `high fastball`) stays an ambiguity and enters the
  existing clarification lifecycle.

### Fallback behavior

- Provider/schema/ungrounded failures are *recoverable*: fall back to the deterministic
  extractor and record `fallback_reason`.
- Contradictions and reused numeric ownership are *unrecoverable*: fail closed into a
  MEANING clarification instead of silently choosing one meaning.
- If the fallback cannot prove an explicit analytics request, the parser fails closed with
  `SEMANTIC_UNAVAILABLE` rather than executing a weaker interpretation.
- A provider failure cannot make an explicit user constraint disappear.

### Live-only defects found and fixed

The first real provider calls (`deepseek-chat`) exposed four interface defects that the
mocked tests could not. All are fixed with regression coverage in
`tests/semantic/test_semantic_layer.py`:

1. The prompt never published the closed `origin` vocabulary, so the model emitted
   `explicit`/`user`. `SemanticVocabulary` now exposes `origins` and the prompt requires
   `USER_EXPLICIT`; the extractor also maps casual origin spellings onto the closed set.
2. The model emits every key with `null` for inapplicable fields, which the frozen tuple
   fields rejected. The extractor now drops `null` recursively, so absent and `null` mean
   the same default.
3. The prompt listed all candidate keys flat, so the model put a ranking metric in
   `metric` instead of `metric_key`. The prompt now names the applicable fields per kind;
   the extractor reuses an already-produced value under the correct field name.
4. Model-supplied character offsets drifted. `evidence.text` remains the grounding
   source; the extractor recomputes offsets from that text, and the validator still
   rejects any text not present in the query.

One repeated-run difference was a genuine semantic defect: the model sometimes proposed
`event_population = ALL_PITCHES` while ranking by exit velocity. Exit velocity is only
defined on contact, so `_check_contradictions` now rejects that combination recoverably
(`INCOMPATIBLE_EVENT_POPULATION`) and the deterministic extractor supplies the batted-ball
population. The validator also gives a rank request without a stated count the documented
`DEFAULT_RANKING_LIMIT`, matching the deterministic extractor.

## Exact compound count semantics

`CountConstraint` gains `states: tuple[CountState, ...]` (authoritative when present) and
an `exact_states` projection. `0-2 -> {(0,2)}`, generic `two strikes -> strikes=2` with all
ball counts, and `0-2 or 1-1 -> {(0,2),(1,1)}`. The SQL builder emits
`((balls = 0 AND strikes = 2) OR (balls = 1 AND strikes = 1))`, never
`balls IN (...)` combined with `strikes IN (...)`.

## Before / after — the five Codex P1 failures

`intent-trace.py` (Codex evidence) and the new
`docs/reviews/v01-semantic-hybrid-gate.py` both exit 0 with `unresolved_blockers=[]`.

| # | Query | Before | After |
| --- | --- | --- | --- |
| 1 | `top 5 by maximum exit velocity on fastballs at least 95 mph, at least 100 BBE in 2023` | added `release_speed >= 100` alongside 95 and `HAVING >= 100` | `release_speed >= 95.0` + `HAVING COUNT(*) >= 100` only |
| 2 | `top 5 by maximum exit velocity with >= 20 BBE in 2023` | added `launch_speed >= 20`, lost qualification (`HAVING >= 3`) | no numeric filter; `HAVING COUNT(*) >= 20` |
| 3 | `top 5 hitters facing pitch velocity >= 95 mph ranked by maximum exit velocity in 2023` | `ORDER BY AVG(release_speed)` | `ORDER BY MAX(launch_speed) DESC` |
| 4 | `top 5 by maximum exit velocity in exhibition games in 2023` | `game_type IN ('R')` | `game_type IN ('E', 'A')` |
| 5 | `top 5 by maximum exit velocity on 0-2 or 1-1 counts in 2023` | count dropped / widened | exact `((balls = 0 AND strikes = 2) OR (balls = 1 AND strikes = 1))` |

## Canonical semantics and SQL for one complex query

Query (real E2E, resolved through the location clarification to
`BATTER_RELATIVE_UPPER_EDGE`):

> During the 2025 regular season, on fastballs at least 95 mph in 0-2 or 1-1 counts near
> the batter-relative upper edge, rank hitters by maximum exit velocity, requiring at
> least 20 batted balls.

Canonical semantics (independently present, verified in
`docs/reviews/semantic-hybrid-evidence/live-hybrid-e2e.jsonl`):

- period: 2025-01-01..2025-12-31 (regular season);
- pitch velocity: `release_speed >= 95.0`;
- count states exactly `{(0,2),(1,1)}`;
- location: `BATTER_RELATIVE_UPPER_EDGE`;
- ranking: `exit_velocity`, aggregation `MAX`, direction `DESC`, limit 5;
- qualification: `min_batted_balls = 20`;
- population: `REGULAR_SEASON` fair batted balls.

Generated PostgreSQL SQL:

```sql
SELECT batter_id AS batter, COUNT(*) AS n,
       AVG(launch_speed) AS avg_metric, MAX(launch_speed) AS max_metric
FROM statcast_pitches
WHERE game_date >= DATE '2025-01-01' AND game_date <= DATE '2025-12-31'
  AND ((balls = 0 AND strikes = 2) OR (balls = 1 AND strikes = 1))
  AND release_speed >= 95.0
  AND pitch_type IN ('FF', 'SI', 'FC', 'FA')
  AND game_type IN ('R')
  AND description = 'hit_into_play'
  AND plate_z >= sz_top - 0.25 AND plate_z <= sz_top
  AND launch_speed IS NOT NULL
GROUP BY batter_id HAVING COUNT(*) >= 20
ORDER BY MAX(launch_speed) DESC LIMIT 5
```

The Parquet SQL is the same physical translation with `read_parquet(...)`, `batter`, and
the archive glob. Both were executed for real through the guarded executors.

## Real E2E results

| Query window | Source | Result |
| --- | --- | --- |
| 2025 compound | PostgreSQL | real executor EMPTY (zero qualifying hitters); tool reports zero rows, not a query failure; objective FAILED; no Top 5 fabricated |
| 2023 compound (historical equivalent) | Parquet | real executor EMPTY; objective FAILED; no fabricated Top 5 |
| 2023 vs 2024 compound routing | Parquet + PostgreSQL | two objectives, correctly routed per window, both empty |
| Relaxed 2023 count-union ranking | Parquet | COMPLETE with a real accepted product |

The empty narrow filters are a genuine data outcome, not a semantic or execution failure;
the adapter now distinguishes `EMPTY` from `SOURCE_QUERY_FAILED`.

## Verification status

- Regression suite: **479 tests, OK, no skips** (includes live PostgreSQL and Parquet
  integration when credentials/archive exist).
- `docs/reviews/v01-reproduce-blockers.py`: exit 0, `unresolved_blockers=[]`.
- `docs/reviews/semantic-recheck-evidence/intent-trace.py`: exit 0 after the mixed-count
  case accepts the now-supported exact union (the review explicitly allowed "represent the
  union or reject/clarify").
- `docs/reviews/v01-semantic-hybrid-gate.py`: exit 0; 21-case corpus plus the five P1
  cases through canonical requirement and generated SQL.
- `compileall`: OK. Secret scan (staged and tree): Ok. CLI workflows: PASS.
- Security, SQL-guard and persistence/restart regressions: green in the suite.
- PostgreSQL analytics, Parquet analytics, 2023-vs-2024 cross-source routing:
  LIVE_VERIFIED (see `live-hybrid-e2e.jsonl` and the integration tests).
- SQLite operational store, crash resume, Artifact reuse and frozen qualification
  thresholds: TESTED_OFFLINE/regression in the suite.
- Live LLM semantic extraction: **LIVE_VERIFIED** with a real `DEEPSEEK_API_KEY`
  (see `live-llm-semantic.jsonl` and `live-llm-semantic.py`). The provider is
  `OpenAICompatibleProvider` at `https://api.deepseek.com`; the recorded harness runs use
  `deepseek-chat`, plus a separate run of the configured default `deepseek-v4-pro` on the
  compound and ambiguous cases. Every required case below was accepted by the
  deterministic validator through the LLM extractor (`fallback_reason == ""`):

  | Case | Canonical result |
  | --- | --- |
  | fastballs >= 95, MAX EV, >= 100 BBE | `pitch_velocity GTE 95`; `exit_velocity/MAX/DESC`; `min_batted_balls 100` |
  | average EV, >= 20 BBE | no numeric filter; `exit_velocity/AVG/DESC`; `min_batted_balls 20` |
  | exhibition games, MAX EV | `game_types ('EXHIBITION',)`, no regular-season override |
  | 0-2 or 1-1 counts, MAX EV | exact states `{(0,2),(1,1)}`, no cartesian widening |
  | 2025 compound | `2025`, `REGULAR_SEASON`, `pitch_velocity GTE 95`, exact counts, `BATTER_RELATIVE_UPPER_EDGE`, `exit_velocity/MAX/DESC`, `min_batted_balls 20`, batted-ball population |

  The compound query was extracted three independent times; all three accepted outputs
  normalized to the same canonical meaning. `Show me hitters against high fastballs.`
  reached the normal CONSTRAINT clarification with the three defensible location options
  (`deepseek-v4-pro` produced the `location.upper_edge` ambiguity directly; `deepseek-chat`
  fell back deterministically after proposing an unfilled location, which still clarifies).
  No explicit constraint can be weakened by a provider failure: the mocked
  provider-error path still falls back to the deterministic extractor with all explicit
  constraints preserved (`test_provider_failure_falls_back_without_dropping_constraints`).

### Provider latency observation

The configured default model `deepseek-v4-pro` answers the semantic prompt in roughly
44 seconds, above the default `LLM_TIMEOUT_SECONDS=30`. It validates correctly once the
request timeout is raised; the default timeout degrades safely to the deterministic
extractor rather than silently weakening semantics. This is an operational tuning point,
not a correctness defect, and the default model is configuration-driven.

## Remaining

- **UNVERIFIED_LIVE**: Web Evidence and Operational PostgreSQL (unchanged from the prior
  review).
- **DEFERRED**: generalized temporal NLP, same-period-last-year language, RAG/pgvector,
  new agents/infrastructure/statistics, and any LLM source selection or Text2SQL. These
  remain outside the v0.1 scope freeze.

## Safety boundaries preserved

SQL/file-access restrictions, CTE allowlist hardening, `baseball_readonly` runtime role,
PostgreSQL read-only transactions, DuckDB filesystem restrictions, the bounded
batter-relative upper-edge predicate, entity/date filtering, Artifact reuse after crash,
invalid/null ball-count filtering, fair-batted-ball population, measured-contact behavior,
frozen qualification, safe game-type backfill, context isolation and accepted-product
response boundaries are all unchanged and still exercised by the suite.
