"""Read-only execution of compiled analytical SQL over PostgreSQL and DuckDB/Parquet."""

from __future__ import annotations

import datetime as dt
import decimal
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.capabilities.analytics.catalog import SOURCE_PARQUET, SOURCE_POSTGRES
from app.capabilities.analytics.conformance import SourceAvailability, digest


@dataclass
class QueryResult:
    columns: list[dict[str, Any]]
    rows: list[dict[str, Any]]
    row_count: int
    source: str
    duration_ms: int
    sql_hash: str
    error: str = ""
    error_code: str = ""


def _jsonable(value: Any) -> Any:
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


class PostgresExecutor:
    def __init__(self, config):
        self.config = config

    def run(self, sql: str, limit_hint: int | None = None) -> QueryResult:
        import time

        import psycopg2
        import psycopg2.extras

        started = time.monotonic()
        sql_hash = hashlib.sha256(sql.encode("utf-8")).hexdigest()[:16]
        try:
            conn = psycopg2.connect(
                host=self.config.host,
                port=self.config.port,
                dbname=self.config.db,
                user=self.config.user,
                password=self.config.password,
                connect_timeout=self.config.connect_timeout,
                application_name="baseball_agent_readonly",
                options="-c default_transaction_read_only=on -c statement_timeout=25000",
            )
        except Exception as exc:  # noqa: BLE001
            return QueryResult([], [], 0, SOURCE_POSTGRES, 0, sql_hash, f"{type(exc).__name__}: {exc}", "SOURCE_UNAVAILABLE")
        try:
            with conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SET TRANSACTION READ ONLY")
                    cur.execute(sql)
                    rows = [dict(r) for r in cur.fetchall()]
                    columns = [
                        {"name": d.name, "type": _type_name(d.type_code)} for d in (cur.description or [])
                    ]
        except Exception as exc:  # noqa: BLE001
            conn.close()
            return QueryResult([], [], 0, SOURCE_POSTGRES, int((time.monotonic() - started) * 1000), sql_hash, f"{type(exc).__name__}: {exc}", "QUERY_FAILED")
        conn.close()
        duration = int((time.monotonic() - started) * 1000)
        return QueryResult(columns, [{k: _jsonable(v) for k, v in row.items()} for row in rows], len(rows), SOURCE_POSTGRES, duration, sql_hash)


def _type_name(type_code: Any) -> str:
    import psycopg2.extensions as ext

    try:
        value = ext.string_types[type_code]
        return str(getattr(value, "name", type_code))
    except Exception:  # noqa: BLE001
        return str(type_code)


class DuckDBExecutor:
    def __init__(self, archive_path: str, *, memory_limit: str = "1GB", threads: int = 2):
        self.archive_path = str(Path(archive_path))
        self.memory_limit = memory_limit
        self.threads = threads

    def _connect(self):
        import duckdb

        con = duckdb.connect(database=":memory:")
        con.execute(f"SET memory_limit='{self.memory_limit}'")
        con.execute(f"SET threads={int(self.threads)}")
        try:
            con.execute("SET enable_external_access=true")
            con.execute(f"SET allowed_directories=['{self.archive_path}']")
        except Exception:  # noqa: BLE001 - older DuckDB fallback
            pass
        return con

    def run(self, sql: str, limit_hint: int | None = None) -> QueryResult:
        import time

        started = time.monotonic()
        sql_hash = hashlib.sha256(sql.encode("utf-8")).hexdigest()[:16]
        try:
            con = self._connect()
        except Exception as exc:  # noqa: BLE001
            return QueryResult([], [], 0, SOURCE_PARQUET, 0, sql_hash, f"{type(exc).__name__}: {exc}", "SOURCE_UNAVAILABLE")
        try:
            relation = con.execute(sql)
            columns = [{"name": d[0], "type": str(d[1])} for d in relation.description]
            rows = [dict(zip([c["name"] for c in columns], row)) for row in relation.fetchall()]
        except Exception as exc:  # noqa: BLE001
            con.close()
            return QueryResult([], [], 0, SOURCE_PARQUET, int((time.monotonic() - started) * 1000), sql_hash, f"{type(exc).__name__}: {exc}", "QUERY_FAILED")
        con.close()
        duration = int((time.monotonic() - started) * 1000)
        return QueryResult(columns, [{k: _jsonable(v) for k, v in row.items()} for row in rows], len(rows), SOURCE_PARQUET, duration, sql_hash)


class AnalyticalExecutor:
    """Routes a compiled query to the correct read-only engine."""

    def __init__(self, pg_config, archive_path: str):
        self.postgres = PostgresExecutor(pg_config)
        self.duckdb = DuckDBExecutor(archive_path)
        self._availability: dict[tuple[str, str], SourceAvailability] = {}

    def availability(self, source: str, relation: str = "pitch") -> SourceAvailability:
        """Observed availability, not catalog promises. No fabricated snapshot identity.

        Observation is cached per executor/turn and timestamped; it is not an assertion
        that separate PostgreSQL transactions share a snapshot.
        """
        from app.capabilities.analytics.catalog import CATALOG
        import re
        key = (source, relation)
        if key in self._availability:
            return self._availability[key]
        partitions = []
        seasons = []
        if source == SOURCE_PARQUET:
            for path in sorted(Path(self.duckdb.archive_path).glob("mlb_statcast_*.parquet")):
                match = re.fullmatch(r"mlb_statcast_(\d{4})\.parquet", path.name)
                if match:
                    seasons.append(int(match.group(1)))
                    stat = path.stat()
                    partitions.append(f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}")
        try:
            table = CATALOG.physical_table(source, relation, parquet_archive=self.duckdb.archive_path)
            measured = self.run(f"SELECT MIN(game_date) AS first, MAX(game_date) AS last, COUNT(*) AS n FROM {table}", source)
            if measured.error or not measured.rows:
                return SourceAvailability(source=source, relation=relation, note="availability observation failed")
            row = measured.rows[0]
            first = dt.date.fromisoformat(str(row['first'])[:10]) if row.get('first') else None
            last = dt.date.fromisoformat(str(row['last'])[:10]) if row.get('last') else None
            if source == SOURCE_POSTGRES and first and last:
                years = self.run(f"SELECT DISTINCT EXTRACT(YEAR FROM game_date) AS year FROM {table}", source)
                if years.error:
                    return SourceAvailability(source=source, relation=relation, note="observed seasons could not be established")
                seasons = sorted(int(r["year"]) for r in years.rows if r.get("year") is not None and int(r["year"]) < dt.date.today().year)
            observed = dt.datetime.now(dt.timezone.utc).isoformat()
            result = SourceAvailability(source=source, relation=relation, known=True,
                start=first, end=last, seasons=tuple(seasons), partitions=tuple(partitions),
                observed_at=observed, version=digest(dict(source=source,relation=relation,rows=row,partitions=partitions)),
                note=f"observed {source} availability: {first}..{last}; as of {observed}")
            self._availability[key] = result
            return result
        except (OSError, ValueError, KeyError):
            return SourceAvailability(source=source,relation=relation,note="availability could not be established")

    def run(self, sql: str, source: str) -> QueryResult:
        if source == SOURCE_POSTGRES:
            return self.postgres.run(sql)
        if source == SOURCE_PARQUET:
            return self.duckdb.run(sql)
        return QueryResult([], [], 0, source, 0, "", f"unknown source '{source}'", "SOURCE_UNAVAILABLE")
