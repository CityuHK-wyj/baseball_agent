# Independent final v0.1 review

**FINAL_REVIEW_BLOCKED** — 2026-09-16. Do not merge, adopt onto main, or tag.

Builder reviewed: `b0e5d86bb9e2b254bd82c390a9fcf8e609c49d57`.
Review branch: `codex/v0.1-final-review`, created from that exact clean checkout.
Remote Builder was independently rechecked at that SHA. Remote main observed:
`c93953d4c7e54dfd2b98ffd7d559e5efc946e6b9`. Neither branch was modified.
The exact final commit/tree identifiers are in the delivery message; obtain them locally
with `git rev-parse HEAD HEAD^{tree}`. A report cannot embed its own final tree hash.

## Decision and open blockers

The tree is not a release candidate. Passing tests, real data, accepted artifacts, and
COMPLETE states do not establish preservation of user intent. The remaining defects are
within the requested v0.1 analytics scope, not requests for generalized NLP or new agents.

| ID / severity | Demonstrated defect / violated invariant | Reproduction and next action |
| --- | --- | --- |
| R1 / P1 | Normalization silently loses or changes explicit analytics intent | `fastballs >= 95 mph` loses velocity; `exit velocity above 95 mph` becomes pitch_velocity; `top 5 by maximum exit velocity` loses ranking; `0-2` expands to balls0..3. Run the blocker gate below. Preserve the bounded supported grammar, or clarify/fail closed; never execute weaker intent. |
| R2 / P1 | Qualification is not part of the frozen initial requirement | `top 5 by exit velocity minimum 20 batted balls` yields no qualification/sample rule; SQL uses adapter default3. Persist a separate eligibility rule and execute it consistently across resume/sources. Do not encode eligibility as metric aggregation or confuse it with sample adequacy. |
| R3 / P1 | Analytics population is insufficiently defined and represented | launch_speed IS NOT NULL includes measured fouls; Spring Training and postseason coexist, while game_type is dropped. A regular-season or fair-ball interpretation cannot be enforced from current retained semantics. Define/persist the population and make unsupported populations fail closed; do not silently relabel contact counts as standard leaderboard BBE. |
| R4 / P1 | “Just above the zone” is advertised as exact zones11/12 | Those codes are upper outside quadrants, including pitches laterally outside below sz_top. In 2023, 35,188/85,756 zone11 pitches and 27,311/62,581 zone12 pitches are below sz_top. Retain explicit zone-set semantics, but resolve the misleading natural-language contract rather than claiming exact above-edge filtering. |

Reproduction: `python3 docs/reviews/v01-reproduce-blockers.py` exits **1**, with five
failed semantic expectations. This is a deliberately failing release gate, separate from
the passing regression suite. [Captured output](v01-evidence/blocker-gate.jsonl).
The live population probes below demonstrate R3/R4. These remain open for a focused
correction and re-review; this review did not invent new qualification or population
contracts on behalf of the Builder.

## Fixed defects

All behavior fixes had a failing regression before their implementation.

| Severity | Defect | Fix / evidence |
| --- | --- | --- |
| P1 security | DuckDB query()/query_table() bypassed file restrictions and returned an outside-root canary | Block dynamic SQL/file indirection; validate additional file readers; executor rejection tests |
| P1 security | Global CTE-name filtering hid qualified or out-of-scope physical tables; catalog qualification was dropped | Resolve lexical CTE scopes and retain full catalog/schema names; adversarial allowlist tests |
| P1 security | Relative paths were validated relative to file_root but executed relative to cwd; safe glob prefixes could match outside symlinks | Match DuckDB cwd semantics and inspect resolved matches |
| P1 security | Runtime accepted any configured PostgreSQL role | Reject non-baseball_readonly configuration before opening a connection |
| P1 correctness | Upper-edge predicate included arbitrarily high pitches | Enforce sz_top-0.25 <= plate_z <= sz_top, matching the documented three-inch band below the top; executable boundary fixture rejects 3.51/6.0 at sz_top3.5 |
| P1 correctness | Artifact advertised requested player/date semantics without filtering them | Filter canonical MLBAM batter IDs and descriptor TimeRange; fail closed for unsupported identities. Fixture previously returned another batter and wrong-year rows |
| P2 correctness | Two-strike query omitted ball bounds, admitting corrupt/null ball counts | Always apply permitted ball values; empty set matches nothing. 0-2/1-2/2-2 and valid3-2 remain eligible for generic two-strike intent |
| P1 persistence | Artifact saved before execution-status finalization could not be reused | Stage returned outcome references before artifact storage; recover only when all referenced artifact metadata exists; regression reopens store with no executable tool |

Fix commits: `933bde6` (security), `bef0cc7` (recovery), `e51118d` (analytics bounds).
No architecture redesign, data reload, backup removal, or synthetic fallback was introduced.

## Standards axis

Hard breaches of ADR0004/0006 SQL isolation and ADR0007/0009 artifact recovery were
reproduced and fixed. Runtime privilege separation now has an explicit role gate as well
as transaction and database protections. Code retains the frozen domain layering and
semantic-task/physical-adapter seam. Cosmetic smell refactors were excluded.

Security is defense in depth, not a proof that every future DuckDB function is safe.
File checks assume a trusted local archive directory; adversarial concurrent filesystem
mutation and every extension/function version were not exhaustively verified.

## Spec axis

R1–R4 prevent acceptance. Typed NumericConstraint maps pitch_velocity to release_speed
and exit_velocity to launch_speed deterministically on both sources, but the parser can
assign the wrong semantic key before mapping. SQL ranking consumes explicit aggregation;
AVG/MAX are represented by `RankingConstraint.aggregation`, not separate named metric
constants. This separation works through the typed API, not all advertised NL wording.
Fastball FF/SI/FC/FA is explicit and is the project definition; no universal pitch-family
ontology is asserted. Qualification remains a constructor default, contradicting immutable
semantic intent. Unsupported physical queries fail rather than fabricate data.

## Live PostgreSQL and privilege audit

LIVE_VERIFIED using runtime credentials, read-only queries, and rollback after each probe:

- statcast_pitches: **2,196,186**, 2024-03-15..2026-09-14; new fields all non-null.
- batting_events: **564,307**, same span.
- player_dictionary: **3,726 rows and 3,726 distinct IDs**; all **2,050** batter IDs resolve.
- Zero identical full-row duplicate excess in statcast_pitches; zero invalid ball/strike
  counts observed. Dictionary cardinality prevents one ID multiplying a join. Runtime
  name resolution is a separate ID lookup, not a pitch-table multiplication join.
- SELECT succeeds as baseball_readonly, transaction_read_only=on; no superuser,
  createdb/createrole/replication/bypassrls attributes. CREATE/INSERT/UPDATE/DELETE/DROP
  all fail with SQLSTATE25006. Probes cannot leave durable writes: each rolls back.
- No admin credential fallback/import exists in app/. POSTGRES_ADMIN_PASSWORD is confined
  to maintenance modules. Archival has an explicit environment gate, though any nonempty
  value enables it; this is an opt-in guard, not the runtime permission boundary.

Name uniqueness and coverage are verified, not all 3,726 display spellings against an
external canonical roster. Last-first formatting/aliases do not change MLBAM identity.
The loader lacks retained at_bat_number/pitch_number and database pitch uniqueness keys;
zero identical rows is not proof of canonical pitch uniqueness. Maintenance reload skips
chunks when all existing rows have sz_top, which is not a completeness manifest.

**Why March15:** installed pybaseball `statcast_date_range` falls back to March15–November15
for years absent from STATCAST_VALID_DATES; statcast uses that iterator. It also requests
regular season/postseason/Spring Training. [MLB game747956](https://statsapi.mlb.com/api/v1/schedule?gamePk=747956)
is a Spring Training game on March15. This start is ingestion behavior, not proven upstream
absence. Earlier Spring Training coverage is not claimed and no missing games were fabricated.

## Parquet rebuild and +8 reconciliation

LIVE_VERIFIED: **6,168,817 rows**, nine year files, 2015-04-05..2023-11-01;
sz_top/sz_bot/p_throws all present and non-null; zero identical full-row duplicate excess.
Each file's observed dates stay in its named year. Runtime glob points to the current
archive, not `/tmp/ba_parquet_backup_20260916_191753`. Parquet is gitignored and untracked;
backup remains untouched.

Backup total **6,168,809**. The net +8 is concentrated in these games:

| Year | Game | Net rows |
| --- | --- | ---: |
| 2015 | 414012 | +1 |
| 2015 | 414625 | +1 |
| 2015 | 414773 | +2 |
| 2017 | 490181 | +3 |
| 2018 | 529812 | +1 |

Other retained-field values changed too: for example matched2015 records have changes
in pitch type, spin, EV, launch angle, and expected metrics. EXCEPT ALL comparisons and
coordinate/count matching are in [the differential evidence](v01-evidence/parquet-delta.txt).
The Builder's “added null-coordinate rows that were dropped” explanation cannot explain
an increase in final retained rows. The added retained records have non-null coordinates.
A fresh Savant game414012 CSV contains243 valid-coordinate rows, matching rebuilt243
versus backup242. Its extra pitch is at_bat_number26/pitch_number1, pitcher450308,
batter543063; coordinates0.11841716301591276/1.5617498842737163. All243 matched zone-top,
zone-bottom and throwing-hand values agree with upstream (coordinate tolerance1e-10).

Fresh upstream CSVs for all five affected games were then checked. Valid-coordinate
upstream/rebuilt/backup counts are243/243/242,264/264/263,291/291/289,
287/287/284 and292/292/291 respectively. All1,377 rebuilt records in these games match
upstream zone-top/zone-bottom/throwing-hand fields using the coordinate tolerance above.
Game490181 has one upstream null-coordinate row and game529812 has six; they are excluded
and do not explain the positive retained delta. [Captured comparison](v01-evidence/upstream-games.json).

The saved archives omit canonical pitch sequence keys and the older raw upstream export
is absent. Thus the exact upstream editing history (new event versus repaired previously
missing coordinates) cannot be reconstructed for every row. The demonstrated cause at
archive level is changed retained upstream records, not duplicate ingestion or added
rows that remained null and were dropped. Full upstream reconciliation is UNVERIFIED_LIVE.

## Routing, capability and E2E

Declared coverage boundary probes:

| Date | Eligible real analytics source |
| --- | --- |
| 2023-12-31 | none |
| 2024-01-01 | none |
| 2024-03-14 | none |
| 2024-03-15 | POSTGRES |
| 2026-09-14 | POSTGRES |
| 2026-09-15 | none |
| 2014-12-31 | none |

Routing accepts overlap and assessment reports observed partial span. It does not prove
complete coverage. Capabilities/schema columns are static declarations; SQL against an
older schema fails rather than silently substitutes a zone definition, but pre-routing
introspection is not implemented. A failed observed-span query can fall back to the
requested span in `_observed_range`; this is an additional truthfulness risk that should
be removed before asserting comprehensive coverage guarantees.

Representative runs used explicit typed AVG, GTE95, BATTER_RELATIVE_UPPER_EDGE, generic
two-strike and fastball semantics, top5, and text minimum3. Minimum3 is disclosed by the
adapter but **not frozen by normalization** (R2). Consequently the exact requested E2E
acceptance gate is not satisfied, even though technical execution is LIVE_VERIFIED:

| Window | Objectives | Source | Runtime result |
| --- | ---: | --- | --- |
| 2023 | 1 | PARQUET | five rows, ACCEPTABLE, COMPLETE with64% calendar-span limitation |
| 2025 | 1 | POSTGRES | five named rows, ACCEPTABLE, COMPLETE with64% limitation |
| 2023 vs2024 | 2 | PARQUET / POSTGRES | two accepted products and distinct responses; no result overwrite |

2025 top row: Gary Sánchez, ID596142, count3, average104.3mph (current contact population).
Historical IDs are real; names are blank outside sparse canonical knowledge seeds.
No synthetic source appears in these runs. Captured results are [here](v01-evidence/representative-e2e.json).
Do not present these rankings as approved analytical answers until R1–R4 are resolved.

## Statistical sanity: the plausible numbers were reproducible

Filter: strikes2, balls0..3, release_speed>=95, FF/SI/FC/FA, launch_speed non-null.
No player eligibility HAVING was applied to these pooled, pitch-weighted sanity means.

| Source | Measured-contact denominator | Old unbounded upper-edge numerator | Fraction | Pooled EV |
| --- | ---: | ---: | ---: | ---: |
| 2023 Parquet | 19,048 | 5,356 | 28.12% | 81.5932mph |
| 2024–2026 PostgreSQL | 63,429 | 18,986 | 29.93% | 81.4893mph |

These reproduce the reported28.1%/29.9% and81.59/81.49. They do not validate the semantics.
With the corrected closed band, counts become2,357/8,240 and means81.7114/81.6279.
Only907/3,250 of those records are hit_into_play; those means are87.5686/86.9053mph.
In2023 the remaining1,450 qualifying records are fouls. The old query's location error,
measured-contact denominator, and pooled weighting explain plausible but misleading
similarity. Player ranking then applies count>=3 and AVG/MAX independently.
[All > versus >= and ball-count variants](v01-evidence/cross-source-sanity.jsonl).

[Official Statcast documentation](https://baseballsavant.mlb.com/csv-docs) independently
confirms pitch-speed/EV fields, pre-pitch count semantics, and game-type codes. Its
[zone diagram](https://baseballsavant.mlb.com/sections/statcast-search-new/images/zones.png)
places1–3 above7–9 and shows11/12 as upper outside regions. The documentation also records
2026 measurement changes (zone bounds and plate-coordinate reference); pooling2024–2026
therefore warrants an explicit comparability limitation rather than a claim of identical
measurement regimes.

## Persistence, context and architecture verdict

TESTED_OFFLINE: full suite includes clarification, permission, constraint revision,
consumed-answer replay prevention, frozen initial definitions and relative dates, durable
intent, process exit/restart, and objective-scoped artifact reuse. The new regression
covers artifact stored before execution status; prior subprocess coverage exercises
stored artifact before assessment/state updates. Restart uses the same run and does not
consume the answer twice. Intent persisted with no durable provider outcome stops with
EXECUTION_UNCERTAIN, including a crash before invocation where the system cannot prove
that fact. This is conservative non-duplication, not unconditional recovery or distributed
exactly-once execution. Missing staged artifact metadata fails closed.

Context/Knowledge tests pass: cross-run/cross-objective isolation, rejected evidence
exclusion, bounded Planner context, source-specific evidence requirements, temporal
validity, ACTIVE versus COLLECTED, and authoritative facts versus community material.
KnowledgeStore remains canonical; EntityDictionary/MetricRegistry are projections.
No retrieval agent, RAG, or pgvector was introduced. Community seed has51sources and51items;
no seed count inconsistency was found. A full external community refresh was not repeated.

Architecture structure is preserved, but compliance is **not approved**: losing intent
before initial definitions and omitting qualification defeats the semantic baseline;
copying a requested descriptor cannot establish the correctness of the returned payload.
Hard Judge veto remains intact, but it cannot veto facts that the adapter never reports.
Response consumes accepted products; the issue is that semantically wrong products may
be accepted upstream.

## Gates, documentation and remaining scope

- Baseline: system `python3 -m unittest discover -s tests -q`: **380 passed**.
- After fixes: same command: **388 passed**. Local real-Parquet tests ran, not skipped.
- `python3 -m compileall -q app tests scripts data_loader`: pass.
- `python3 scripts/secret_scan.py`: pass; this is a current-tree tripwire, not a proof
  about every historical blob or external credential rotation.
- `python3 scripts/verify_v01_workflows.py`: pass; its analytics interaction is explicitly
  demo/synthetic and is not counted as real analytics verification.
- Semantic release gate: **fails five checks**, intentionally recorded, not hidden in suite counts.
- Live PostgreSQL/Parquet and multi-source runs: executed independently; analytical
  acceptance remains blocked as explained above.

README, development status, handoff, capability matrix and integration-matrix current
summary now identify this blocked verdict. Removed stale missing-field/password/branch
claims from the current status/handoff; earlier milestones remain in Git history. ADRs
are historical decisions, not current live-status records, and were not rewritten.

UNVERIFIED_LIVE: full Web Evidence chain; Operational PostgreSQL persistence; comprehensive
upstream canonical pitch-grain and all-name reconciliation; earlier-than-March15 upstream
coverage. PARTIALLY_LIVE_VERIFIED: source/schema capability and complete population fidelity.
DEFERRED: same-period-last-year language, generalized temporal NLP, RAG, pgvector, extra
agents/infrastructure/statistics, and full Web Evidence when network blocks verification.
Those intentional deferrals are not the blockers above.

## Recommended adoption procedure — only after approval

Do not adopt this blocked tree. After fixes and independent approval:

1. Fetch and record current remote main SHA and approved review commit/tree SHA; verify
   clean state, source data assumptions and all release gates.
2. Preserve main lineage: create one `Adopt reviewed Baseball Agent v0.1` commit whose
   sole parent is the freshly verified main SHA and whose tree is exactly the approved
   tree (for example `git commit-tree <tree> -p <main>` on an adoption branch).
3. Verify parent and tree equality explicitly. Rerun suite, compile, secret scan and
   representative live/security gates on a checkout of that adoption commit.
4. After explicit authorization, fast-forward main without force. If main moved, rebuild
   the adoption commit on the new verified parent and repeat gates.
5. Only after explicit release authorization and successful main-tree gates, tagv0.1.0.

No unrelated-history merge, hundreds of cherry-picks, main rewrite, force-push or release
tag was performed by this review.
