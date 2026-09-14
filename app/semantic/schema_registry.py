"""Schema Registry: deterministic table/column knowledge for planning.

Physical schema mapping lives here, not in the Planner (D015), so semantic planning
stays decoupled from a specific source layout.
"""

import re

from app.models.schema import SchemaTable

_TOKEN = re.compile(r"[a-z0-9_]+")


class SchemaRegistry:
    def __init__(self, tables: tuple[SchemaTable, ...] = ()) -> None:
        self._tables = tuple(tables)
        names = [item.table_name for item in self._tables]
        if len(set(names)) != len(names):
            raise ValueError("Schema table names must be unique")

    def get(self, table_name: str) -> SchemaTable:
        for item in self._tables:
            if item.table_name == table_name:
                return item
        raise KeyError(f"Unknown table {table_name!r}")

    def tables(self) -> tuple[SchemaTable, ...]:
        return self._tables

    def search(self, query: str) -> tuple[SchemaTable, ...]:
        tokens = set(_TOKEN.findall(query.lower()))
        if not tokens:
            return self._tables
        matches = []
        for item in self._tables:
            haystack = " ".join((item.table_name, item.source_kind, item.description, *item.columns)).lower()
            if tokens & set(_TOKEN.findall(haystack)):
                matches.append(item)
        return tuple(matches)
