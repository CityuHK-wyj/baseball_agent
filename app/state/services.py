"""Requirement and Objective state derivation.

Pure functions over definitions, assessments and prior projections. These are state
update services, not sub-agents: no scheduling, no judgement, no LLM calls.
"""

from collections.abc import Iterable

from app.models.artifacts import ArtifactAssessment
from app.models.contracts import ArtifactRequirement, ObjectiveState, RequirementState


def derive_requirement_state(requirement_ref: str, assessments: Iterable[ArtifactAssessment],
                             previous: RequirementState | None = None) -> RequirementState:
    items = tuple(assessments)
    refs = tuple(item.assessment_id for item in items)
    accepted = tuple(item for item in items if item.accepted)
    artifact_refs = tuple(dict.fromkeys(item.artifact_ref for item in accepted))
    limitation_refs = tuple(item.assessment_id for item in items if item.limitations)
    if not items:
        status = "PENDING"
    elif accepted:
        status = "SATISFIED"
    elif any(item.final_level == "WEAK" for item in items):
        status = "PARTIAL"
    else:
        status = "UNSATISFIED"
    if (previous is not None and previous.status == status and previous.assessment_refs == refs
            and previous.artifact_refs == artifact_refs and previous.limitation_refs == limitation_refs):
        return previous
    version = 0 if previous is None else previous.version + 1
    return RequirementState(requirement_ref=requirement_ref, status=status,
                            assessment_refs=refs, artifact_refs=artifact_refs,
                            limitation_refs=limitation_refs,
                            recoverable=previous.recoverable if previous is not None else True,
                            version=version)


def derive_objective_state(objective_ref: str, requirements: Iterable[ArtifactRequirement],
                           requirement_states: dict[str, RequirementState],
                           planner_terminal: bool,
                           previous: ObjectiveState | None = None,
                           replan_count: int | None = None) -> ObjectiveState:
    items = tuple(requirements)
    refs = tuple(item.requirement_id for item in items)
    core = [item for item in items if item.base_criticality == "CORE"]
    core_status = [requirement_states[item.requirement_id].status for item in core]
    if core and all(status == "SATISFIED" for status in core_status):
        status = "COMPLETE"
    elif not planner_terminal:
        status = "PENDING" if all(state == "PENDING" for state in core_status) else "IN_PROGRESS"
    elif any(state in ("SATISFIED", "PARTIAL") for state in core_status):
        status = "LIMITED"
    else:
        status = "FAILED"
    gaps = optional_gaps(items, requirement_states)
    limitations = tuple(dict.fromkeys(
        ref for item in items for ref in requirement_states[item.requirement_id].limitation_refs))
    resolved_replans = replan_count if replan_count is not None else (
        previous.replan_count if previous is not None else 0)
    if (previous is not None and previous.status == status and previous.requirement_refs == refs
            and previous.optional_gaps == gaps and previous.limitations == limitations
            and previous.replan_count == resolved_replans):
        return previous
    version = 0 if previous is None else previous.version + 1
    return ObjectiveState(objective_ref=objective_ref, status=status,
                          requirement_refs=refs, limitations=limitations,
                          optional_gaps=gaps, replan_count=resolved_replans, version=version)


def optional_gaps(requirements: Iterable[ArtifactRequirement],
                  requirement_states: dict[str, RequirementState]) -> tuple[str, ...]:
    """Requirements that remain unmet, retained even when the objective is COMPLETE."""
    return tuple(
        item.requirement_id for item in requirements
        if requirement_states[item.requirement_id].status != "SATISFIED"
    )


def unmet_core_requirements(requirements: Iterable[ArtifactRequirement],
                            requirement_states: dict[str, RequirementState]) -> tuple[ArtifactRequirement, ...]:
    return tuple(
        item for item in requirements
        if item.base_criticality == "CORE" and requirement_states[item.requirement_id].status != "SATISFIED"
    )
