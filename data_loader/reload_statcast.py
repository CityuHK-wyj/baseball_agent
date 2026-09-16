"""Resumable full reload of the PostgreSQL Statcast range with the canonical schema.

Run as a maintenance tool (baseball_admin + POSTGRES_ADMIN_PASSWORD), never from the
read-only Agent runtime. It fetches month-sized chunks, skips chunks whose rows already
carry sz_top, and prints one concise line per chunk.

Usage (export the admin credential first, then run):
    python3 -m data_loader.reload_statcast --start 2024-01-01 --end 2026-09-15
"""

import argparse
import os
import sys
import time
from datetime import date, timedelta

import psycopg2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_loader.fetch_and_load import fetch_and_append_mlb_data  # noqa: E402


def _month_chunks(start: date, end: date):
    cursor = start
    while cursor <= end:
        if cursor.month == 12:
            month_end = date(cursor.year, 12, 31)
        else:
            month_end = date(cursor.year, cursor.month + 1, 1) - timedelta(days=1)
        chunk_end = min(month_end, end)
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def _already_loaded(cur, start: date, end: date) -> bool:
    cur.execute(
        "SELECT COUNT(*), COUNT(sz_top) FROM statcast_pitches "
        "WHERE game_date >= %s AND game_date <= %s",
        (start, end))
    total, with_sz = cur.fetchone()
    return total > 0 and total == with_sz


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()

    if not os.environ.get("POSTGRES_ADMIN_PASSWORD"):
        raise RuntimeError("POSTGRES_ADMIN_PASSWORD is required")

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    connection = psycopg2.connect(
        host="127.0.0.1", database="baseball_analytics",
        user="baseball_admin", password=os.environ["POSTGRES_ADMIN_PASSWORD"], port="5433")
    connection.autocommit = True
    cur = connection.cursor()
    started = time.time()
    for chunk_start, chunk_end in _month_chunks(start, end):
        if _already_loaded(cur, chunk_start, chunk_end):
            print(f"[skip] {chunk_start}..{chunk_end} already populated", flush=True)
            continue
        t0 = time.time()
        fetch_and_append_mlb_data(chunk_start.isoformat(), chunk_end.isoformat())
        print(f"[ok] {chunk_start}..{chunk_end} in {time.time() - t0:.0f}s", flush=True)
    cur.close()
    connection.close()
    print(f"done in {time.time() - started:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
