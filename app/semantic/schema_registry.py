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


# Verified against the real archive (2015-2023 Parquet) and the loader DDL (PostgreSQL).
# Neither local source carries ``sz_top`` / ``sz_bot``, so the exact batter-relative
# upper-edge definition is deliberately not advertised as physically available.
_PARQUET_STATCAST_COLUMNS = (
    "game_date", "game_pk", "release_speed", "release_spin_rate", "pitch_type",
    "player_name", "pitcher", "batter", "events", "description", "plate_x", "plate_z",
    "stand", "balls", "strikes", "zone", "inning", "launch_speed", "launch_angle",
    "hit_distance_sc", "estimated_ba_using_speedangle", "estimated_woba_using_speedangle",
)

_POSTGRES_STATCAST_COLUMNS = (
    "game_date", "game_pk", "release_speed", "release_spin_rate", "pitch_type",
    "player_name", "pitcher_id", "batter_id", "events", "description", "plate_x",
    "plate_z", "sz_top", "sz_bot", "p_throws", "stand", "balls", "strikes",
    "zone", "inning", "launch_speed", "launch_angle", "hit_distance_sc",
    "estimated_ba_using_speedangle", "estimated_woba_using_speedangle",
)

_POSTGRES_BATTING_EVENTS_COLUMNS = (
    "game_date", "game_pk", "batter_id", "batter_name", "pitcher_id", "pitcher_name",
    "inning", "events", "launch_speed", "launch_angle", "hit_distance_sc", "stand",
    "pitch_type", "estimated_ba_using_speedangle", "estimated_woba_using_speedangle",
)

_POSTGRES_PLAYER_DICTIONARY_COLUMNS = ("player_id", "player_name")


def statcast_schema_registry() -> SchemaRegistry:
    """Deterministic schema registry built from verified source inspection."""
    return SchemaRegistry((
        SchemaTable(
            table_name="mlb_statcast_archive", source_kind="PARQUET",
            description="Historical Statcast 2015-2023 pitch-level archive; lacks sz_top/sz_bot",
            columns=_PARQUET_STATCAST_COLUMNS),
        SchemaTable(
            table_name="statcast_pitches", source_kind="POSTGRES",
            description="Hot Statcast pitch-level table (2024-2026); has sz_top/sz_bot/p_throws for batter-relative location",
            columns=_POSTGRES_STATCAST_COLUMNS),
        SchemaTable(
            table_name="batting_events", source_kind="POSTGRES",
            description="Event-level Statcast outcomes (478k rows); batter/pitcher names present",
            columns=_POSTGRES_BATTING_EVENTS_COLUMNS),
        SchemaTable(
            table_name="player_dictionary", source_kind="POSTGRES",
            description="MLBAM player_id -> player_name mapping (913 rows)",
            columns=_POSTGRES_PLAYER_DICTIONARY_COLUMNS),
    ))
