"""Backfill the retained ``game_type`` field on already-ingested Statcast data.

The upstream pybaseball/Savant Statcast export carries ``game_type`` (R/F/D/L/W/S/E/A),
but the original loaders discarded it, so the stored PostgreSQL table and Parquet
archive have no game-type column. Re-fetching millions of pitches is unnecessary: MLB
StatsAPI publishes the authoritative ``gamePk -> gameType`` mapping per season, and every
retained row already carries ``game_pk``.

This is a maintenance tool (run as ``baseball_admin`` for the PostgreSQL part), never
imported by the read-only Agent runtime. It is additive: it only adds a column/value and
never drops rows or other columns. Run ``--parquet`` to rewrite the gitignored archive and
``--postgres`` to migrate the hot table. A cached mapping is reused when present.

Usage:
    python3 -m data_loader.backfill_game_type --fetch-only
    python3 -m data_loader.backfill_game_type --parquet
    python3 -m data_loader.backfill_game_type --postgres   # requires the admin credential in the environment
"""

import argparse
import json
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from app.config import settings  # noqa: E402

STATSAPI_SCHEDULE = "https://statsapi.mlb.com/api/v1/schedule"
DEFAULT_MAP_PATH = _PROJECT_ROOT / ".runtime" / "game_type_map.json"
_SEASONS = tuple(range(2015, 2027))


def fetch_game_type_map(map_path: Path, seasons=_SEASONS) -> dict[int, str]:
    import requests
    mapping: dict[int, str] = {}
    for season in seasons:
        response = requests.get(STATSAPI_SCHEDULE, params={
            "sportId": 1, "startDate": f"{season}-01-01", "endDate": f"{season}-12-31"},
            timeout=60)
        response.raise_for_status()
        for day in response.json().get("dates", []):
            for game in day.get("games", []):
                game_pk, game_type = game.get("gamePk"), game.get("gameType")
                if game_pk is not None and game_type:
                    mapping[int(game_pk)] = str(game_type)
    map_path.parent.mkdir(parents=True, exist_ok=True)
    map_path.write_text(json.dumps({str(k): v for k, v in mapping.items()}))
    return mapping


def load_game_type_map(map_path: Path = DEFAULT_MAP_PATH) -> dict[int, str]:
    if map_path.exists():
        return {int(k): str(v) for k, v in json.loads(map_path.read_text()).items()}
    return fetch_game_type_map(map_path)


def backfill_parquet(archive_dir: Path, mapping: dict[int, str]) -> int:
    import duckdb
    files = sorted(archive_dir.glob("mlb_statcast_*.parquet"))
    if not files:
        raise FileNotFoundError(f"No Parquet files under {archive_dir}")
    connection = duckdb.connect()
    try:
        connection.execute("CREATE TABLE game_type_map(game_pk BIGINT PRIMARY KEY, game_type VARCHAR)")
        connection.executemany("INSERT INTO game_type_map VALUES (?, ?)", list(mapping.items()))
        updated = 0
        for path in files:
            columns = [row[0] for row in connection.execute(
                f"DESCRIBE SELECT * FROM read_parquet('{path}')").fetchall()]
            select = "p.*" if "game_type" not in columns else \
                "p.* EXCLUDE (game_type)"
            temporary = path.with_suffix(".parquet.tmp")
            connection.execute(
                f"COPY (SELECT {select}, g.game_type FROM read_parquet('{path}') p "
                f"LEFT JOIN game_type_map g USING (game_pk)) "
                f"TO '{temporary}' (FORMAT PARQUET, COMPRESSION SNAPPY)")
            os.replace(temporary, path)
            updated += 1
        return updated
    finally:
        connection.close()


def backfill_postgres(mapping: dict[int, str]) -> int:
    import psycopg2
    from psycopg2.extras import execute_values
    password = os.environ.get("POSTGRES_ADMIN_PASSWORD")
    if not password:
        raise RuntimeError("POSTGRES_ADMIN_PASSWORD is required for --postgres")
    connection = psycopg2.connect(host="127.0.0.1", port=5433, dbname="baseball_analytics",
                                  user="baseball_admin", password=password)
    try:
        with connection, connection.cursor() as cur:
            cur.execute("ALTER TABLE statcast_pitches ADD COLUMN IF NOT EXISTS game_type VARCHAR(4)")
            cur.execute("CREATE TEMP TABLE _game_type_map(game_pk BIGINT PRIMARY KEY, game_type VARCHAR(4))")
            execute_values(cur, "INSERT INTO _game_type_map (game_pk, game_type) VALUES %s",
                           list(mapping.items()))
            cur.execute("""
                UPDATE statcast_pitches p SET game_type = g.game_type
                FROM _game_type_map g
                WHERE p.game_pk = g.game_pk
                  AND p.game_type IS DISTINCT FROM g.game_type
            """)
            return cur.rowcount
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--map-file", default=str(DEFAULT_MAP_PATH))
    parser.add_argument("--archive-dir", default=str(settings.parquet_archive_path))
    parser.add_argument("--fetch-only", action="store_true")
    parser.add_argument("--parquet", action="store_true")
    parser.add_argument("--postgres", action="store_true")
    args = parser.parse_args()

    map_path = Path(args.map_file)
    mapping = load_game_type_map(map_path)
    print(f"game_type mapping: {len(mapping)} games ({map_path})")
    if args.fetch_only:
        return 0
    if args.parquet:
        print(f"parquet files rewritten: {backfill_parquet(Path(args.archive_dir), mapping)}")
    if args.postgres:
        print(f"postgres rows updated: {backfill_postgres(mapping)}")
    if not (args.parquet or args.postgres):
        print("nothing to do; pass --fetch-only, --parquet and/or --postgres")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
