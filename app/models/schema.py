"""Schema description contract for the registry and shared context."""

from typing import Literal

from app.models.artifacts import ArtifactContract
from app.models.contracts import Name


class SchemaTable(ArtifactContract):
    table_name: Name
    source_kind: Literal["POSTGRES", "PARQUET"]
    description: str = ""
    columns: tuple[Name, ...] = ()
