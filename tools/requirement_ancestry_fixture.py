"""Explicit bound-user roots for synthetic tests/evaluation; NEVER production imported.

Old synthetic producers declared authoritative semantic Requirements directly. The new
runtime requires ancestry as well. These helpers state that test-owned authority root;
they do not manufacture roots for inferred/Planner additions. Existing roots are kept
unless a test explicitly requests re-binding after changing its synthetic USER message.
"""
from functools import wraps
from pydantic import model_validator
from app.contracts.models import Goal as RuntimeGoal, RequirementProvenance
from app.semantic.provenance import message_digest


def root_user_requirements(requirements, raw, message_id="fixture_user_message", *, replace=False):
    for requirement in requirements:
        if requirement.get("source", "user") != "user" or (requirement.get("provenance") is not None and not replace):
            continue
        requirement["provenance"] = RequirementProvenance(
            origin="CURRENT_USER_MESSAGE", message_id=message_id, bound_message=raw,
            message_digest=message_digest(raw), user_span=raw,
        ).model_dump(mode="json")
    return requirements


def root_goal(goal, *, replace=False):
    raw = goal.raw_query or goal.statement
    for requirement in goal.requirements:
        if requirement.source == "user" and (requirement.provenance is None or replace):
            payload = root_user_requirements([requirement.model_dump(mode="json")], raw, goal.id, replace=replace)[0]
            requirement.provenance = RequirementProvenance.model_validate(payload["provenance"])
    return goal


def rooted_case(factory):
    @wraps(factory)
    def build(*args, **kwargs):
        case = factory(*args, **kwargs)
        inputs = case["inputs"]
        raw = inputs.get("bound_user_intent") or inputs.get("raw_query") or "Calculate bound metrics"
        root_user_requirements(inputs.get("analytical_requirements", []), raw)
        root_user_requirements(case.get("requirements", []), raw)
        return case
    return build


def rooted_goal(factory):
    @wraps(factory)
    def build(*args, **kwargs):
        return Goal.model_validate(factory(*args, **kwargs).model_dump())
    return build


class Goal(RuntimeGoal):
    """Test-owned Goal fixture: explicitly establish roots at initial construction only."""

    @model_validator(mode="after")
    def fixture_user_roots(self):
        return root_goal(self)
