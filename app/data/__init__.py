"""Data ingestion, historical archival, and audit functions migrated from ``data_loader``."""

from pathlib import Path
import os

import duckdb
import pandas as pd
from psycopg2.extras import execute_values
from pybaseball import cache, playerid_reverse_lookup, statcast

from app.config import Settings, settings
from app.tools.postgres import connect


PITCH_COLUMNS = [
    "game_date", "game_pk", "release_speed", "release_spin_rate", "pitch_type",
    "player_name", "pitcher", "batter", "events", "description", "plate_x", "plate_z",
    "stand", "balls", "strikes", "zone", "inning", "launch_speed", "launch_angle",
    "hit_distance_sc", "estimated_ba_using_speedangle", "estimated_woba_using_speedangle",
]


def enable_pybaseball_cache() -> None:
    """Enable pybaseball's local download cache, as in the legacy loaders."""
    cache.enable()


def ensure_hot_schema(cursor) -> None:
    """Create the legacy-compatible hot-store tables when absent."""
    from app.validation.policy import deny_analytics_write
    deny_analytics_write()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS player_dictionary (
            player_id INT PRIMARY KEY, player_name VARCHAR(150)
        );
        CREATE TABLE IF NOT EXISTS statcast_pitches (
            game_date DATE, game_pk BIGINT, release_speed FLOAT, release_spin_rate FLOAT,
            pitch_type VARCHAR(10), player_name VARCHAR(100), pitcher_id INT, batter_id INT,
            events VARCHAR(100), description VARCHAR(255), plate_x FLOAT, plate_z FLOAT,
            stand VARCHAR(5), balls INT, strikes INT, zone INT, inning INT, launch_speed FLOAT,
            launch_angle FLOAT, hit_distance_sc FLOAT, estimated_ba_using_speedangle FLOAT,
            estimated_woba_using_speedangle FLOAT
        );
        CREATE TABLE IF NOT EXISTS batting_events (
            game_date DATE, game_pk BIGINT, batter_id INT, batter_name VARCHAR(150),
            pitcher_id INT, pitcher_name VARCHAR(150), inning INT, events VARCHAR(100),
            launch_speed FLOAT, launch_angle FLOAT, hit_distance_sc FLOAT, stand VARCHAR(5),
            pitch_type VARCHAR(20), estimated_ba_using_speedangle FLOAT,
            estimated_woba_using_speedangle FLOAT
        );
    """)


def sync_player_dictionary(cursor, batter_ids: list[int]) -> None:
    """Register newly observed batters in the local player dictionary."""
    from app.validation.policy import deny_analytics_write
    deny_analytics_write()
    if not batter_ids:
        return
    cursor.execute("SELECT player_id FROM player_dictionary")
    known_ids = {row[0] for row in cursor.fetchall()}
    new_ids = list(set(batter_ids) - known_ids)
    if not new_ids:
        return
    dataframe = playerid_reverse_lookup(new_ids, key_type="mlbam")
    records = [
        (int(row.key_mlbam), f"{str(row.name_last).capitalize()}, {str(row.name_first).capitalize()}")
        for row in dataframe.itertuples()
    ]
    if records:
        execute_values(
            cursor,
            "INSERT INTO player_dictionary (player_id, player_name) VALUES %s ON CONFLICT (player_id) DO NOTHING",
            records,
        )


def fetch_and_append_mlb_data(
    start_date: str, end_date: str, config: Settings = settings
) -> int:
    """Fetch a range of Statcast pitches and regenerate its event-level projection."""
    from app.validation.policy import deny_analytics_write
    deny_analytics_write()
    enable_pybaseball_cache()
    dataframe = statcast(start_dt=start_date, end_dt=end_date)
    if dataframe is None or dataframe.empty:
        return 0
    missing_columns = set(PITCH_COLUMNS) - set(dataframe.columns)
    if missing_columns:
        raise ValueError(f"Statcast response missing columns: {sorted(missing_columns)}")
    pitches = dataframe[PITCH_COLUMNS].dropna(subset=["plate_x", "plate_z"]).copy()
    pitches["game_date"] = pd.to_datetime(pitches["game_date"]).dt.date
    for column in ("game_pk", "batter", "pitcher", "zone", "inning"):
        pitches[column] = pd.to_numeric(pitches[column], errors="coerce").astype("Int64")
    pitches = pitches.astype(object).where(pd.notnull(pitches), None)
    with connect(config) as connection, connection.cursor() as cursor:
        ensure_hot_schema(cursor)
        sync_player_dictionary(cursor, [int(value) for value in pitches["batter"].dropna().unique()])
        cursor.execute("DELETE FROM statcast_pitches WHERE game_date BETWEEN %s AND %s", (start_date, end_date))
        cursor.execute("DELETE FROM batting_events WHERE game_date BETWEEN %s AND %s", (start_date, end_date))
        execute_values(cursor, "INSERT INTO statcast_pitches VALUES %s", [tuple(row) for row in pitches.values])
        events = pitches[pitches["events"].notnull()].copy()
        if not events.empty:
            cursor.execute("SELECT player_id, player_name FROM player_dictionary")
            events["batter_name"] = events["batter"].map(dict(cursor.fetchall()))
            event_columns = [
                "game_date", "game_pk", "batter", "batter_name", "pitcher", "player_name",
                "inning", "events", "launch_speed", "launch_angle", "hit_distance_sc", "stand",
                "pitch_type", "estimated_ba_using_speedangle", "estimated_woba_using_speedangle",
            ]
            execute_values(cursor, "INSERT INTO batting_events VALUES %s", [tuple(row) for row in events[event_columns].values])
    return len(pitches)


def archive_mlb_history(
    start_year: int = 2015, end_year: int = 2023, config: Settings = settings
) -> list[Path]:
    """Archive missing Statcast seasons to one Snappy Parquet file per year."""
    from app.validation.policy import deny_analytics_write
    deny_analytics_write()
    enable_pybaseball_cache()
    archive_dir = config.parquet_archive_path
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived: list[Path] = []
    for year in range(start_year, end_year + 1):
        file_path = archive_dir / f"mlb_statcast_{year}.parquet"
        if file_path.exists():
            continue
        dataframe = statcast(start_dt=f"{year}-03-01", end_dt=f"{year}-11-10")
        if dataframe is None or dataframe.empty:
            continue
        clean = dataframe[PITCH_COLUMNS].dropna(subset=["plate_x", "plate_z"]).copy()
        clean["game_date"] = pd.to_datetime(clean["game_date"])
        for column in ("game_pk", "batter", "pitcher", "zone", "inning"):
            clean[column] = pd.to_numeric(clean[column], errors="coerce").astype("Int64")
        clean.to_parquet(file_path, engine="pyarrow", compression="snappy", index=False)
        archived.append(file_path)
    return archived


def audit_parquet_data(archive_dir: Path | None = None, config: Settings = settings) -> dict:
    """Run the existing availability, physics, and Aaron Judge record audits."""
    raise PermissionError("Validated read-only archive auditing is not yet available.")
    archive_dir = archive_dir or config.parquet_archive_path
    if not archive_dir.exists() or not any(archive_dir.iterdir()):
        raise FileNotFoundError(f"No Parquet archives found in {archive_dir}")
    parquet_pattern = archive_dir / "mlb_statcast_*.parquet"
    judge_file = archive_dir / "mlb_statcast_2022.parquet"
    with duckdb.connect(database=":memory:") as connection:
        total_rows = connection.execute(f"SELECT COUNT(*) FROM read_parquet('{parquet_pattern}')").fetchone()[0]
        by_year = connection.execute(f"SELECT EXTRACT(YEAR FROM game_date) AS year, COUNT(*) AS count FROM read_parquet('{parquet_pattern}') GROUP BY year ORDER BY year").fetchall()
        physics = connection.execute(f"SELECT MIN(release_speed), MAX(release_speed), AVG(release_speed), MAX(release_spin_rate), MAX(launch_speed) FROM read_parquet('{parquet_pattern}') WHERE release_speed > 0").fetchone()
        judge_home_runs = None
        if judge_file.exists():
            judge_home_runs = connection.execute(f"SELECT COUNT(*) FROM read_parquet('{judge_file}') WHERE batter = 592450 AND events = 'home_run'").fetchone()[0]
    return {"total_rows": total_rows, "yearly_distribution": by_year, "physics": physics, "aaron_judge_2022_home_runs": judge_home_runs}
