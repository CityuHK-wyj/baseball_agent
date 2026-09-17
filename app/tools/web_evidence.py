"""Turn a fetched web document into a normal, assessable evidence Artifact.

The raw page is registered only as a supporting lineage product. The returned Artifact
contains structured claims and is the sole web product that can reach validation, the
Judge, or a ResponsePackage.
"""

import json
from collections.abc import Callable, Iterable

from app.agent.executor import ToolResult
from app.models.artifacts import Artifact, Provenance
from app.models.contracts import ArtifactDescriptor, ArtifactRequirement
from app.models.evidence import RawWebResult
from app.semantic.evidence import EvidenceExtractor, evidence_to_artifact


class WebEvidenceTool:
    name = "web-evidence"

    def __init__(self, fetcher: Callable[[object], RawWebResult], extractor: EvidenceExtractor,
                 requirements: Iterable[ArtifactRequirement]) -> None:
        self._fetcher = fetcher
        self._extractor = extractor
        self._requirements = {item.requirement_id: item for item in requirements}

    def execute(self, task) -> ToolResult:
        requirement = self._requirements.get(task.requirement_refs[0])
        if requirement is None:
            return ToolResult.no_data()
        raw = self._fetcher(task)
        evidence = self._extractor.extract(raw)
        raw_artifact = self._raw_artifact(raw)
        evidence_artifact = evidence_to_artifact(
            evidence, raw_artifact.artifact_id, descriptor=requirement.descriptor)
        return ToolResult.ok(
            len(evidence.claims), artifact=evidence_artifact,
            supporting_artifacts=(raw_artifact,),
            payload=evidence.model_dump_json().encode(),
        )

    @staticmethod
    def _raw_artifact(raw: RawWebResult) -> Artifact:
        return Artifact(
            artifact_id=f"raw-{raw.result_id}",
            descriptor=ArtifactDescriptor(
                artifact_type="EVIDENCE", data_keys=("raw_web_result",),
                granularity="document", population_scope="web"),
            payload_ref=f"web://{raw.result_id}",
            provenance=Provenance(source=raw.source, source_kind="WEB", reference=raw.url,
                                  retrieved_at=raw.retrieved_at),
            row_count=1,
        )
