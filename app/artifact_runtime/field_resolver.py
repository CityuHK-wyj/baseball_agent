"""Resolve planner-friendly field names to trusted SchemaCatalog fields.

The planner may propose a real catalog field, or a canonical semantic name (for example
``exit_velocity``). Only catalog-approved physical fields survive; anything else is an
``UNKNOWN_FIELD`` recovery signal. This is a general baseball vocabulary, not a
query-specific mapping.
"""

from __future__ import annotations

from app.artifact_runtime.schema_catalog import CatalogField, SchemaCatalog

# Canonical semantic name -> preferred physical fields, per source kind. The first
# catalog match wins; this keeps a stable semantic layer without blocking ad-hoc fields.
SEMANTIC_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "exit_velocity": ("launch_speed",),
    "ev": ("launch_speed",),
    "hard_hit": ("launch_speed",),
    "launch_speed": ("launch_speed",),
    "pitch_velocity": ("release_speed",),
    "velocity": ("release_speed",),
    "release_speed": ("release_speed",),
    "spin_rate": ("release_spin_rate",),
    "launch_angle": ("launch_angle",),
    "hit_distance": ("hit_distance_sc",),
    "distance": ("hit_distance_sc",),
    "expected_woba": ("estimated_woba_using_speedangle",),
    "expected_ba": ("estimated_ba_using_speedangle",),
    "batter": ("batter_id", "batter"),
    "pitcher": ("pitcher_id", "pitcher"),
    "player": ("player_id",),
    "game_date": ("game_date",),
    "date": ("game_date",),
    "game_type": ("game_type",),
    "pitch_type": ("pitch_type",),
    "zone": ("zone",),
    "balls": ("balls",),
    "strikes": ("strikes",),
    "stand": ("stand",),
    "p_throws": ("p_throws",),
    "description": ("description",),
    "events": ("events",),
}


def resolve_field(catalog: SchemaCatalog, source_kind: str, table: str,
                  name: str) -> CatalogField | None:
    direct = catalog.field(source_kind, table, name)
    if direct is not None:
        return direct
    for candidate in SEMANTIC_FIELD_ALIASES.get(name.strip().casefold(), ()):
        found = catalog.field(source_kind, table, candidate)
        if found is not None:
            return found
    return None
