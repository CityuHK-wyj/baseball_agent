# Development status

Current independent verdict: **FINAL_REVIEW_BLOCKED** on
`codex/v0.1-final-hybrid-review`, reviewing Pi `ebdccc75793ebd2cf448e1b199f2a16109e5ca7e`.
[Final hybrid review](reviews/v01-final-hybrid-review.md): 479 tests pass, but nine new adversarial cases fail.
Real DeepSeek extraction and PostgreSQL/Parquet execution are LIVE_VERIFIED;
semantic containment and partial fallback fidelity fail. The original/compositional
blockers are repaired. Main and tags remain untouched. Web Evidence and Operational
PostgreSQL remain UNVERIFIED_LIVE; generalized temporal NLP and RAG remain DEFERRED.

Builder checkpoint (2026-09-18; superseded by the review above): **HYBRID_SEMANTIC_LAYER_LIVE_VERIFIED — READY_FOR_CODEX_REVIEW** on
`pi/v0.1-llm-semantic-parser`, branched from
`codex/v0.1-semantic-recheck @ eae9875e86632f509b0351ea31da040ef151bc7e`.
[Implementation report](reviews/v01-hybrid-semantic-layer.md). 479 tests pass with no
skips; the original blocker gate, the Codex NL-to-SQL trace and the new hybrid semantic
gate all exit 0 with no unresolved blockers. Live LLM semantic extraction is
`LIVE_VERIFIED` against the real provider (`deepseek-chat`, plus the configured default
`deepseek-v4-pro`), with recorded evidence in
`reviews/semantic-hybrid-evidence/live-llm-semantic.jsonl`. This is a review candidate,
not adoption or release approval: `main` is untouched and no tag exists.

Prior review history (superseded by the review above):

Historical independent verdict (2026-09-17): **FINAL_REVIEW_BLOCKED** on
`codex/v0.1-semantic-recheck`. [Semantic re-review](reviews/v01-semantic-recheck.md)
records the remaining five compositional failures, regression-backed fixes, and live evidence.
Baseline: 432 tests; corrected review: 435 tests, no skips. The original blocker gate
passes, but the expanded NL-to-SQL release gate fails. No main adoption or tag is authorized
while those P1 defects remain. Repair-checkpoint claims below are not release approval.

Status: **FINAL_REVIEW_BLOCKED** on
`pi/v0.1-semantic-fixes`, branched from the reviewed `codex/v0.1-final-review`.

The authoritative blocked baseline is [the final review](reviews/v01-final-review.md).
The focused repairs are described in
[the population ADR](adr/0020-analytical-population-and-qualification.md) and the
[capability matrix](development/analytics-capability-matrix.md). Older milestone
statements remain in Git history; they are not current capability claims.

| Surface | Verification | Remaining limit |
| --- | --- | --- |
| Regression suite | TESTED_OFFLINE: 432 tests pass (includes live PostgreSQL/Parquet integration when credentials/archive exist) | Passing suite is not independent approval |
| Semantic blocker gate | `docs/reviews/v01-reproduce-blockers.py` exits 0 with zero unresolved blockers | Gate covers the five demonstrated failures; other wording remains bounded |
| SQL guard and runtime privilege separation | IMPLEMENTED, TESTED_OFFLINE, LIVE_VERIFIED for local runtime role | Denylist is not a general SQL sandbox proof |
| Analytics PostgreSQL | LIVE_VERIFIED: 2,196,186 pitches; 564,307 events; 3,726 dictionary rows; `game_type` 100% non-null | Parser coverage is a bounded supported grammar |
| Historical Parquet | LIVE_VERIFIED: 6,168,817 rows, 2015-04-05 through 2023-11-01; `game_type` 100% non-null | Canonical pitch keys were discarded; complete grain verification unavailable |
| New retained fields | LIVE_VERIFIED: sz_top, sz_bot, p_throws, game_type present and non-null in both sources | Declared capability metadata is static |
| Batter-relative upper edge | IMPLEMENTED, TESTED_OFFLINE, LIVE_VERIFIED | Corrected closed band: sz_top - 0.25 <= plate_z <= sz_top |
| Zones 11-12 | IMPLEMENTED, TESTED_OFFLINE | Named/described upper outside quadrants, not a strict above-sz_top predicate |
| Explicit qualification | IMPLEMENTED, TESTED_OFFLINE, LIVE_VERIFIED | Frozen `QualificationRule`; explicit threshold survives restart/source change |
| Population | IMPLEMENTED, TESTED_OFFLINE, LIVE_VERIFIED | Regular-season fair batted balls default; explicit postseason/Spring Training/contact/pitches |
| LLM semantic extraction | LIVE_VERIFIED: 5/5 required cases plus the clarification lifecycle through real DeepSeek calls | Model output is validator-gated; latency above the default timeout falls back safely |
| Routing and multi-objective results | TESTED_OFFLINE, LIVE_VERIFIED | Declared coverage overlap, not completeness proof; dates outside coverage fail closed |
| Crash recovery and interaction consumption | IMPLEMENTED, TESTED_OFFLINE | Uncertain external execution fails closed; no distributed exactly-once guarantee |
| Shared Knowledge and context isolation | IMPLEMENTED, TESTED_OFFLINE | 51 community seed items/sources; broad external freshness sweep not repeated |
| Web Evidence | UNVERIFIED_LIVE | StatsAPI transport is not evidence-chain verification |
| Operational PostgreSQL | UNVERIFIED_LIVE | Local SQLite recovery tested |

The five original sentence-level reproductions pass; broader composition remains blocked: explicit pitch/exit-velocity
filters, explicit AVG/MAX aggregation, exact `0-2` counts, frozen qualification
thresholds, and an explicit game-type/batted-ball population. Do not adopt this tree onto
main or tag a release until the independent recheck approves it.

DEFERRED by the scope freeze: current-period versus same-period-last-year wording,
generalized temporal NLP, RAG, pgvector, new agents/infrastructure, extra statistics,
and full live Web Evidence when network access prevents verification.
