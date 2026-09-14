"""Shared synthetic domain fixtures for tests. No network, no credentials, no analytics IO."""

from app.models.artifacts import Artifact, Provenance
from app.models.contracts import ArtifactDescriptor, ArtifactRequirement, AnalysisObjective, Entity, TimeRange


def objective(objective_id: str = "o1", **changes) -> AnalysisObjective:
    fields = dict(objective_id=objective_id, raw_query="Did player 1 ever hit 105 mph?",
                  description="Establish the existence of a 105 mph batted ball")
    return AnalysisObjective(**(fields | changes))


def requirement(requirement_id: str = "r1", **changes) -> ArtifactRequirement:
    fields = dict(
        requirement_id=requirement_id, objective_ref="o1", description="Exit velocity evidence",
        descriptor=ArtifactDescriptor(
            artifact_type="TABLE", data_keys=("exit_velocity",),
            granularity="batted_ball", population_scope="player:1",
            entities=(Entity(namespace="MLBAM", entity_type="PLAYER", identifier="1"),),
        ),
    )
    return ArtifactRequirement(**(fields | changes))


def artifact(artifact_id: str = "a1", **changes) -> Artifact:
    fields = dict(
        artifact_id=artifact_id,
        descriptor=ArtifactDescriptor(
            artifact_type="TABLE", data_keys=("exit_velocity",),
            granularity="batted_ball", population_scope="player:1",
            entities=(Entity(namespace="MLBAM", entity_type="PLAYER", identifier="1"),),
        ),
        payload_ref=f"store://{artifact_id}",
        provenance=Provenance(source="synthetic-tool", source_kind="SYNTHETIC"),
        row_count=1200,
    )
    return Artifact(**(fields | changes))


def time_range(start: str = "2024-04-01", end: str = "2024-09-30") -> TimeRange:
    return TimeRange(start=start, end=end)
