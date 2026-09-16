"""Schema description contract for the registry and shared context."""

from typing import Literal

from app.models.artifacts import ArtifactContract
from app.models.contracts import Name


class SchemaTable(ArtifactContract):
    table_name: Name
    source_kind: Literal["POSTGRES", "PARQUET"]
    description: str = ""
    columns: tuple[Name, ...] = ()


class FieldMapping(ArtifactContract):
    """Maps one semantic data key to one physical column for one source kind.

    This is the deterministic boundary between semantic planning and physical query
    generation. The Planner reasons in ``semantic_key``; only the adapter consults the
    ``physical_field``.
    """

    semantic_key: Name
    source_kind: Literal["POSTGRES", "PARQUET"]
    physical_field: Name


class LocationDefinition(ArtifactContract):
    """An explicit, named pitch-location definition and the physical fields it needs.

    A batter-relative upper edge requires ``plate_z`` plus ``sz_top`` / ``sz_bot``; a
    zone-based definition requires only ``zone``. Keeping these separate prevents a
    source from silently redefining the semantic request.
    """

    definition: Name
    required_physical_fields: tuple[Name, ...] = ()
    zone_codes: tuple[int, ...] = ()
    description: str = ""
