# Independent semantic re-review — 2026-09-17

**FINAL_REVIEW_BLOCKED. No adoption, main update, or release tag.**

Repair reviewed: `86c96de6b000834ec82008a27fcd31785cbfe88a` on
`pi/v0.1-semantic-fixes`. Checkout was clean. Merge-base with the previous review is
exactly `6560bb0c4a934df50ede3eae8c73e82ffb118d2f`.
Independent review branch: `codex/v0.1-semantic-recheck`.
Final commit and exact tree hashes are recorded in the delivery message (a report cannot
contain its own final tree hash). Builder and main remain untouched.

## Baseline and decision

The unchanged `python3 docs/reviews/v01-reproduce-blockers.py` independently exits0 with
`unresolved_blockers=[]`. That verifies its five particular sentences, not compositional
correctness. [Captured original gate](semantic-recheck-evidence/original-gate.jsonl).

The exact repair tree was exported into an isolated `/tmp` checkout and tested with the
real current Parquet path and restored read-only PostgreSQL credentials: **432 tests, OK,
no skips**. An earlier sandbox attempt failed four PostgreSQL cases; a later credential-free
attempt skipped four. Neither was counted as the successful full baseline.

After regression-first corrections: **435 tests, OK, no skips**. Five new compositional
semantic cases still fail a separate release gate. Therefore the condition authorizing
main adoption and v0.1.0 tagging is not met. This is not a feature-expansion request: the
failures combine the metric, threshold, count and game populations the repair exposes.

## Remaining blockers — Spec axis

[Run the complete NL-to-SQL trace](semantic-recheck-evidence/intent-trace.py):

```sh
python3 docs/reviews/semantic-recheck-evidence/intent-trace.py
```

It exits1. It records natural language, normalized objective, immutable requirement,
qualification rule, descriptor and generated PostgreSQL/Parquet SQL. The generated SQL
comes through the actual adapter execute interface with a recording executor.
[Captured trace](semantic-recheck-evidence/intent-trace.jsonl).

| Severity | Exact reproduction | Incorrect outcome / next action |
| --- | --- | --- |
| P1 | `top 5 by maximum exit velocity on fastballs at least 95 mph, at least 100 BBE in 2023` | Adds release_speed>=100 alongside >=95 and HAVING>=100. Qualification text must not be scanned as a velocity threshold. |
| P1 | `top 5 by maximum exit velocity with >= 20 BBE in 2023` | Adds launch_speed>=20; loses qualification, HAVING>=3. Recognize the qualification unit/operator atomically; do not reinterpret its number as mph. |
| P1 | `top 5 hitters facing pitch velocity >= 95 mph ranked by maximum exit velocity in 2023` | Chooses AVG(release_speed), not MAX(launch_speed). Ranking selection takes the first metric after Top5 rather than the explicitly ranked metric. |
| P1 | `top 5 by maximum exit velocity in exhibition games in 2023` | Emits regular-season R instead of exhibition. Named unsupported/recognized competition intent must not silently default to R. |
| P1 | `top 5 by maximum exit velocity on 0-2 or 1-1 counts in 2023` | Drops the count completely, no clarification. `_count_constraint` says fail closed but returns None, allowing a wider query. Either represent the union or reject/clarify it. |

Simple minimum3/20/100 BBE with both MAX and AVG passes the entire chain. No explicit
qualification freezes default3. Exact0-2 remains balls0/strikes2; generic two strikes
keeps balls0..3. A sentence with separate fastball>=95 and EV>=100 filters and MAX EV
ranking works in the tested ordering. These successes do not negate the ordering and
unit-binding failures above. RankingConstraint, QualificationRule and SampleAdequacyRule
remain distinct; no sample-adequacy rule is invented from qualification.

## Fixed defects — Standards axis

1. **P1 population predicate:** `events IS NOT NULL` includes plate-appearance endings,
   not just balls in play. The real Parquet archive has three measured
   `events='truncated_pa', description='foul'` rows, with EV74.7..92.3, which the old EV
   predicate admitted. With pitch-velocity ranking, walks/strikeouts/HBP also entered the
   purported fair-ball population because release_speed is populated.
2. **P1 measured-contact predicate:** no explicit contact filter was applied when ranking
   pitch velocity. Fixed to require launch_speed IS NOT NULL independently of ranking.
3. **P2 frozen-threshold override:** an explicit requirement minimum20 plus real adapter
   constructor min_batted_balls=1 generated HAVING>=1. Frozen rule now always wins;
   constructor fallback applies only when no rule exists.
4. **P2 maintenance data loss:** an incomplete cached game map rewrote an existing W row
   to NULL. Backfill now checks every archive file for missing mappings before replacing
   any file; failure preserves existing bytes. No actual source backfill was run here.

Fix commits: `d6ee736` (population/qualification) and `bae805a` (backfill).
All had failing tests before fixes. The population fixture executes SQL over actual DuckDB
rows, including strikeouts, walks, HBP, sacrifices, fielding outcomes, terminal/nonterminal
fouls, in-play rows with NULL EV, Spring Training, unknown and NULL game types. The new
qualification test proves constructor configuration cannot override the frozen rule.
The maintenance regression proves unchanged file bytes after a rejected incomplete map.

The new event predicates are:

| Population | Predicate before the metric's non-null filter |
| --- | --- |
| BATTED_BALL | description='hit_into_play' |
| MEASURED_CONTACT | launch_speed IS NOT NULL |
| ALL_PITCHES | no event filter |

For all populations, the requested metric must also be non-null. Thus an EV leaderboard
counts available EV measurements, without imputing missing EV to zero. A pitch-velocity
leaderboard can retain in-play rows lacking EV under BATTED_BALL; MEASURED_CONTACT cannot.
Sacrifice/fielding results classified hit_into_play remain; strikeouts, walks, HBP and
foul contacts do not. The implementation uses the provider's in-play classification, not
an independent reconstruction of fair/foul territory. ADR0020 was corrected explicitly;
we did not rename the broader plate-appearance population to conceal the defect.

[Official CSV documentation](https://baseballsavant.mlb.com/csv-docs) identifies events as
the plate-appearance result and description as the pitch result, and distinguishes
release_speed from launch_speed. The live stored values, not merely that documentation,
provide the concrete counterexample. PostgreSQL currently has no measured terminal
non-hit_into_play rows; the bug was nevertheless real on the historical source.

## Live data and game-type audit

[Data audit](semantic-recheck-evidence/data-audit.jsonl):

| Source | Rows | Populated game_type | Distinct gamePk | Full-row duplicate excess |
| --- | ---: | ---: | ---: | ---: |
| Parquet | 6,168,817 | 6,168,817 | 20,956 | 0 |
| PostgreSQL | 2,196,186 | 2,196,186 | 7,502 | 0 |

Counts are unchanged from the previous independently verified checkpoint. Every stored
gamePk has exactly one type and matches the cached backfill map. The backfill joins use
primary-key maps, preventing many-to-many row multiplication. Canonical pitch sequence
keys remain absent, so no claim of complete pitch-grain uniqueness is made.

Both live sources contain R/S/F/D/L/W. Fresh official StatsAPI schedules for **all twelve
years2015–2026** were fetched and compared with the cached map: all returned cached games
match; zero type conflicts. [Fresh upstream and security checks](semantic-recheck-evidence/upstream-security.jsonl).
Read-only recipes accompany the evidence; no credentials are included.

Physical mappings: regular season R; postseason F/D/L/W; Spring Training S; the exposed
EXHIBITION category currently E/A. R queries exclude postseason/Spring Training. Unknown
and NULL stored codes are excluded by named IN filters; unknown semantic categories are
rejected by the typed contract. EXHIBITION's natural-language path still defaults to R
(the blocker above), and E/A lack live rows here, so those executions are TESTED_OFFLINE,
not LIVE_VERIFIED. Calendar/game-type coverage is not inferred from the date alone.

## Location and prior security/recovery regressions

Upper-third1–3 and lower-third7–9 remain intact. The legacy ZONE_ABOVE_UPPER_EDGE runtime
symbol is removed; its appearances in historical review/ADR discussion are explanations,
not executable aliases. “Just above the zone” triggers clarification rather than direct
zones11/12 mapping. ZONE_UPPER_OUTSIDE remains a code set without a strict above-sz_top
claim. Batter-relative band remains sz_top-0.25<=plate_z<=sz_top. No synonym was substituted
for an unavailable physical field.

Prior SQL/file/dynamic-query/CTE protections and runtime-role gate are unchanged and their
regressions pass. Live connection is baseball_readonly with transaction_read_only=on.
SELECT succeeds; CREATE, INSERT, UPDATE, DELETE and DROP all fail SQLSTATE25006 in
rollback-protected probes. No admin credential or maintenance path enters app/.

Persistence tests cover stored qualification, consumed interactions, frozen dates and
artifact reuse across crashes. Live restart probes make **zero additional source SQL
calls**, with unchanged objective definitions and statuses. The persisted requirement
threshold remains20. Uncertain external outcomes still fail closed; no distributed
exactly-once claim is made. No new context/evidence leakage was found in the full suite
or multi-objective live products.

## Real E2E, SQL agreement and limits

[Run recipe](semantic-recheck-evidence/live-probes.py),
[captured SQL, rows, descriptors, accepted packages and restarts](semantic-recheck-evidence/live-e2e.jsonl).
These use natural language for AVG/MAX, GTE95, generic two-strike count, regular season,
minimum20 and Top5; upper-edge wording is resolved through the real clarification flow.
No synthetic adapters or fallback are used.

The SQL explicitly contains release_speed>=95, strikes=2, balls IN(0,1,2,3), FF/SI/FC/FA,
game_type IN('R'), description='hit_into_play', the closed sz_top band,
launch_speed IS NOT NULL, HAVING COUNT(*)>=20, and requested AVG/MAX(launch_speed).

| Query window | Source/result | Assessment outcome |
| --- | --- | --- |
| 2023, full narrow filters | Parquet, zero qualifying batters | FAILED; no accepted product or fabricated Top5 |
| 2025, full narrow filters | PostgreSQL, zero qualifying batters | FAILED; no accepted product |
| 2023 vs2024, full narrow filters | two correctly routed objectives, both empty | both FAILED; distinct objective state |
| 2015-04-05..2023-11-01, full narrow filters | Parquet, five rows, each count>=20 | STRONG accepted Artifact, COMPLETE, real provenance |
| 2024-03-15..2026-09-14, full narrow filters | PostgreSQL, two rows with counts23/28 | LIMITED; no false COMPLETE or accepted Top5 |
| 2023 vs2024, regular-season MAX EV/minimum20 without narrow pitch/location filters | Parquet/PostgreSQL, five rows each | separate ACCEPTABLE products, both COMPLETE |

The final comparison is additional evidence, not a replacement disguised as the requested
narrow query. Historical names are sparse; PostgreSQL accepted results have dictionary
names. Narrow single-year qualification cannot be satisfied by secretly lowering20 to3.
One secondary diagnostic defect remains: `_query` maps executor EMPTY to None, so empty
rankings appear as generic query failure rather than an explicit no-qualifiers answer.
This does not create a false accepted result, but deserves a focused follow-up.

## Gates, documentation and release history

- Exact repair baseline:432 tests passed, no skips.
- Corrected review tree:435 tests passed, no skips.
- compileall, current-tree secret scan and diff whitespace checks pass.
- CLI workflow script passes; its synthetic analytics are explicitly demo-only and are
  not counted as real-data verification.
- Original five-case blocker gate passes; expanded NL-to-SQL gate fails five cases.
- PostgreSQL/Parquet data reads, game-type backfill values, supported R/S/postseason data,
  live role protections, representative executions and restart reuse: LIVE_VERIFIED.
- Population regression, unsupported code exclusion, qualification override prevention,
  maintenance map failure, prior security/recovery regressions: TESTED_OFFLINE.
- Full Web Evidence, Operational PostgreSQL, comprehensive canonical pitch-key
  reconciliation and E/A source executions: UNVERIFIED_LIVE.
- DEFERRED: generalized temporal NLP, same-period-last-year language, RAG/pgvector,
  extra agents/infrastructure/statistics. These are not the blockers above.

Current README/status/handoff/matrix summaries point to this report. Historical repair
claims remain distinguishable from independently verified current status. No architecture
redesign or feature expansion was made. Frozen intent is still violated by normalization,
so architecture compliance is not approved despite correct layer separation.

Do not adopt this tree. After the remaining semantic defects are corrected and independently
approved, reverify origin/main, create one adoption commit with its sole parent equal to
current main and its tree equal to the approved review tree, verify tree equality, and run
all release gates on that commit. Only if remote main remains unchanged and gates pass may
main fast-forward and the annotated v0.1.0 tag be pushed. This review performed no adoption,
force-push, unrelated-history merge, main update or release tag.
