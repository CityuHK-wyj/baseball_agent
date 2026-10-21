"""Offline mirrored execution for M2.12R3 development and frozen evaluations.

Builds an in-memory DuckDB relation from generated rows, writes the historical-format
Parquet fixture, and exposes a SchemaCatalog whose POSTGRES and PARQUET physical
sources both read that fixture. This keeps contract/coverage attacks executable without
a live PostgreSQL server while preserving the real compiler/SQL path. It is evaluation
infrastructure only and is never imported by production.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
from pathlib import Path

from app.capabilities.analytics.catalog import CATALOG, SchemaCatalog
from app.capabilities.analytics.compiler import render_literal
from app.capabilities.analytics.conformance import SourceAvailability
from app.capabilities.analytics.executor import DuckDBExecutor, QueryResult


class OfflineExecutor:
    def __init__(self, archive_dir: str | Path):
        self.archive_dir = str(archive_dir)
        self.duck = DuckDBExecutor(self.archive_dir)
        self.calls = 0

    def run(self, sql: str, source: str | None = None) -> QueryResult:
        if "public.player_dictionary" in sql:
            return QueryResult([], [], 0, source or "POSTGRES", 0, "names")
        self.calls += 1
        return self.duck.run(sql)

    def availability(self, source: str, relation: str) -> SourceAvailability:
        return SourceAvailability(source=source, relation=relation, known=True,
                                  start=dt.date(2010, 1, 1), end=dt.date(2030, 12, 31),
                                  seasons=tuple(range(2010, 2031)), note="offline mirrored fixture")


def offline_mirror(c: dict, path: Path):
    import duckdb

    path.mkdir(parents=True, exist_ok=True)
    columns = list(c["rows"][0])
    values = ", ".join(
        "(" + ", ".join(
            ("DATE " + render_literal(row[k])) if isinstance(row[k], dt.date) else render_literal(row[k])
            for k in columns
        ) + ")"
        for row in c["rows"]
    )
    physical = "(VALUES " + values + ") AS fixture(" + ", ".join(columns) + ")"
    file = path / f"mlb_statcast_{c['raw']['season']}_{c['seed']}.parquet"
    db = duckdb.connect(":memory:")
    db.execute("CREATE TABLE fixture AS SELECT * FROM " + physical)
    db.execute(f"COPY fixture TO '{file}' (FORMAT PARQUET)")
    db.close()
    spec = dataclasses.replace(
        CATALOG.table("pitch"),
        physical={"POSTGRES": physical, "PARQUET": f"read_parquet('{file}')"},
        key_map={"POSTGRES": CATALOG.table("pitch").key_map["POSTGRES"],
                 "PARQUET": CATALOG.table("pitch").key_map["POSTGRES"]},
    )
    catalog = SchemaCatalog({"pitch": spec})
    executor = OfflineExecutor(path)
    return catalog, executor, file


def fixture_digest(file: Path) -> str:
    return hashlib.sha256(file.read_bytes()).hexdigest()
