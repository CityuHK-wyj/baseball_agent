"""Trusted Schema Catalog for safe analytical execution.

The catalog exposes the *allowed* tables, fields, types, meanings, grain, coverage and
operations for local analytics. It is built from the repository's verified source
metadata (``app.semantic.schema_registry``) and can be extended from live introspection.
Only catalog fields are valid at the analytical boundary; hallucinated fields are
rejected with a structured recovery code.

This is what lets the agent build an ad-hoc derived calculation (for example a
hard-hit-rate over ``launch_speed``) without pre-registering a named metric, while still
preventing arbitrary physical identifiers.
"""

from __future__ import annotations

from app.models.artifact_runtime import _Envelope
from app.semantic.schema_registry import statcast_schema_registry

# Operation vocabulary the safe IR may use. COUNT is row-based and needs no field.
ALLOWED_AGGREGATIONS: tuple[str, ...] = ("COUNT", "COUNT_NON_NULL", "COUNT_IF",
                                         "AVG", "SUM", "MIN", "MAX")
ALLOWED_COMPARISONS: tuple[str, ...] = ("EQ", "NE", "GT", "GTE", "LT", "LTE",
                                        "IN", "IS_NULL", "NOT_NULL", "BETWEEN")
IDENTIFIER_OPERATIONS: tuple[str, ...] = ("EQ", "NE", "IN", "IS_NULL", "NOT_NULL")
MEASURE_OPERATIONS: tuple[str, ...] = ("AVG", "SUM", "MIN", "MAX", "COUNT_NON_NULL",
                                       "COUNT_IF", "GT", "GTE", "LT", "LTE", "BETWEEN")


class CatalogField(_Envelope):
    name: str
    table: str
    source_kind: str
    data_type: str = "UNKNOWN"
    meaning: str = ""
    nullable: bool = True
    role: str = "DIMENSION"  # MEASURE | DIMENSION | IDENTIFIER | DATE | POPULATION
    entity: str = ""
    allowed_operations: tuple[str, ...] = ()
    source_coverage: tuple[str, ...] = ()


class CatalogTable(_Envelope):
    name: str
    source_kind: str
    grain: str = ""
    description: str = ""
    coverage: tuple[str, ...] = ()
    fields: tuple[CatalogField, ...] = ()


# Baseline semantic metadata for the verified Statcast sources. Physical identifiers are
# known to the catalog, never to the LLM.
_FIELD_SPECS: dict[str, dict] = {
    "game_date": {"type": "DATE", "role": "DATE", "meaning": "game date", "nullable": False},
    "game_pk": {"type": "INTEGER", "role": "IDENTIFIER", "meaning": "game identifier"},
    "game_type": {"type": "TEXT", "role": "POPULATION",
                  "meaning": "game type code (R/F/D/L/W/S/E/A)"},
    "release_speed": {"type": "DOUBLE", "role": "MEASURE",
                      "meaning": "pitch velocity (mph)", "entity": "pitch"},
    "release_spin_rate": {"type": "DOUBLE", "role": "MEASURE",
                          "meaning": "pitch spin rate (rpm)", "entity": "pitch"},
    "pitch_type": {"type": "TEXT", "role": "DIMENSION", "meaning": "pitch type code"},
    "player_name": {"type": "TEXT", "role": "DIMENSION", "meaning": "pitcher name"},
    "pitcher": {"type": "INTEGER", "role": "IDENTIFIER", "meaning": "pitcher MLBAM id",
                "entity": "pitcher"},
    "pitcher_id": {"type": "INTEGER", "role": "IDENTIFIER", "meaning": "pitcher MLBAM id",
                   "entity": "pitcher"},
    "batter": {"type": "INTEGER", "role": "IDENTIFIER", "meaning": "batter MLBAM id",
               "entity": "batter"},
    "batter_id": {"type": "INTEGER", "role": "IDENTIFIER", "meaning": "batter MLBAM id",
                  "entity": "batter"},
    "events": {"type": "TEXT", "role": "DIMENSION",
               "meaning": "terminal plate-appearance event"},
    "description": {"type": "TEXT", "role": "POPULATION",
                    "meaning": "pitch outcome description"},
    "plate_x": {"type": "DOUBLE", "role": "MEASURE", "meaning": "horizontal pitch location"},
    "plate_z": {"type": "DOUBLE", "role": "MEASURE", "meaning": "vertical pitch location"},
    "sz_top": {"type": "DOUBLE", "role": "MEASURE", "meaning": "batter strike-zone top"},
    "sz_bot": {"type": "DOUBLE", "role": "MEASURE", "meaning": "batter strike-zone bottom"},
    "p_throws": {"type": "TEXT", "role": "DIMENSION", "meaning": "pitcher handedness"},
    "stand": {"type": "TEXT", "role": "DIMENSION", "meaning": "batter handedness"},
    "balls": {"type": "INTEGER", "role": "DIMENSION", "meaning": "balls in count"},
    "strikes": {"type": "INTEGER", "role": "DIMENSION", "meaning": "strikes in count"},
    "zone": {"type": "INTEGER", "role": "DIMENSION", "meaning": "Statcast zone code"},
    "inning": {"type": "INTEGER", "role": "DIMENSION", "meaning": "inning number"},
    "launch_speed": {"type": "DOUBLE", "role": "MEASURE",
                     "meaning": "exit velocity (mph)", "entity": "batted_ball"},
    "launch_angle": {"type": "DOUBLE", "role": "MEASURE",
                     "meaning": "launch angle (degrees)", "entity": "batted_ball"},
    "hit_distance_sc": {"type": "DOUBLE", "role": "MEASURE",
                        "meaning": "projected hit distance (ft)", "entity": "batted_ball"},
    "estimated_ba_using_speedangle": {"type": "DOUBLE", "role": "MEASURE",
                                      "meaning": "estimated batting average"},
    "estimated_woba_using_speedangle": {"type": "DOUBLE", "role": "MEASURE",
                                        "meaning": "estimated wOBA"},
    "player_id": {"type": "INTEGER", "role": "IDENTIFIER", "meaning": "MLBAM player id",
                  "entity": "player"},
    "batter_name": {"type": "TEXT", "role": "DIMENSION", "meaning": "batter name"},
    "pitcher_name": {"type": "TEXT", "role": "DIMENSION", "meaning": "pitcher name"},
}

# Coverage windows declared by the sources (kept in one place; never inferred from rows).
SOURCE_COVERAGE: dict[str, tuple[str, tuple[str, str]]] = {
    "PARQUET": ("mlb_statcast_archive", ("2015-04-05", "2023-11-01")),
    "POSTGRES": ("statcast_pitches", ("2024-03-15", "2026-09-14")),
}


def _operations_for(role: str) -> tuple[str, ...]:
    if role in ("IDENTIFIER",):
        return IDENTIFIER_OPERATIONS
    if role in ("MEASURE",):
        return MEASURE_OPERATIONS
    return ("EQ", "NE", "IN", "IS_NULL", "NOT_NULL")


def _table_coverage(source_kind: str) -> tuple[str, ...]:
    entry = SOURCE_COVERAGE.get(source_kind)
    if entry is None:
        return ()
    _name, (start, end) = entry
    return (f"{start}..{end}",)


def catalog_from_registry(registry=None) -> "SchemaCatalog":
    registry = registry or statcast_schema_registry()
    tables: list[CatalogTable] = []
    for table in registry.tables():
        fields: list[CatalogField] = []
        for column in table.columns:
            spec = _FIELD_SPECS.get(column, {})
            role = spec.get("role", "DIMENSION")
            coverage = _table_coverage(table.source_kind)
            data_type = spec.get("type") or ("INTEGER" if column.endswith("_id") else "TEXT")
            fields.append(CatalogField(
                name=column, table=table.table_name, source_kind=table.source_kind,
                data_type=data_type, meaning=spec.get("meaning", table.description),
                nullable=bool(spec.get("nullable", True)), role=role,
                entity=spec.get("entity", ""),
                allowed_operations=_operations_for(role),
                source_coverage=coverage))
        grain = "pitch" if table.table_name in ("statcast_pitches", "mlb_statcast_archive") \
            else ("player" if table.table_name == "player_dictionary" else "event")
        tables.append(CatalogTable(
            name=table.table_name, source_kind=table.source_kind, grain=grain,
            description=table.description, coverage=_table_coverage(table.source_kind),
            fields=tuple(fields)))
    return SchemaCatalog(tuple(tables))


class SchemaCatalog:
    def __init__(self, tables: tuple[CatalogTable, ...]) -> None:
        self._tables = tables
        self._by_table = {(item.source_kind, item.name): item for item in tables}
        self._fields: dict[tuple[str, str, str], CatalogField] = {}
        for table in tables:
            for field in table.fields:
                self._fields[(table.source_kind, table.name, field.name)] = field

    def tables(self) -> tuple[CatalogTable, ...]:
        return self._tables

    def table(self, source_kind: str, name: str) -> CatalogTable | None:
        return self._by_table.get((source_kind, name))

    def field(self, source_kind: str, table: str, name: str) -> CatalogField | None:
        return self._fields.get((source_kind, table, name))

    def find_field(self, source_kind: str, name: str) -> CatalogField | None:
        for (kind, _table, field_name), field in self._fields.items():
            if kind == source_kind and field_name == name:
                return field
        return None

    def fields_for(self, source_kind: str, table: str | None = None) -> tuple[CatalogField, ...]:
        values = [field for (kind, table_name, _), field in self._fields.items()
                  if kind == source_kind and (table is None or table_name == table)]
        return tuple(values)

    def has_field(self, source_kind: str, name: str) -> bool:
        return self.find_field(source_kind, name) is not None

    def field_names(self, source_kind: str, table: str | None = None) -> tuple[str, ...]:
        return tuple(sorted({field.name for field in self.fields_for(source_kind, table)}))
