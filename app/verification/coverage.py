"""Closed-world aggregation of Judge-evaluated proposition states.

The Judge is the production authority writer. The response compatibility helper delegates
here for old stored projections, never supplies a second aggregation policy.
"""
from app.contracts.models import CoverageState, RequirementStatus, GoalStatus


def requirement_coverage(propositions) -> CoverageState:
    considered = [p for p in propositions if p.mandatory] or list(propositions)
    if not considered:
        return CoverageState.NOT_ATTEMPTED
    states = {p.coverage for p in considered}
    if states == {CoverageState.SATISFIED}:
        return CoverageState.SATISFIED
    if CoverageState.CONTRADICTED in states:
        return CoverageState.CONTRADICTED
    if states & {CoverageState.SATISFIED, CoverageState.PARTIAL}:
        return CoverageState.PARTIAL
    for state in (CoverageState.UNSUPPORTED, CoverageState.BLOCKED, CoverageState.NOT_ATTEMPTED):
        if state in states:
            return state
    return CoverageState.NOT_ATTEMPTED


def status_for_coverage(coverage):
    return {CoverageState.SATISFIED:RequirementStatus.SATISFIED,
            CoverageState.PARTIAL:RequirementStatus.PARTIAL,
            CoverageState.BLOCKED:RequirementStatus.BLOCKED}.get(coverage,RequirementStatus.UNSATISFIED)


def goal_status(propositions, *, clarification=None, background_ok=False, investigated=False):
    if clarification is not None:
        return GoalStatus.WAITING_FOR_USER
    mandatory = [p for p in propositions if p.mandatory]
    if mandatory and all(p.coverage == CoverageState.SATISFIED for p in mandatory):
        return GoalStatus.COMPLETE
    if background_ok or investigated or any(p.coverage in {
            CoverageState.SATISFIED,CoverageState.PARTIAL,CoverageState.CONTRADICTED}
            for p in (mandatory or propositions)):
        return GoalStatus.LIMITED
    return GoalStatus.FAILED
