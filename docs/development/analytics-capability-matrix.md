# Analytics source capability matrix

Status: **FINAL_REVIEW_BLOCKED**. Source reads are LIVE_VERIFIED; this is not an
end-to-end semantic correctness claim. See [final review](../reviews/v01-final-review.md).

Verified against the live `baseball_analytics` PostgreSQL database (role
`baseball_readonly`) and the local historical Parquet archive on 2026-09-16, after the
non-destructive migration that retained `sz_top`/`sz_bot`/`p_throws`.

## Verified schema summary

### PostgreSQL `baseball_analytics` (host 127.0.0.1:5433)

| Table | Grain | Coverage | Rows | Notes |
| --- | --- | --- | --- | --- |
| `statcast_pitches` | pitch | 2024-03-15 .. 2026-09-14 | 2,196,186 | `release_speed`, `pitch_type`, `plate_x`, `plate_z`, `zone`, `balls`, `strikes`, `launch_speed`, `batter_id`, `pitcher_id`, `game_date`, **`sz_top`, `sz_bot`, `p_throws`** all populated (new fields 100% non-null) |
| `batting_events` | event/at-bat | 2024-03-15 .. 2026-09-14 | 564,307 | has `batter_name` and `pitcher_name`; event-level results |
| `player_dictionary` | player | n/a | 3,726 | `player_id` → `player_name`; covers 2,050 of 2,050 distinct batters (100%) after StatsAPI backfill |

### Parquet archive (`data_loader/parquet_archive`, 2015–2023)

| File set | Grain | Coverage | Rows | Notes |
| --- | --- | --- | --- | --- |
| `mlb_statcast_*.parquet` | pitch | 2015-04-05 .. 2023-11-01 | 6,168,817 | `release_speed`, `pitch_type`, `plate_z`, `zone`, `balls`, `strikes`, `launch_speed`, `batter`, `pitcher`, `game_date`, **`sz_top`, `sz_bot`, `p_throws`** present (new fields 100% non-null); `player_name` is the **pitcher** name; exit velocity is `launch_speed` |

## Semantic capability matrix

| Semantic requirement | PostgreSQL | Parquet | Verdict |
| --- | --- | --- | --- |
| `exit_velocity` (ranking metric) | `launch_speed` | `launch_speed` | **EXACTLY_SUPPORTED** (both) |
| `pitch_velocity` (e.g. > 95 mph) | `release_speed` | `release_speed` | **EXACTLY_SUPPORTED** (both) |
| `pitch_type` fastball family | `pitch_type` → FF/SI/FC/FA | `pitch_type` → FF/SI/FC/FA | **EXACTLY_SUPPORTED** (both; codes explicit in `FieldMappingRegistry`) |
| `count` two-strike | `balls` / `strikes` | `balls` / `strikes` | **EXACTLY_SUPPORTED** (both) |
| `pitch_location` zone-based (upper third 1-3; above-zone 11-12) | `zone` | `zone` | **EXACTLY_SUPPORTED** (both) |
| `pitch_location` batter-relative upper edge | `plate_z` + `sz_top`/`sz_bot` (predicate `sz_top - 0.25 ft <= plate_z <= sz_top`) | `plate_z` + `sz_top`/`sz_bot` (predicate `sz_top - 0.25 ft <= plate_z <= sz_top`) | **EXACTLY_SUPPORTED** (both) |
| `ranking` (metric + direction + limit) | aggregation | aggregation | **EXACTLY_SUPPORTED** (both) |
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
and the 2024–2026 range was reloaded (2,196,186 rows, new fields 100% non-null).

**Resolved** for Parquet: the 2015–2023 archive was rebuilt with the updated loader
(6,168,817 rows, `sz_top`/`sz_bot`/`p_throws` 100% non-null), so historical
batter-relative location is now `EXACTLY_SUPPORTED`.

## Final-review qualifications

Schema/coverage metadata is declared statically, not discovered from each installation.
Fastball codes FF/SI/FC/FA are the explicit project definition. The AVG/MAX SQL contract
is explicit; natural-language extraction still loses explicit metric/filter instructions.
Qualification is still an adapter default, not frozen requirement semantics. Non-null
launch_speed includes fouls; the label batted_balls does not establish a balls-in-play
population. Zone codes11/12 mean upper outside quadrants, not strictly above sz_top.
Parquet provides IDs and only sparse seed names; PostgreSQL dictionary identity is unique.
Game type and canonical at_bat_number/pitch_number were discarded by both loaders.
PostgreSQL's March15 start reflects pybaseball's fallback season iterator. Neither
calendar-span overlap nor a populated sz_top column proves a complete ingestion window.
