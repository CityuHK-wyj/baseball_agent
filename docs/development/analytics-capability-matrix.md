# Analytics source capability matrix

Current independent verdict: **FINAL_REVIEW_BLOCKED** on
`codex/v0.1-final-hybrid-review`, reviewing Pi `ebdccc75793ebd2cf448e1b199f2a16109e5ca7e`.
[Final hybrid review](../reviews/v01-final-hybrid-review.md): 479 tests pass, but nine new adversarial cases fail.
Real DeepSeek extraction and PostgreSQL/Parquet execution are LIVE_VERIFIED;
semantic containment and partial fallback fidelity fail. The original/compositional
blockers are repaired. Main and tags remain untouched. Web Evidence and Operational
PostgreSQL remain UNVERIFIED_LIVE; generalized temporal NLP and RAG remain DEFERRED.

Historical independent verdict (2026-09-17): **FINAL_REVIEW_BLOCKED** on
`codex/v0.1-semantic-recheck`. [Semantic re-review](../reviews/v01-semantic-recheck.md)
records the remaining five compositional failures, regression-backed fixes, and live evidence.
Baseline: 432 tests; corrected review: 435 tests, no skips. The original blocker gate
passes, but the expanded NL-to-SQL release gate fails. No main adoption or tag is authorized
while those P1 defects remain. Repair-checkpoint claims below are not release approval.

Status: **FINAL_REVIEW_BLOCKED**. Source reads are LIVE_VERIFIED on the semantic-fix
branch; the independent recheck is pending. See
[final review](../reviews/v01-final-review.md) for the originally blocked baseline and
[the population ADR](../adr/0020-analytical-population-and-qualification.md) for the
population and qualification contract.

Verified against the live `baseball_analytics` PostgreSQL database (role
`baseball_readonly`) and the local historical Parquet archive on 2026-09-16, after the
non-destructive migration that retained `sz_top`/`sz_bot`/`p_throws` and the additive
`game_type` backfill.

## Verified schema summary

### PostgreSQL `baseball_analytics` (host 127.0.0.1:5433)

| Table | Grain | Coverage | Rows | Notes |
| --- | --- | --- | --- | --- |
| `statcast_pitches` | pitch | 2024-03-15 .. 2026-09-14 | 2,196,186 | `release_speed`, `pitch_type`, `plate_x`, `plate_z`, `zone`, `balls`, `strikes`, `launch_speed`, `batter_id`, `pitcher_id`, `game_date`, **`sz_top`, `sz_bot`, `p_throws`, `game_type`** all populated (new fields 100% non-null) |
| `batting_events` | event/at-bat | 2024-03-15 .. 2026-09-14 | 564,307 | has `batter_name` and `pitcher_name`; event-level results |
| `player_dictionary` | player | n/a | 3,726 | `player_id` → `player_name`; covers 2,050 of 2,050 distinct batters (100%) after StatsAPI backfill |

### Parquet archive (`data_loader/parquet_archive`, 2015–2023)

| File set | Grain | Coverage | Rows | Notes |
| --- | --- | --- | --- | --- |
| `mlb_statcast_*.parquet` | pitch | 2015-04-05 .. 2023-11-01 | 6,168,817 | `release_speed`, `pitch_type`, `plate_z`, `zone`, `balls`, `strikes`, `launch_speed`, `batter`, `pitcher`, `game_date`, **`sz_top`, `sz_bot`, `p_throws`, `game_type`** present (new fields 100% non-null); `player_name` is the **pitcher** name; exit velocity is `launch_speed` |

## Semantic capability matrix

| Semantic requirement | PostgreSQL | Parquet | Verdict |
| --- | --- | --- | --- |
| `exit_velocity` (ranking metric) | `launch_speed` | `launch_speed` | **EXACTLY_SUPPORTED** (both) |
| `pitch_velocity` (e.g. > 95 mph) | `release_speed` | `release_speed` | **EXACTLY_SUPPORTED** (both) |
| `pitch_type` fastball family | `pitch_type` → FF/SI/FC/FA | `pitch_type` → FF/SI/FC/FA | **EXACTLY_SUPPORTED** (both; codes explicit in `FieldMappingRegistry`) |
| `count` two-strike | `balls` / `strikes` | `balls` / `strikes` | **EXACTLY_SUPPORTED** (both) |
| `pitch_location` zone-based (upper third 1-3; upper outside 11-12) | `zone` | `zone` | **EXACTLY_SUPPORTED** (both) |
| `pitch_location` batter-relative upper edge | `plate_z` + `sz_top`/`sz_bot` (predicate `sz_top - 0.25 ft <= plate_z <= sz_top`) | `plate_z` + `sz_top`/`sz_bot` (predicate `sz_top - 0.25 ft <= plate_z <= sz_top`) | **EXACTLY_SUPPORTED** (both) |
| `ranking` (metric + direction + limit + aggregation) | aggregation | aggregation | **EXACTLY_SUPPORTED** (both) |
| `population` game type (`game_type`) | `game_type` (R/F/D/L/W/S/E/A) | `game_type` | **EXACTLY_SUPPORTED** (both, after additive backfill from StatsAPI) |
| `population` event grain | `description` / `launch_speed` | `description` / `launch_speed` | **EXACTLY_SUPPORTED** for fair batted balls (`description = 'hit_into_play'`) and measured contact |
| `qualification` minimum batted balls | frozen `QualificationRule` → `HAVING COUNT(*)` | frozen `QualificationRule` → `HAVING COUNT(*)` | **EXACTLY_SUPPORTED** (both; explicit user threshold never replaced by the default) |
| `batter` identity | `batter_id` + `player_dictionary` (100% name coverage) | `batter` (id only; no name) | **EXACTLY_SUPPORTED** for id + name (Postgres); **EXACTLY_SUPPORTED** for id, **UNSUPPORTED** for name (Parquet) |
| `pitcher` identity | `pitcher_id` (name only in `batting_events`) | `pitcher` + `player_name` (pitcher name) | **APPROXIMATELY_SUPPORTED** (both, differently) |
| `game_date` window | `game_date` | `game_date` | **EXACTLY_SUPPORTED** (both) |
| `sz_top` / `sz_bot` | present, non-null | present, non-null | LIVE_VERIFIED physical fields |
| `p_throws` | present, non-null | present, non-null | LIVE_VERIFIED physical field |

## Read-only enforcement (verified live)

`baseball_readonly` has:

- role attributes: not superuser, cannot create role/db, not replication/bypassrls;
- only `SELECT` grants on `statcast_pitches`, `batting_events`, `player_dictionary`;
- `ALTER ROLE ... SET default_transaction_read_only = on` (connection-wide read-only);
- live probes: `CREATE TABLE`, `INSERT`, `UPDATE`, `DELETE`, `DROP TABLE` all fail
  (`ReadOnlySqlTransaction` / missing grants).

The Agent runtime additionally parses SQL before connect and runs
`SET TRANSACTION READ ONLY`, so there are three independent read-only layers.

## Retained-field ingestion gap (resolved for both sources)

`sz_top`, `sz_bot` (and `p_throws`) are present in the upstream pybaseball Statcast
export but were omitted by the loader's hardcoded column list. This was an **ingestion
gap**, not a source limitation.

**Resolved** for PostgreSQL: the loaders now retain the three fields, the
`statcast_pitches` schema was migrated (idempotent `ALTER ... ADD COLUMN IF NOT EXISTS`),
and the 2024-2026 range was reloaded (2,196,186 rows, new fields 100% non-null).

**Resolved** for Parquet: the 2015-2023 archive was rebuilt with the updated loader
(6,168,817 rows, `sz_top`/`sz_bot`/`p_throws` 100% non-null), so historical
batter-relative location is now `EXACTLY_SUPPORTED`.

`game_type` was a second, analogous ingestion gap: the upstream export carries it but the
loaders discarded it. Both loaders now retain it, and
`python3 -m data_loader.backfill_game_type` adds it non-destructively to already-stored
rows from the authoritative StatsAPI `gamePk -> gameType` schedule mapping. All 20,956
Parquet and 7,502 PostgreSQL game keys resolve; row counts are unchanged.

## Analytical population and qualification contract

The v0.1 contract is explicit and reproducible rather than implicit in SQL:

- default `game_types=("REGULAR_SEASON",)` → `game_type IN ('R')`;
- default `event_population="BATTED_BALL"` → `description = 'hit_into_play'` in addition to the
  metric not being null; `MEASURED_CONTACT` deliberately includes measured fouls and
  `ALL_PITCHES` omits the event predicate;
- an explicit user threshold (`minimum 20 batted balls`) is frozen as
  `QualificationRule.min_batted_balls`; the adapter applies `HAVING COUNT(*) >= 20` and
  never substitutes its documented default of 3;
- a source lacking `game_type` or `events` fails closed with `MISSING_PHYSICAL_FIELDS`.

## Final-review qualifications

Schema/coverage metadata is declared statically, not discovered from each installation.
Fastball codes FF/SI/FC/FA are the explicit project definition. The AVG/MAX SQL contract
is explicit and the natural-language parser preserves explicit metric, filter, count and
aggregation wording. Qualification is frozen requirement semantics, not an adapter
default. The default batted-ball population is fair balls in play (`description = 'hit_into_play'`),
not measured contact, and the analyzed population is visible on the artifact. Zone codes
11/12 are named and described as upper outside quadrants, not a strict above-sz_top
predicate. Parquet provides IDs and only sparse seed names; PostgreSQL dictionary identity
is unique. PostgreSQL's March15 start reflects pybaseball's fallback season iterator.
Neither calendar-span overlap nor a populated `game_type` column proves a complete
ingestion window.
