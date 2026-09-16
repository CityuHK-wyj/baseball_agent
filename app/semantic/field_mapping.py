"""Deterministic semantic-key -> physical-field mapping for local analytics sources.

This is the boundary the Planner never crosses: semantic keys (``pitch_velocity``,
``exit_velocity``, ``count``, ``pitch_type``, ``pitch_location``, ``batter``) map to
physical columns here, per source kind. Pitch-family -> provider code sets and named
pitch-location definitions also live here so they are explicit and testable rather than
silently assumed.
"""

from app.models.schema import FieldMapping, LocationDefinition

# Named semantic pitch-location definitions. The exact batter-relative upper edge is the
# semantic default; it requires plate_z plus sz_top / sz_bot and must not be silently
# replaced with a zone set when those fields are absent.
BATTER_RELATIVE_UPPER_EDGE = "BATTER_RELATIVE_UPPER_EDGE"
ZONE_UPPER_THIRD = "ZONE_UPPER_THIRD"
ZONE_ABOVE_UPPER_EDGE = "ZONE_ABOVE_UPPER_EDGE"

# Verified against plate_z (the physical vertical coordinate) in both PostgreSQL and the
# Parquet archive: zones 1-3 sit highest inside the zone, 7-9 lowest, 11-12 above the top
# edge and 13-14 below. This is the canonical Statcast zone orientation.
ZONE_LOWER_THIRD: tuple[int, ...] = (7, 8, 9)

DEFAULT_LOCATION_DEFINITIONS: tuple[LocationDefinition, ...] = (
    LocationDefinition(
        definition=BATTER_RELATIVE_UPPER_EDGE,
        required_physical_fields=("plate_z", "sz_top", "sz_bot"),
        zone_codes=(),
        description="exact upper edge relative to the batter's own strike zone",
    ),
    LocationDefinition(
        definition=ZONE_UPPER_THIRD,
        required_physical_fields=("zone",),
        zone_codes=(1, 2, 3),
        description="upper third of the strike zone (Statcast zones 1-3)",
    ),
    LocationDefinition(
        definition=ZONE_ABOVE_UPPER_EDGE,
        required_physical_fields=("zone",),
        zone_codes=(11, 12),
        description="just above the strike zone (Statcast shadow zones 11-12)",
    ),
)

# Semantic pitch families to explicit provider pitch-code sets. The fastball set is the
# only one the target query needs; the others document the same explicit mechanism.
PITCH_FAMILY_CODES: dict[str, tuple[str, ...]] = {
    "fastball": ("FF", "SI", "FC", "FA"),
    "breaking": ("SL", "CU", "KC", "SC"),
    "offspeed": ("CH", "FS", "FO"),
}

# Semantic data keys the local Statcast archives can provide, mapped to physical columns.
DEFAULT_FIELD_MAPPINGS: tuple[FieldMapping, ...] = (
    FieldMapping(semantic_key="exit_velocity", source_kind="PARQUET", physical_field="launch_speed"),
    FieldMapping(semantic_key="exit_velocity", source_kind="POSTGRES", physical_field="launch_speed"),
    FieldMapping(semantic_key="pitch_velocity", source_kind="PARQUET", physical_field="release_speed"),
    FieldMapping(semantic_key="pitch_velocity", source_kind="POSTGRES", physical_field="release_speed"),
    FieldMapping(semantic_key="pitch_type", source_kind="PARQUET", physical_field="pitch_type"),
    FieldMapping(semantic_key="pitch_type", source_kind="POSTGRES", physical_field="pitch_type"),
    FieldMapping(semantic_key="count", source_kind="PARQUET", physical_field="strikes"),
    FieldMapping(semantic_key="count", source_kind="POSTGRES", physical_field="strikes"),
    FieldMapping(semantic_key="balls", source_kind="PARQUET", physical_field="balls"),
    FieldMapping(semantic_key="balls", source_kind="POSTGRES", physical_field="balls"),
    FieldMapping(semantic_key="batter", source_kind="PARQUET", physical_field="batter"),
    FieldMapping(semantic_key="batter", source_kind="POSTGRES", physical_field="batter_id"),
    FieldMapping(semantic_key="game_date", source_kind="PARQUET", physical_field="game_date"),
    FieldMapping(semantic_key="game_date", source_kind="POSTGRES", physical_field="game_date"),
)


class FieldMappingRegistry:
    def __init__(self, mappings: tuple[FieldMapping, ...] = DEFAULT_FIELD_MAPPINGS,
                 locations: tuple[LocationDefinition, ...] = DEFAULT_LOCATION_DEFINITIONS,
                 pitch_families: dict[str, tuple[str, ...]] | None = None) -> None:
        self._mappings: dict[tuple[str, str], str] = {}
        for item in mappings:
            self._mappings[(item.semantic_key, item.source_kind)] = item.physical_field
        location_names = [item.definition for item in locations]
        if len(set(location_names)) != len(location_names):
            raise ValueError("Location definition names must be unique")
        self._locations = {item.definition: item for item in locations}
        self._pitch_families = dict(PITCH_FAMILY_CODES if pitch_families is None else pitch_families)

    def physical_field(self, semantic_key: str, source_kind: str) -> str | None:
        return self._mappings.get((semantic_key, source_kind))

    def location(self, definition: str) -> LocationDefinition | None:
        return self._locations.get(definition)

    def pitch_type_codes(self, family: str) -> tuple[str, ...] | None:
        return self._pitch_families.get(family)

    def source_kinds_for(self, semantic_keys: tuple[str, ...]) -> tuple[str, ...]:
        """Source kinds that can physically provide every requested semantic key."""
        if not semantic_keys:
            return ()
        kinds = {"PARQUET", "POSTGRES"}
        for key in semantic_keys:
            kinds &= {kind for kind in kinds if self.physical_field(key, kind) is not None}
        return tuple(sorted(kinds))
