# Development status

Status: **FINAL_REVIEW_BLOCKED** — independent review of Builder
`b0e5d86bb9e2b254bd82c390a9fcf8e609c49d57`, on `codex/v0.1-final-review`.

The authoritative current evidence is [the final review](reviews/v01-final-review.md).
Older milestone statements remain in Git history; they are not current capability claims.

| Surface | Verification | Remaining limit |
| --- | --- | --- |
| Regression suite | TESTED_OFFLINE: 388 tests pass (includes local Parquet tests when archive exists); baseline 380 | Passing suite does not cover all release requirements; separate blocker gate fails |
| SQL guard and runtime privilege separation | IMPLEMENTED, TESTED_OFFLINE, LIVE_VERIFIED for local runtime role | Denylist is not a general SQL sandbox proof |
| Analytics PostgreSQL | LIVE_VERIFIED: 2,196,186 pitches; 564,307 events; 3,726 dictionary rows | Parser, qualification, population semantics block release |
| Historical Parquet | LIVE_VERIFIED: 6,168,817 rows, 2015-04-05 through 2023-11-01 | Canonical pitch keys were discarded; complete grain verification unavailable |
| New location fields | LIVE_VERIFIED: sz_top, sz_bot, p_throws present and non-null in both sources | Declared capability metadata is static |
| Batter-relative upper edge | IMPLEMENTED, TESTED_OFFLINE, LIVE_VERIFIED | Corrected closed band: sz_top - 0.25 <= plate_z <= sz_top |
| Routing and multi-objective results | TESTED_OFFLINE, LIVE_VERIFIED | Declared coverage overlap, not completeness proof; dates outside coverage fail closed |
| Crash recovery and interaction consumption | IMPLEMENTED, TESTED_OFFLINE | Uncertain external execution fails closed; no distributed exactly-once guarantee |
| Shared Knowledge and context isolation | IMPLEMENTED, TESTED_OFFLINE | 51 community seed items/sources; broad external freshness sweep not repeated |
| Web Evidence | UNVERIFIED_LIVE | StatsAPI transport is not evidence-chain verification |
| Operational PostgreSQL | UNVERIFIED_LIVE | Local SQLite recovery tested |

The remaining release blockers are loss of explicit analytics intent, unfrozen qualification,
and undefined/incorrect population semantics (fouls, season game types, outside-zone labels).
Run `python3 docs/reviews/v01-reproduce-blockers.py` to reproduce five semantic failures.
Do not adopt this tree onto main or tag a release.

DEFERRED by the scope freeze: current-period versus same-period-last-year wording,
generalized temporal NLP, RAG, pgvector, new agents/infrastructure, extra statistics,
and full live Web Evidence when network access prevents verification.
