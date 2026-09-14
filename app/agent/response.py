"""Build the user-facing projection from accepted products only.

The Response Agent reads a ``ResponsePackage``. It never sees failed attempts,
rejected evidence, old plans, routing experiments or Judge reasoning.
"""

from app.agent.registry import ArtifactRegistry
from app.assessment.service import AssessmentService
from app.models.contracts import ArtifactRequirement, ObjectiveState, RequirementState
from app.models.reports import AcceptedEvidence, ResponsePackage
from app.state.services import optional_gaps


def build_response_package(run_id: str, objective_state: ObjectiveState,
                           requirements: tuple[ArtifactRequirement, ...],
                           requirement_states: dict[str, RequirementState],
                           assessment_service: AssessmentService,
                           registry: ArtifactRegistry) -> ResponsePackage:
    accepted = tuple(item for item in assessment_service.all_assessments() if item.accepted)
    evidence = []
    for assessment in accepted:
        artifact = registry.get(assessment.artifact_ref)
        evidence.append(AcceptedEvidence(
            artifact_ref=artifact.artifact_id, requirement_ref=assessment.requirement_ref,
            level=assessment.final_level, summary=assessment.assessment_summary,
            payload_ref=artifact.payload_ref, source_kind=artifact.provenance.source_kind,
            source=artifact.provenance.source))
    core = tuple(item for item in requirements if item.base_criticality == "CORE")
    unresolved = tuple(item.requirement_id for item in core
                       if requirement_states[item.requirement_id].status != "SATISFIED")
    limitations = tuple(dict.fromkeys(
        limitation for assessment in accepted for limitation in assessment.limitations))
    return ResponsePackage(
        run_id=run_id, objective_ref=objective_state.objective_ref,
        objective_status=objective_state.status, accepted_evidence=tuple(evidence),
        objective_result="", limitations=limitations,
        optional_gaps=optional_gaps(requirements, requirement_states),
        unresolved_items=unresolved)
