# Development status

Status: **SEMANTIC_BLOCKERS_FIXED — READY_FOR_CODEX_RECHECK** on
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
| Routing and multi-objective results | TESTED_OFFLINE, LIVE_VERIFIED | Declared coverage overlap, not completeness proof; dates outside coverage fail closed |
| Crash recovery and interaction consumption | IMPLEMENTED, TESTED_OFFLINE | Uncertain external execution fails closed; no distributed exactly-once guarantee |
| Shared Knowledge and context isolation | IMPLEMENTED, TESTED_OFFLINE | 51 community seed items/sources; broad external freshness sweep not repeated |
| Web Evidence | UNVERIFIED_LIVE | StatsAPI transport is not evidence-chain verification |
| Operational PostgreSQL | UNVERIFIED_LIVE | Local SQLite recovery tested |

The five Codex-reproduced semantic blockers are resolved: explicit pitch/exit-velocity
filters, explicit AVG/MAX aggregation, exact `0-2` counts, frozen qualification
thresholds, and an explicit game-type/batted-ball population. Do not adopt this tree onto
main or tag a release until the independent recheck approves it.

DEFERRED by the scope freeze: current-period versus same-period-last-year wording,
generalized temporal NLP, RAG, pgvector, new agents/infrastructure, extra statistics,
and full live Web Evidence when network access prevents verification.
