"""Build contextual ArtifactAssessments from deterministic facts plus a Judge."""

from typing import Callable

from app.agent.registry import ArtifactRegistry
from app.assessment.judge import Judge
from app.assessment.validator import validate_artifact
from app.models.artifacts import ArtifactAssessment, AssessmentSummary
from app.models.contracts import ArtifactRequirement


class AssessmentService:
    """Owns assessment identity and storage. Not an agent: no scheduling, no planning."""

    def __init__(self, registry: ArtifactRegistry, judge: Judge,
                 id_factory: Callable[[str], str]) -> None:
        self._registry = registry
        self._judge = judge
        self._id_factory = id_factory
        self._assessments: dict[str, ArtifactAssessment] = {}

    def assess(self, artifact_id: str, requirement: ArtifactRequirement,
               objective_ref: str | None = None) -> ArtifactAssessment:
        artifact = self._registry.get(artifact_id)
        deterministic = validate_artifact(artifact, requirement)
        judge_result = self._judge.assess(artifact, requirement, deterministic)
        # Hard veto is enforced here as well as in the contract.
        final_level = "REJECT" if not deterministic.passed else judge_result.level
        limitations = tuple(signal.detail for signal in deterministic.soft_signals)
        assessment = ArtifactAssessment(
            assessment_id=self._id_factory("assessment"),
            artifact_ref=artifact_id,
            requirement_ref=requirement.requirement_id,
            objective_ref=objective_ref,
            deterministic_result=deterministic,
            judge_result=judge_result,
            final_level=final_level,
            assessment_summary=self._summarize(artifact.row_count, final_level, judge_result.rationale),
            usable_for=(requirement.descriptor.artifact_type,) if final_level in ("STRONG", "ACCEPTABLE") else (),
            limitations=limitations,
        )
        existing = self._assessments.get(assessment.assessment_id)
        if existing is not None and existing != assessment:
            raise ValueError("Assessment identity collision")
        self._assessments[assessment.assessment_id] = assessment
        return assessment

    @staticmethod
    def _summarize(row_count: int | None, level: str, rationale: str) -> str:
        size = f"{row_count} rows" if row_count is not None else "size unknown"
        return f"{level} ({size}): {rationale}"

    def get(self, assessment_id: str) -> ArtifactAssessment:
        return self._assessments[assessment_id]

    def assessments_for(self, requirement_ref: str) -> tuple[ArtifactAssessment, ...]:
        return tuple(item for item in self._assessments.values() if item.requirement_ref == requirement_ref)

    def all_assessments(self) -> tuple[ArtifactAssessment, ...]:
        return tuple(self._assessments.values())

    def summaries(self) -> tuple[AssessmentSummary, ...]:
        return tuple(
            AssessmentSummary(assessment_ref=item.assessment_id, artifact_ref=item.artifact_ref,
                              requirement_ref=item.requirement_ref, final_level=item.final_level,
                              summary=item.assessment_summary)
            for item in self._assessments.values()
        )
