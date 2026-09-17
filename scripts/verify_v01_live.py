"""Bounded read-only probes. Prints verified status, never credentials."""

import json
import sys
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.tools.execution import DuckDBReadOnlyExecutor, PostgresReadOnlyExecutor


def main():
    report = {}
    result = PostgresReadOnlyExecutor(settings, allowed_tables=()).execute("SELECT 1 AS connection_check")
    report["analytics_postgres"] = {"status": "VERIFIED_LIVE" if result.status == "OK" else "UNVERIFIED_LIVE",
                                    "result": result.model_dump(mode="json")}
    files = sorted(settings.parquet_archive_path.glob("*.parquet"))
    if files:
        path = str(files[-1].resolve()).replace("'", "''")
        sql = (
            "SELECT batter, avg(launch_speed) AS exit_velocity, count(*) AS batted_balls "
            f"FROM read_parquet('{path}') WHERE strikes = 2 AND release_speed > 95 "
            "AND pitch_type IN ('FF', 'SI', 'FC') AND launch_speed IS NOT NULL "
            "AND plate_z BETWEEN sz_bot + 2 * (sz_top - sz_bot) / 3 AND sz_top "
            "AND abs(plate_x) <= 0.83 GROUP BY batter "
            "ORDER BY exit_velocity DESC, batter LIMIT 5")
        result, rows = DuckDBReadOnlyExecutor(settings.parquet_archive_path).execute_with_rows(sql)
        report["high_zone_ev"] = {"status": "VERIFIED_LIVE" if result.status == "OK" else "UNVERIFIED_LIVE",
                                  "result": result.model_dump(mode="json"), "rows": rows}
        result, rows = DuckDBReadOnlyExecutor(settings.parquet_archive_path).execute_with_rows(
            f"SELECT game_date, batter, launch_speed FROM read_parquet('{path}') LIMIT 5")
        report["duckdb_parquet"] = {"status": "VERIFIED_LIVE" if result.status == "OK" else "UNVERIFIED_LIVE",
            "file": files[-1].name, "result": result.model_dump(mode="json"), "rows": rows,
            "scope": "bounded historical archive read; complex high-zone query reported separately"}
    else:
        report["duckdb_parquet"] = {"status": "UNVERIFIED_LIVE", "reason": "No local archive"}
    try:
        with urlopen("https://statsapi.mlb.com/api/v1/teams?sportId=1", timeout=10) as response:
            payload = json.loads(response.read(1_000_000))
        count = len(payload.get("teams", []))
        report["public_web"] = {"status": "VERIFIED_LIVE" if count == 30 else "UNVERIFIED_LIVE",
                                "team_count": count, "scope": "MLB StatsAPI transport only"}
    except Exception as error:
        report["public_web"] = {"status": "UNVERIFIED_LIVE", "error_type": type(error).__name__}
    report["operational_postgres"] = {"status": "UNVERIFIED_LIVE",
                                       "reason": "No separate operational PostgreSQL connection configured"}
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
