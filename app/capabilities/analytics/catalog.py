"""SchemaCatalog: the authoritative description of the real analytical sources.

Built from live inspection of the PostgreSQL Statcast store and the historical Parquet
archive (verified 2026-09-21). Logical field ids are stable; physical names differ per
source (PostgreSQL exposes `pitcher_id`/`batter_id`, the Parquet archive exposes
`pitcher`/`batter`).

This module contains business facts about the real sources. It contains no query logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable

SOURCE_POSTGRES = "POSTGRES"
SOURCE_PARQUET = "PARQUET"

# Live-verified coverage windows (inclusive).
POSTGRES_COVERAGE = (date(2024, 3, 1), date(2026, 12, 31))
PARQUET_COVERAGE = (date(2015, 1, 1), date(2023, 12, 31))

# Competition populations are business sets, not calendar windows.
POPULATIONS: dict[str, list[str]] = {
    "REGULAR_SEASON": ["R"],
    "POSTSEASON": ["F", "D", "L", "W"],
    "WORLD_SERIES": ["W"],
    "WILDCARD": ["F"],
    "DIVISION_SERIES": ["D"],
    "LEAGUE_CHAMPIONSHIP": ["L"],
    "SPRING_TRAINING": ["S"],
}

EVENT_ENUM = [
    "field_out", "strikeout", "single", "walk", "double", "home_run", "force_out",
    "grounded_into_double_play", "hit_by_pitch", "sac_fly", "field_error", "triple",
    "sac_bunt", "fielders_choice", "double_play", "fielders_choice_out", "truncated_pa",
    "strikeout_double_play", "catcher_interf", "sac_fly_double_play", "triple_play",
    "intent_walk", "sac_bunt_double_play", "game_advisory",
]

PITCH_TYPE_ENUM = [
    "FF", "SI", "SL", "CH", "FC", "ST", "CU", "FS", "KC", "SV", "EP", "FA", "KN", "FO", "CS",
]

HIT_EVENTS = ["single", "double", "triple", "home_run"]
NON_AT_BAT_EVENTS = [
    "walk", "intent_walk", "hit_by_pitch", "catcher_interf", "sac_fly", "sac_bunt",
    "sac_fly_double_play", "sac_bunt_double_play", "truncated_pa", "game_advisory",
]


@dataclass(frozen=True)
class FieldSpec:
    name: str
    meaning: str
    type: str                       # int | float | string | date
    role: str                       # id | time | dimension | measure | population | lookup
    ops: frozenset[str]             # GROUP_BY | FILTER | AGG | DERIVE
    enum_values: tuple[str, ...] = ()
    nullable: bool = True
    minimum: int | float | None = None
    maximum: int | float | None = None


@dataclass(frozen=True)
class TableSpec:
    name: str
    grain: str
    description: str
    fields: dict[str, FieldSpec]
    physical: dict[str, str]                    # source -> physical table/parquet read expr
    key_map: dict[str, dict[str, str]]          # source -> logical -> physical column
    coverage: dict[str, tuple[date, date] | None]
    default_population: str = "REGULAR_SEASON"

    def fields_for_source(self, source: str) -> dict[str, FieldSpec]:
        key_map = self.key_map.get(source, {})
        return {name: spec for name, spec in self.fields.items() if name in key_map}


def _f(name, meaning, type_, role, ops, enum=(), nullable=True) -> FieldSpec:
    bounds = {"season": (1, 9999), "balls": (0, 3), "strikes": (0, 2), "inning": (1, 2147483647), "zone": (1, 14)}
    low, high = bounds.get(name, (1, 9223372036854775807) if role == "id" else (None, None))
    return FieldSpec(name, meaning, type_, role, frozenset(ops), tuple(enum), nullable, low, high)


# Logical field definitions shared by the two physical pitch sources.
_PITCH_FIELDS = {
    "game_date": _f("game_date", "Date the game was played", "date", "time", {"FILTER", "GROUP_BY", "DERIVE"}),
    "season": _f("season", "Calendar season derived from game_date", "int", "time", {"FILTER", "GROUP_BY", "DERIVE"}, nullable=False),
    "game_pk": _f("game_pk", "Unique game identifier", "int", "id", {"FILTER", "GROUP_BY"}),
    "game_type": _f("game_type", "Competition code (R/S/F/D/L/W)", "string", "population", {"FILTER", "GROUP_BY"}, enum=["R", "S", "F", "D", "L", "W"]),
    "pitcher_id": _f("pitcher_id", "MLBAM id of the pitcher", "int", "id", {"FILTER", "GROUP_BY"}),
    "batter_id": _f("batter_id", "MLBAM id of the batter", "int", "id", {"FILTER", "GROUP_BY"}),
    "player_name": _f("player_name", "Pitcher name recorded on the pitch", "string", "lookup", {"FILTER", "GROUP_BY"}),
    "pitch_type": _f("pitch_type", "Statcast pitch classification", "string", "dimension", {"FILTER", "GROUP_BY", "DERIVE"}, enum=PITCH_TYPE_ENUM),
    "release_speed": _f("release_speed", "Pitch velocity in mph", "float", "measure", {"FILTER", "GROUP_BY", "AGG"}),
    "release_spin_rate": _f("release_spin_rate", "Pitch spin rate in rpm", "float", "measure", {"FILTER", "AGG"}),
    "plate_x": _f("plate_x", "Horizontal pitch location at the plate (feet, catcher POV)", "float", "measure", {"FILTER", "AGG"}),
    "plate_z": _f("plate_z", "Vertical pitch location at the plate (feet)", "float", "measure", {"FILTER", "AGG"}),
    "sz_top": _f("sz_top", "Batter-specific top of strike zone (feet)", "float", "measure", {"FILTER", "AGG", "DERIVE"}),
    "sz_bot": _f("sz_bot", "Batter-specific bottom of strike zone (feet)", "float", "measure", {"FILTER", "AGG", "DERIVE"}),
    "stand": _f("stand", "Batter handedness (L/R)", "string", "dimension", {"FILTER", "GROUP_BY"}, enum=["L", "R"]),
    "p_throws": _f("p_throws", "Pitcher handedness (L/R)", "string", "dimension", {"FILTER", "GROUP_BY"}, enum=["L", "R"]),
    "balls": _f("balls", "Ball count before the pitch", "int", "dimension", {"FILTER", "GROUP_BY"}),
    "strikes": _f("strikes", "Strike count before the pitch", "int", "dimension", {"FILTER", "GROUP_BY"}),
    "zone": _f("zone", "Statcast zone number (1-14)", "int", "dimension", {"FILTER", "GROUP_BY"}),
    "inning": _f("inning", "Inning number", "int", "dimension", {"FILTER", "GROUP_BY"}),
    "description": _f("description", "Pitch result description", "string", "dimension", {"FILTER", "GROUP_BY"}, enum=["ball", "called_strike", "swinging_strike", "foul", "hit_into_play", "blocked_ball", "foul_tip", "swinging_strike_blocked", "hit_by_pitch", "foul_bunt", "missed_bunt", "pitchout"]),
    "events": _f("events", "Plate-appearance terminal event (null for non-terminal pitches)", "string", "dimension", {"FILTER", "GROUP_BY", "DERIVE"}, enum=EVENT_ENUM),
    "launch_speed": _f("launch_speed", "Exit velocity in mph (null when no batted ball)", "float", "measure", {"FILTER", "AGG", "DERIVE"}),
    "launch_angle": _f("launch_angle", "Launch angle in degrees (null when no batted ball)", "float", "measure", {"FILTER", "AGG", "DERIVE"}),
    "hit_distance_sc": _f("hit_distance_sc", "Projected hit distance in feet", "float", "measure", {"FILTER", "AGG"}),
    "estimated_ba_using_speedangle": _f("estimated_ba_using_speedangle", "Expected batting average from speed and angle", "float", "measure", {"FILTER", "AGG", "DERIVE"}),
    "estimated_woba_using_speedangle": _f("estimated_woba_using_speedangle", "Expected wOBA from speed and angle", "float", "measure", {"FILTER", "AGG", "DERIVE"}),
}

_PG_KEY_MAP = {name: name for name in _PITCH_FIELDS}
_PARQUET_KEY_MAP = {name: name for name in _PITCH_FIELDS}
_PARQUET_KEY_MAP["pitcher_id"] = "pitcher"
_PARQUET_KEY_MAP["batter_id"] = "batter"
del _PARQUET_KEY_MAP["season"]  # derived from game_date in SQL

PITCH_TABLE = TableSpec(
    name="pitch",
    grain="PITCH",
    description="Statcast pitch-level data for MLB games.",
    fields=_PITCH_FIELDS,
    physical={
        SOURCE_POSTGRES: "public.statcast_pitches",
        SOURCE_PARQUET: "read_parquet('{archive}/mlb_statcast_*.parquet')",
    },
    key_map={SOURCE_POSTGRES: _PG_KEY_MAP, SOURCE_PARQUET: _PARQUET_KEY_MAP},
    coverage={SOURCE_POSTGRES: POSTGRES_COVERAGE, SOURCE_PARQUET: PARQUET_COVERAGE},
)

PLAYER_DICTIONARY = TableSpec(
    name="player_dictionary",
    grain="PLAYER",
    description="MLBAM player id to player name mapping.",
    fields={
        "player_id": _f("player_id", "MLBAM player id", "int", "id", {"FILTER", "GROUP_BY"}),
        "player_name": _f("player_name", "Display name 'Last, First'", "string", "lookup", {"FILTER", "GROUP_BY"}),
    },
    physical={SOURCE_POSTGRES: "public.player_dictionary"},
    key_map={SOURCE_POSTGRES: {"player_id": "player_id", "player_name": "player_name"}},
    coverage={SOURCE_POSTGRES: (date(2015, 1, 1), date(2026, 12, 31))},
)

TABLES: dict[str, TableSpec] = {PITCH_TABLE.name: PITCH_TABLE, PLAYER_DICTIONARY.name: PLAYER_DICTIONARY}

# Qualification bases: logical sample-size measures expressed with real columns.
QUALIFICATION_BASES: dict[str, dict[str, Any]] = {
    "PA": {"field": "events", "mode": "COUNT_NON_NULL", "label": "plate appearances"},
    "BBE": {"field": "launch_speed", "mode": "COUNT_NON_NULL", "label": "batted ball events"},
    "PITCHES": {"field": "*", "mode": "COUNT", "label": "pitches"},
    "GAMES": {"field": "game_pk", "mode": "COUNT_DISTINCT", "label": "games"},
    "RESULTS": {"field": "*", "mode": "COUNT", "label": "result rows"},
}


class CatalogError(ValueError):
    pass


class SchemaCatalog:
    """Read-only accessor over the verified source schema."""

    def __init__(self, tables: dict[str, TableSpec] | None = None):
        self.tables = tables or TABLES

    # -- source selection ---------------------------------------------------
    def source_for_window(
        self, table: str, *, season: int | None = None, start: date | None = None, end: date | None = None
    ) -> tuple[str | None, str]:
        spec = self.table(table)
        available: list[tuple[str, tuple[date, date]]] = [
            (source, window) for source, window in spec.coverage.items() if window is not None
        ]
        if season is not None:
            for source, (cstart, cend) in available:
                if cstart.year <= season <= cend.year:
                    return source, ""
            return None, f"season {season} is outside all known coverage"
        if start is not None or end is not None:
            window_start = start or date.min
            window_end = end or date.max
            overlapping = [
                (source, coverage)
                for source, coverage in available
                if not (window_end < coverage[0] or window_start > coverage[1])
            ]
            if not overlapping:
                return None, f"requested window {window_start}..{window_end} is outside all known coverage"
            for source, (cstart, cend) in overlapping:
                if window_start >= cstart and window_end <= cend:
                    return source, ""
            # Partial overlap: proceed on the best-covered source and disclose the gap.
            overlapping.sort(
                key=lambda pair: (min(window_end, pair[1][1]) - max(window_start, pair[1][0])),
                reverse=True,
            )
            source, (cstart, cend) = overlapping[0]
            return source, f"partial coverage: requested {window_start}..{window_end}, source covers {cstart}..{cend}"
        if SOURCE_POSTGRES in spec.coverage:
            return SOURCE_POSTGRES, ""
        if SOURCE_PARQUET in spec.coverage:
            return SOURCE_PARQUET, ""
        return None, "no coverage window could be resolved"

    def table(self, name: str) -> TableSpec:
        if name not in self.tables:
            raise CatalogError(f"unknown relation '{name}'")
        return self.tables[name]

    def field(self, table: str, name: str) -> FieldSpec:
        spec = self.table(table)
        if name not in spec.fields:
            raise CatalogError(f"unknown field '{name}' on '{table}'")
        return spec.fields[name]

    def is_valid(self, table: str, name: str) -> bool:
        return name in self.tables and name in self.tables[table].fields

    def physical_column(self, source: str, table: str, logical: str) -> str | None:
        spec = self.table(table)
        return spec.key_map.get(source, {}).get(logical)

    def physical_table(self, source: str, table: str, *, parquet_archive: str = "") -> str:
        spec = self.table(table)
        expr = spec.physical.get(source)
        if expr is None:
            raise CatalogError(f"relation '{table}' is not available on {source}")
        return expr.format(archive=parquet_archive)

    # -- prompt views -------------------------------------------------------
    def planner_affordances(self) -> dict[str, Any]:
        """Semantic data affordances for the Planner: no physical identifiers."""
        return {
            "coverage": {
                "statcast_pitch_level": "2015-2026 (2015-2023 archive, 2024-2026 live)",
                "competition_populations": sorted(POPULATIONS),
                "notes": [
                    "Pitch-level data supports ad-hoc filters, grouping and aggregation.",
                    "Pitch types, handedness splits, counts, zones, exit velocity and launch angle are available.",
                    "Player identity comes from entity_resolution, not from the analytics capability.",
                ],
            }
        }

    def builder_slice(self, table: str = "pitch", *, max_fields: int = 60) -> dict[str, Any]:
        """Physical catalog slice for the IR builder (the analytical lane only)."""
        spec = self.table(table)
        fields = []
        for name, f in spec.fields.items():
            entry: dict[str, Any] = {"field": name, "type": f.type, "meaning": f.meaning, "role": f.role}
            if f.enum_values:
                entry["enum"] = list(f.enum_values)
            fields.append(entry)
        return {
            "relation": spec.name,
            "grain": spec.grain,
            "description": spec.description,
            "coverage": {
                source: (f"{w[0]}..{w[1]}" if w else "unknown") for source, w in spec.coverage.items()
            },
            "fields": fields[:max_fields],
            "qualification_bases": {k: v["label"] for k, v in QUALIFICATION_BASES.items()},
            "populations": {k: v for k, v in POPULATIONS.items()},
            "hit_events": HIT_EVENTS,
            "non_at_bat_events": NON_AT_BAT_EVENTS,
            "canonical_derived_metrics": {
                "tb": "singles + 2*doubles + 3*triples + 4*home_runs",
                "avg": "hits / at_bats",
                "obp": "(hits + walks + hbp) / (at_bats + walks + hbp + sac_flies)",
                "slg": "tb / at_bats",
                "ops": "obp + slg",
                "k_pct": "100 * strikeouts / pa",
                "bb_pct": "100 * walks / pa",
                "hard_hit_pct": "100 * COUNT_IF(launch_speed >= 95) / COUNT_NON_NULL(launch_speed)",
            },
        }

    def resolve_population(self, population: str | None) -> list[str]:
        if not population:
            return []
        return list(POPULATIONS.get(population.upper(), []))

    def all_sources(self) -> Iterable[str]:
        return (SOURCE_POSTGRES, SOURCE_PARQUET)


CATALOG = SchemaCatalog()
