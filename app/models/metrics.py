"""Metric definition and source mapping contracts.

MetricDefinition describes a metric semantically; SourceMapping is how the system can
obtain it. DIRECT means the source provides it; CALCULATED means the Feature Engine can
compute it deterministically. This is not a place for retrieval or reasoning.
"""

from typing import Literal

from app.models.artifacts import ArtifactContract
from app.models.contracts import Name


class MetricDefinition(ArtifactContract):
    metric_key: Name
    display_name: Name
    description: Name
    required_data_keys: tuple[Name, ...] = ()
    unit: str = ""


class SourceMapping(ArtifactContract):
    metric_key: Name
    source_kind: Literal["POSTGRES", "PARQUET", "WEB", "FEATURE"]
    location: Name
    computation: Literal["DIRECT", "CALCULATED"] = "DIRECT"
