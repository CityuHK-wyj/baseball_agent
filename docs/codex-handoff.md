# Codex handoff

Status: **FINAL_REVIEW_BLOCKED**. Continue from `codex/v0.1-final-review`, based exactly on
Builder `b0e5d86bb9e2b254bd82c390a9fcf8e609c49d57`. Builder branch and main are untouched.
See [the final review](reviews/v01-final-review.md) and its evidence directory.

Fixed with failing regressions first:

- SQL dynamic-query/file-reader escapes, CTE allowlist scope, catalog qualification,
  relative-path and glob/symlink checks; reject runtime roles other than baseball_readonly.
- Persisted artifact reuse when a crash interrupts execution-status finalization.
- Upper-edge upper bound, canonical batter filter, descriptor date window, valid ball counts;
  unsupported entity identities fail closed.

Baseline 380 tests; reviewed implementation 388 passing. Use system `python3`:
the repository `.venv` lacks dependencies. Verification commands are in the review report.

Next work is defect correction, not feature expansion:

1. Preserve >= pitch-speed, explicit EV filters/AVG/MAX, and explicit counts in normalization;
   unsupported recognized analytics intent must fail closed or ask clarification.
2. Persist qualification independently from metric and sample adequacy, and execute that
   frozen rule rather than a mutable adapter default. A requested minimum20 currently becomes3.
3. Define and enforce the batted-ball and season population. Measured foul contacts currently
   enter the leaderboard; Spring Training/postseason rows coexist and game_type is discarded.
4. Resolve the misleading zones11/12 “just above” contract: those are outside upper quadrants,
   not a predicate requiring plate_z above sz_top. Do not silently substitute definitions.
5. Re-run independent review and all live gates before any release adoption.

LIVE_VERIFIED: local PostgreSQL counts/coverage/readonly privileges; Parquet counts and
new fields; representative historical/recent/multi-source executions. Their COMPLETE output
is not approval of analytical correctness. UNVERIFIED_LIVE: full Web Evidence, Operational
PostgreSQL, complete upstream pitch-grain reconciliation. No synthetic fallback observed.

The +8 Parquet delta is localized to five games, with net +4/+3/+1 in 2015/2017/2018.
It is not caused by dropping null-coordinate rows. Keep `/tmp/ba_parquet_backup_20260916_191753`.
PostgreSQL starts March15 because pybaseball's fallback iterator skips earlier dates;
this is not a verified upstream absence. See the report for exact evidence and limitations.

Remote main observed during review: `c93953d4c7e54dfd2b98ffd7d559e5efc946e6b9`.
Only after approval, re-fetch main and verify both SHAs. Create one adoption commit with
current main as parent and the approved tree as its exact tree. Verify tree equality and
rerun release gates; no force-push, unrelated-history merge, main rewrite, or tag yet.

DEFERRED: generalized temporal NLP, same-period-last-year wording, RAG/pgvector, new
agents/infrastructure/statistics. Existing architecture and domain invariants remain frozen.
