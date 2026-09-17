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
                           registry: ArtifactRegistry,
                           context_items: tuple = (),
                           understanding=None) -> ResponsePackage:
    # Only this objective's assessments: a shared service must not leak another
    # objective's accepted evidence into this response.
    requirement_ids = {item.requirement_id for item in requirements}
    accepted = tuple(
        item for item in assessment_service.all_assessments()
        if item.accepted
        and item.requirement_ref in requirement_ids
        and item.objective_ref in (None, objective_state.objective_ref))
    evidence = []
    for assessment in accepted:
        artifact = registry.get(assessment.artifact_ref)
        evidence.append(AcceptedEvidence(
            artifact_ref=artifact.artifact_id, requirement_ref=assessment.requirement_ref,
            level=assessment.final_level, summary=assessment.assessment_summary,
            payload_ref=artifact.payload_ref, source_kind=artifact.provenance.source_kind,
            source=artifact.provenance.source,
            text_excerpt=(artifact.text_content or "")[:4000]))
    core = tuple(item for item in requirements if item.base_criticality == "CORE")
    unresolved = tuple(item.requirement_id for item in core
                       if requirement_states[item.requirement_id].status != "SATISFIED")
    unresolved_explanations = tuple(
        f"Could not obtain {', '.join(item.descriptor.data_keys) or 'the requested data'} "
        f"for: {item.description}"
        for item in core if requirement_states[item.requirement_id].status != "SATISFIED")
    limitations = tuple(dict.fromkeys(
        limitation for assessment in accepted for limitation in assessment.limitations))
    assumptions: tuple[str, ...] = ()
    if understanding is not None:
        if understanding.analysis_strategy:
            assumptions = (*assumptions, understanding.analysis_strategy)
        assumptions = (*assumptions, *understanding.assumptions_allowed)
        if understanding.unresolved_concepts and not unresolved_explanations:
            unresolved_explanations = tuple(
                f"Unresolved concept not answered locally: {concept}"
                for concept in understanding.unresolved_concepts)
    return ResponsePackage(
        run_id=run_id, objective_ref=objective_state.objective_ref,
        objective_status=objective_state.status, accepted_evidence=tuple(evidence),
        critical_shared_knowledge=tuple(f"{entry.kind}:{entry.item_id}" for entry in context_items),
        knowledge_context=context_items,
        objective_result="", limitations=limitations,
        optional_gaps=optional_gaps(requirements, requirement_states),
        unresolved_items=unresolved,
        unresolved_explanations=tuple(dict.fromkeys(unresolved_explanations)),
        assumptions=tuple(dict.fromkeys(assumptions)))
