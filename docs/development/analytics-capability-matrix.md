# Analytics source capability matrix

Verified against the live `baseball_analytics` PostgreSQL database (role
`baseball_readonly`) and the local historical Parquet archive on 2026-09-16. No schema
was altered during the audit.

## Verified schema summary

### PostgreSQL `baseball_analytics` (host 127.0.0.1:5433)

| Table | Grain | Coverage | Rows | Notes |
| --- | --- | --- | --- | --- |
| `statcast_pitches` | pitch | 2024-03-15 .. 2026-06-18 | 1,861,212 | `release_speed`, `pitch_type`, `plate_x`, `plate_z`, `zone`, `balls`, `strikes`, `launch_speed`, `batter_id`, `pitcher_id`, `game_date` all populated; **no `sz_top` / `sz_bot` / `p_throws`** |
| `batting_events` | event/at-bat | 2024-03-15 .. 2026-06-18 | 478,250 | has `batter_name` and `pitcher_name`; event-level results |
| `player_dictionary` | player | n/a | 913 | `player_id` → `player_name`; covers 913 of 2,039 distinct batters (~45%) |

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
| `pitch_location` batter-relative upper edge | **missing `sz_top`/`sz_bot`** | **missing `sz_top`/`sz_bot`** | **UNSUPPORTED** (both; never silently substituted) |
| `ranking` (metric + direction + limit) | aggregation | aggregation | **EXACTLY_SUPPORTED** (both) |
| `batter` identity | `batter_id` + `player_dictionary` (45% name coverage) | `batter` (id only; no name) | **EXACTLY_SUPPORTED** for id; **APPROXIMATELY_SUPPORTED** for name (Postgres), **UNSUPPORTED** for name (Parquet) |
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

## Ingestion gap (reported, not applied)

`sz_top`, `sz_bot` (and `p_throws`) are present in the upstream pybaseball Statcast
export but were omitted by the loader's hardcoded column list:

- `data_loader/archive_history_to_parquet.py` (`target_columns`)
- `data_loader/fetch_and_load.py` (`pitch_columns` and the `statcast_pitches` DDL)

This is an **ingestion gap**, not a source limitation. Recommended non-destructive
migration (do not run without approval): add `sz_top FLOAT`, `sz_bot FLOAT`,
`p_throws VARCHAR(5)` to the loader column list and `statcast_pitches` DDL, then
incrementally backfill the affected windows. Doing so would make
`BATTER_RELATIVE_UPPER_EDGE` `EXACTLY_SUPPORTED`. Until then it stays `UNSUPPORTED`
and the Agent must not silently substitute a zone set.
