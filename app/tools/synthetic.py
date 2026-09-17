"""Offline synthetic data source.

Fabricates an Artifact that matches a Requirement's descriptor so the full pipeline can
run without a database. It is clearly marked SYNTHETIC and is not a claim of real data.
"""

import json
from collections.abc import Iterable

from app.agent.executor import ToolResult
from app.models.artifacts import Artifact, Provenance
from app.models.contracts import ArtifactRequirement


def fabricate_artifact(requirement: ArtifactRequirement, row_count: int = 1200) -> Artifact:
    descriptor = requirement.descriptor
    artifact_id = f"synthetic-{requirement.requirement_id}"
    return Artifact(
        artifact_id=artifact_id, descriptor=descriptor,
        payload_ref=f"synthetic://{artifact_id}",
        provenance=Provenance(source="synthetic", source_kind="SYNTHETIC",
                              reference=f"requirement:{requirement.requirement_id}"),
        row_count=row_count, observed_time_range=descriptor.time_range)


class SyntheticDataTool:
    """Returns a synthetic artifact for the task's requirement. Never touches a database."""

    name = "synthetic"

    def __init__(self, requirements: Iterable[ArtifactRequirement], row_count: int = 1200) -> None:
        self._by_id = {item.requirement_id: item for item in requirements}
        self._row_count = row_count

    def execute(self, task) -> ToolResult:
        requirement = self._by_id.get(task.requirement_refs[0])
        if requirement is None:
            return ToolResult(status="EMPTY")
        artifact = fabricate_artifact(requirement, self._row_count)
        payload = json.dumps({"requirement": requirement.requirement_id,
                              "rows": self._row_count}).encode()
        return ToolResult(status="OK", artifact=artifact, payload=payload)
