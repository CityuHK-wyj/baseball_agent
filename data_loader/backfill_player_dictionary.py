"""Backfill player_dictionary from the authoritative MLB StatsAPI people endpoint.

The local chadwick register (used by pybaseball.playerid_reverse_lookup) misses recent
minor-league/spring call-ups, so batter-name coverage was ~913 of 2,039 distinct batters.
This maintenance script resolves the missing MLBAM ids against StatsAPI (canonical source)
in batches and stores "LastName, FirstName" to match the existing dictionary format.

Run as baseball_admin (write) outside the read-only Agent runtime.
"""

import argparse
import os
import time

import psycopg2
import requests

_BATCH = 50
_URL = "https://statsapi.mlb.com/api/v1/people"


def _fetch_names(ids):
    response = requests.get(
        _URL, params={"personIds": ",".join(str(i) for i in ids)}, timeout=30,
        headers={"User-Agent": "baseball-agent-maintenance/1.0"})
    response.raise_for_status()
    result = {}
    for person in response.json().get("people", []):
        first = person.get("firstName") or ""
        last = person.get("lastName") or ""
        if last or first:
            result[int(person["id"])] = f"{last}, {first}".strip(", ")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default="5433")
    parser.add_argument("--db", default="baseball_analytics")
    args = parser.parse_args()

    if not os.environ.get("POSTGRES_ADMIN_PASSWORD"):
        raise RuntimeError("POSTGRES_ADMIN_PASSWORD is required")

    conn = psycopg2.connect(host=args.host, port=args.port, dbname=args.db,
                            user="baseball_admin",
                            password=os.environ["POSTGRES_ADMIN_PASSWORD"])
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT id FROM (
            SELECT batter_id AS id FROM statcast_pitches
            UNION
            SELECT pitcher_id AS id FROM statcast_pitches
        ) t
        WHERE id IS NOT NULL
        EXCEPT SELECT player_id FROM player_dictionary
    """)
    missing = sorted(r[0] for r in cur.fetchall())
    print(f"missing ids: {len(missing)}", flush=True)

    resolved = 0
    for index in range(0, len(missing), _BATCH):
        batch = missing[index:index + _BATCH]
        for attempt in range(3):
            try:
                names = _fetch_names(batch)
                break
            except Exception as error:  # noqa: BLE001
                print(f"  batch retry {attempt + 1}: {type(error).__name__}", flush=True)
                time.sleep(2)
                names = {}
        for player_id, name in names.items():
            cur.execute(
                "INSERT INTO player_dictionary (player_id, player_name) "
                "VALUES (%s, %s) ON CONFLICT (player_id) DO UPDATE SET player_name = EXCLUDED.player_name",
                (player_id, name))
            resolved += 1
        time.sleep(0.2)

    cur.execute("SELECT COUNT(*), COUNT(DISTINCT player_id) FROM player_dictionary")
    total, distinct = cur.fetchone()
    cur.execute("SELECT COUNT(DISTINCT batter_id) FROM statcast_pitches")
    batters = cur.fetchone()[0]
    cur.close()
    conn.close()
    print(f"resolved this run: {resolved}; dictionary: {distinct} distinct; "
          f"batters: {batters}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
