"""Deterministic Feature Engine.

Turns raw tabular records into a metric Artifact. Output is a normal Artifact with
``artifact_type="FEATURE"`` and ``lineage`` pointing at the input, so there is no private
result format (D037). This module performs only deterministic computation; it never
interprets or judges the result.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.models.artifacts import Artifact, ArtifactContract, Provenance
from app.models.contracts import ArtifactDescriptor

Rows = Sequence[Mapping[str, Any]]


@dataclass(frozen=True)
class FeatureComputation:
    key: str
    unit: str
    fn: Callable[[Rows], float]


def _values(rows: Rows, key: str) -> list[float]:
    collected: list[float] = []
    for row in rows:
        value = row.get(key)
        if value is None:
            continue
        collected.append(float(value))
    return collected


def _mean(rows: Rows, key: str) -> float:
    values = _values(rows, key)
    if not values:
        raise ValueError(f"No non-null '{key}' values to average")
    return sum(values) / len(values)


def _maximum(rows: Rows, key: str) -> float:
    values = _values(rows, key)
    if not values:
        raise ValueError(f"No non-null '{key}' values to maximise")
    return max(values)


def _count(rows: Rows) -> float:
    return float(len(rows))


def default_computations() -> dict[str, FeatureComputation]:
    return {
        "mean_launch_speed": FeatureComputation(
            "mean_launch_speed", "mph", lambda rows: _mean(rows, "launch_speed")),
        "max_launch_speed": FeatureComputation(
            "max_launch_speed", "mph", lambda rows: _maximum(rows, "launch_speed")),
        "batted_ball_count": FeatureComputation("batted_ball_count", "count", _count),
    }


class FeatureResult(ArtifactContract):
    artifact: Artifact
    values: dict[str, float]


class FeatureEngine:
    def __init__(self, computations: Mapping[str, FeatureComputation] | None = None,
                 id_factory: Callable[[str], str] | None = None) -> None:
        self._computations = dict(computations or default_computations())
        self._id_factory = id_factory or (lambda prefix: f"{prefix}-{abs(hash(prefix))}")

    def computations(self) -> tuple[str, ...]:
        return tuple(self._computations)

    def compute(self, input_artifact: Artifact, rows: Rows,
                metric_keys: Sequence[str], payload_ref: str | None = None) -> FeatureResult:
        if input_artifact.descriptor.artifact_type != "TABLE":
            raise ValueError("Feature Engine derives features from a TABLE artifact")
        unknown = [key for key in metric_keys if key not in self._computations]
        if unknown:
            raise KeyError(f"Unknown feature computation(s): {sorted(unknown)}")
        values = {key: self._computations[key].fn(rows) for key in metric_keys}
        source = input_artifact.descriptor
        artifact_id = self._id_factory("feature")
        artifact = Artifact(
            artifact_id=artifact_id,
            descriptor=ArtifactDescriptor(
                artifact_type="FEATURE", entities=source.entities, data_keys=tuple(values),
                optional_data_keys=(), time_range=source.time_range, constraints=source.constraints,
                granularity=source.granularity, population_scope=source.population_scope),
            payload_ref=payload_ref or f"feature://{artifact_id}",
            provenance=Provenance(source="feature_engine", source_kind="FEATURE",
                                  reference=f"derived_from:{input_artifact.artifact_id}"),
            lineage=(input_artifact.artifact_id,), row_count=len(rows),
            observed_time_range=input_artifact.observed_time_range)
        return FeatureResult(artifact=artifact, values=values)
