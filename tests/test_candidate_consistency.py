"""Closed-world candidate consistency: no contradictory restriction, no redundant role."""
import pytest

from app.contracts.models import AnalysisRequest, Constraint, SemanticInterpretation
from app.semantic.analytical_candidate import ground_candidate
from app.semantic.goals import RequirementBuilder

_TEXT = "面对左投和右投的打击率分别是多少"


def _candidate(**overrides):
    base = {"analytical_role": "BATTER", "source_text": _TEXT}
    base.update(overrides)
    return base


@pytest.mark.parametrize("filters", [
    [{"op": "COMPARE", "field": "p_throws", "comparator": "=", "value": "L"},
     {"op": "COMPARE", "field": "p_throws", "comparator": "=", "value": "R"}],
    [{"op": "IN", "field": "p_throws", "values": ["L"]},
     {"op": "IN", "field": "p_throws", "values": ["R"]}],
    [{"op": "COMPARE", "field": "p_throws", "comparator": "=", "value": "L"},
     {"op": "IN", "field": "p_throws", "values": ["R"]}],
])
def test_mutually_exclusive_restrictions_on_one_field_are_not_grounded(filters):
    grounded, issues = ground_candidate(_candidate(filters=filters))
    assert not grounded
    assert any("contradictory" in issue or "disjoint" in issue for issue in issues)


@pytest.mark.parametrize("filters", [
    [{"op": "IN", "field": "p_throws", "values": ["L", "R"]}],
    [{"op": "COMPARE", "field": "p_throws", "comparator": "=", "value": "L"},
     {"op": "COMPARE", "field": "strikes", "comparator": "=", "value": 2}],
])
def test_consistent_restrictions_still_ground(filters):
    grounded, issues = ground_candidate(_candidate(filters=filters))
    assert not issues, issues
    assert grounded.get("filters")


def test_grouping_is_the_governed_representation_of_a_split():
    grounded, issues = ground_candidate(_candidate(group_by=["p_throws"]))
    assert not issues, issues
    assert grounded["group_by"] == ["p_throws"]


def _semantic(analysis_role, candidate_role):
    return SemanticInterpretation(
        raw_query=_TEXT,
        analysis_requests=[AnalysisRequest(text=_TEXT, objective="batting split", user_span=_TEXT,
                                           analytical_role=analysis_role)],
        constraints=[Constraint(kind="analytical", value=_candidate(analytical_role=candidate_role,
                                                                    group_by=["p_throws"]), explicit=True)],
    )


def test_role_only_candidate_redundant_with_analysis_request_is_dropped():
    goal = RequirementBuilder().build(_semantic("BATTER", "BATTER"))
    constraints = [r for r in goal.requirements if r.kind.value == "CONSTRAINT"]
    # The split grouping is retained; no separate role-only candidate is created.
    assert all("analytical_role" not in (r.value if isinstance(r.value, dict) else {})
               or set(r.value) - {"analytical_role"} for r in constraints)


def test_role_conflicting_with_analysis_request_is_not_silently_dropped():
    goal = RequirementBuilder().build(_semantic("BATTER", "PITCHER"))
    role_constraints = [r for r in goal.requirements if r.kind.value == "CONSTRAINT"
                        and isinstance(r.value, dict) and r.value.get("analytical_role")]
    assert role_constraints, "a conflicting role must remain an audit-visible obligation"


def test_contradictory_candidate_does_not_poison_the_finite_split_adapter():
    semantic = SemanticInterpretation(
        raw_query="面对左投和右投的打击率分别是多少",
        analysis_requests=[AnalysisRequest(text="batting average split by opposing pitcher handedness",
                                           objective="batting average split by opposing pitcher handedness",
                                           user_span="面对左投和右投的打击率分别是多少", analytical_role="BATTER")],
        constraints=[Constraint(kind="analytical", value={
            "analytical_role": "BATTER",
            "filters": [{"op": "COMPARE", "field": "p_throws", "comparator": "=", "value": "L"},
                        {"op": "COMPARE", "field": "p_throws", "comparator": "=", "value": "R"}],
            "group_by": ["p_throws"], "source_text": "面对左投和右投"}, explicit=True)],
    )
    goal = RequirementBuilder().build(semantic)
    from app.capabilities.analytics.conformance import requested_contract
    payload = [r.model_dump(mode="json") for r in goal.requirements]
    contract = requested_contract({}, dict(analytical_requirements=payload,
                                           bound_user_intent=goal.raw_query), [])
    assert contract.group_by == ("p_throws",)
    # The contradictory equality filters never reach execution.
    assert not any(f.get("field") == "p_throws" and f.get("comparator") == "="
                   for f in contract.filters)
