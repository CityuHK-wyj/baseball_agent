# Analytics source capability matrix

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
| `mlb_statcast_*.parquet` | pitch | 2015-04-05 .. 2023-11-01 | 6,168,809 | `release_speed`, `pitch_type`, `plate_z`, `zone`, `balls`, `strikes`, `launch_speed`, `batter`, `pitcher`, `game_date` present; `player_name` is the **pitcher** name; **no `sz_top` / `sz_bot` / `p_throws`**; exit velocity is `launch_speed` |

## Semantic capability matrix

| Semantic requirement | PostgreSQL | Parquet | Verdict |
| --- | --- | --- | --- |
| `exit_velocity` (ranking metric) | `launch_speed` | `launch_speed` | **EXACTLY_SUPPORTED** (both) |
| `pitch_velocity` (e.g. > 95 mph) | `release_speed` | `release_speed` | **EXACTLY_SUPPORTED** (both) |
| `pitch_type` fastball family | `pitch_type` → FF/SI/FC/FA | `pitch_type` → FF/SI/FC/FA | **EXACTLY_SUPPORTED** (both; codes explicit in `FieldMappingRegistry`) |
| `count` two-strike | `balls` / `strikes` | `balls` / `strikes` | **EXACTLY_SUPPORTED** (both) |
| `pitch_location` zone-based (upper third 1-3; above-zone 11-12) | `zone` | `zone` | **EXACTLY_SUPPORTED** (both) |
| `pitch_location` batter-relative upper edge | `plate_z` + `sz_top`/`sz_bot` (predicate `plate_z >= sz_top - 0.25 ft`) | **missing `sz_top`/`sz_bot`** | **EXACTLY_SUPPORTED** (PostgreSQL); **UNSUPPORTED** (Parquet, until rebuilt) |
| `ranking` (metric + direction + limit) | aggregation | aggregation | **EXACTLY_SUPPORTED** (both) |
| `batter` identity | `batter_id` + `player_dictionary` (100% name coverage) | `batter` (id only; no name) | **EXACTLY_SUPPORTED** for id + name (Postgres); **EXACTLY_SUPPORTED** for id, **UNSUPPORTED** for name (Parquet) |
| `pitcher` identity | `pitcher_id` (name only in `batting_events`) | `pitcher` + `player_name` (pitcher name) | **APPROXIMATELY_SUPPORTED** (both, differently) |
| `game_date` window | `game_date` | `game_date` | **EXACTLY_SUPPORTED** (both) |
| `sz_top` / `sz_bot` | absent | absent | **UNSUPPORTED** (both) |
| `p_throws` | absent | absent | **UNSUPPORTED** (both) |

## Read-only enforcement (verified live)

`baseball_readonly` has:

- role attributes: not superuser, cannot create role/db, not replication/bypassrls;
- only `SELECT` grants on `statcast_pitches`, `batting_events`, `player_dictionary`;
- `ALTER ROLE ... SET default_transaction_read_only = on` (connection-wide read-only);
- live probes: `CREATE TABLE`, `INSERT`, `UPDATE`, `DELETE`, `DROP TABLE` all fail
  (`ReadOnlySqlTransaction` / missing grants).

The Agent runtime additionally parses SQL before connect and runs
`SET TRANSACTION READ ONLY`, so there are three independent read-only layers.

## Ingestion gap (resolved for PostgreSQL, pending for Parquet)

`sz_top`, `sz_bot` (and `p_throws`) are present in the upstream pybaseball Statcast
export but were omitted by the loader's hardcoded column list. This was an **ingestion
gap**, not a source limitation.

**Resolved** for PostgreSQL: the loaders now retain the three fields, the
`statcast_pitches` schema was migrated (idempotent `ALTER ... ADD COLUMN IF NOT EXISTS`),
and the 2024–2026 range was reloaded (2,196,186 rows, new fields 100% non-null).

**Pending** for Parquet: the historical loader (`archive_history_to_parquet.py`) now
retains the fields for future rebuilds, but the existing 2015–2023 archive was not
rebuilt, so Parquet batter-relative location remains `UNSUPPORTED` until a rebuild.
