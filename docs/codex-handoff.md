# Codex handoff

Current independent verdict (2026-09-17): **FINAL_REVIEW_BLOCKED** on
`codex/v0.1-semantic-recheck`. [Semantic re-review](reviews/v01-semantic-recheck.md)
records the remaining five compositional failures, regression-backed fixes, and live evidence.
Baseline: 432 tests; corrected review: 435 tests, no skips. The original blocker gate
passes, but the expanded NL-to-SQL release gate fails. No main adoption or tag is authorized
while those P1 defects remain. Repair-checkpoint claims below are not release approval.

Status: **FINAL_REVIEW_BLOCKED**. The focused repair branch
`pi/v0.1-semantic-fixes` is based exactly on the reviewed
`codex/v0.1-final-review` checkpoint (Builder `b0e5d86bb9e2b254bd82c390a9fcf8e609c49d57`,
review fixes through `6560bb0`). Builder branch and main are untouched.
See [the final review](reviews/v01-final-review.md) for the blocked baseline,
[the capability matrix](development/analytics-capability-matrix.md) for the current
source/population contract, and
[the population ADR](adr/0020-analytical-population-and-qualification.md) for the design.

The five original sentence-level reproductions pass, with regression coverage:

1. Normalization now preserves explicit pitch/exit-velocity filters (including `>=`),
   explicit AVG/MAX aggregation, and exact `0-2` counts; unsupported wording asks for
   clarification instead of widening intent.
2. A requested qualification threshold is a typed `QualificationConstraint` frozen as
   `QualificationRule`; the adapter applies the frozen value and never substitutes its
   own default when one is present.
3. The analyzed population is an explicit `PopulationConstraint`. `game_type` is retained
   by the loaders and backfilled on already-stored rows from the authoritative StatsAPI
   schedule (`python3 -m data_loader.backfill_game_type`), so regular season, postseason,
   Spring Training, fair batted balls and measured contact are distinct and visible.
4. Statcast zones 11-12 are renamed `ZONE_UPPER_OUTSIDE` and described as upper outside
   quadrants; `just above the zone` no longer silently claims that zone set.

Fixed earlier with regressions first (must not regress):

- SQL dynamic-query/file-reader escapes, CTE allowlist scope, catalog qualification,
  relative-path and glob/symlink checks; reject runtime roles other than
  baseball_readonly.
- Persisted artifact reuse when a crash interrupts execution-status finalization.
- Upper-edge upper bound, canonical batter filter, descriptor date window, valid ball
  counts; unsupported entity identities fail closed.

Gates on this branch: 432 tests pass, blocker reproduction exits 0, `compileall` passes,
secret scan passes, live PostgreSQL and Parquet analytics pass, and the 2023-vs-2024
cross-source comparison produces distinct accepted products. Do not adopt this tree onto
main or tag a release before the independent recheck approves it.

LIVE_VERIFIED: local PostgreSQL counts/coverage/readonly privileges and `game_type`
coverage; Parquet counts, retained fields and `game_type`; representative
historical/recent/multi-source executions. UNVERIFIED_LIVE: full Web Evidence, Operational
PostgreSQL, complete upstream pitch-grain reconciliation. No synthetic fallback observed.

Remote main observed during review: `c93953d4c7e54dfd2b98ffd7d559e5efc946e6b9`.
Only after approval, re-fetch main and verify both SHAs. Create one adoption commit with
current main as parent and the approved tree as its exact tree. Verify tree equality and
rerun release gates; no force-push, unrelated-history merge, main rewrite, or tag yet.

DEFERRED: generalized temporal NLP, same-period-last-year wording, RAG/pgvector, new
agents/infrastructure/statistics. Existing architecture and domain invariants remain frozen.
