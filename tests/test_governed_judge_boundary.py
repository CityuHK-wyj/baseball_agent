"""Mechanism tests for the immediately frozen Judge/governed coverage boundary."""
from itertools import product

import pytest

from app.contracts.models import (
    AnswerProposition, CoverageState, GoalStatus, RequirementStatus,
)
from app.response.answer_contract import build_answer_package
from app.verification.coverage import requirement_coverage
from app.verification.judge import Judge
from tests.test_semantic_judge import _goal, _needs, _entity_artifact, _stats_artifact


@pytest.mark.parametrize("left,right", list(product(CoverageState, repeat=2)))
def test_parent_satisfied_iff_every_mandatory_child_satisfied(left, right):
    children = [AnswerProposition(statement="A", coverage=left),
                AnswerProposition(statement="B", coverage=right)]
    assert (requirement_coverage(children) == CoverageState.SATISFIED) == (
        left == right == CoverageState.SATISFIED)


def test_optional_child_does_not_block_mandatory_completion():
    assert requirement_coverage([
        AnswerProposition(statement="A", coverage=CoverageState.SATISFIED),
        AnswerProposition(statement="B", mandatory=False, coverage=CoverageState.UNSUPPORTED),
    ]) == CoverageState.SATISFIED


def _evaluate():
    goal = _goal()
    projection = Judge().evaluate(goal, [_entity_artifact(), _stats_artifact()], _needs())
    return goal, projection


def test_judge_publishes_requirement_assessments():
    goal, projection = _evaluate()
    assert projection.goal_status == GoalStatus.COMPLETE
    assert {a.requirement_id for a in projection.requirement_assessments} == {r.id for r in goal.requirements}
    assert all(a.coverage == CoverageState.SATISFIED for a in projection.requirement_assessments)
    assert all(a.status == RequirementStatus.SATISFIED for a in projection.requirement_assessments)
    assert any(a.support_refs for a in projection.requirement_assessments)


@pytest.mark.parametrize("mutation", [
    lambda p: setattr(p, "goal_status", GoalStatus.LIMITED),
    lambda p: p.claims.clear(),
    lambda p: p.limitations.append("presentation supplied"),
    lambda p: setattr(p.claims[0], "text", "model-invented fact"),
    lambda p: p.claims[0].proposition_ids.append("invented"),
    lambda p: setattr(p.requirement_assessments[0], "status", RequirementStatus.UNSATISFIED),
    lambda p: setattr(p.propositions[0], "coverage", CoverageState.UNSUPPORTED),
    lambda p: p.claims[0].support_refs[0].metadata.update({"invented": True}),
    lambda p: setattr(p, "consistency", None),
    lambda p: p.propositions.__setitem__(0, p.propositions[-1]),
])
def test_judge_output_is_deeply_immutable_immediately(mutation):
    _, projection = _evaluate()
    before = projection.model_dump(mode="json")
    with pytest.raises(TypeError, match="immutable"):
        mutation(projection)
    assert projection.model_dump(mode="json") == before
    assert projection.is_governed_frozen


def test_snapshot_detaches_from_mutable_goal_and_next_judge_pass():
    goal, first = _evaluate()
    before = first.model_dump_json()
    second = Judge().evaluate(goal, [], _needs())
    assert second is not first
    assert second.goal_status != first.goal_status
    goal.propositions[0].coverage = CoverageState.UNSUPPORTED
    assert first.model_dump_json() == before


def test_package_consumes_assessments_without_recomputing_coverage(monkeypatch):
    goal, projection = _evaluate()
    def forbidden(*args):
        raise AssertionError("response layer must not recompute Judge authority")
    monkeypatch.setattr("app.response.answer_contract._requirement_coverage", forbidden)
    package = build_answer_package(goal, projection)
    assert all(entry.satisfied for entry in package.requirements)


def test_presentation_has_no_judge_reconciliation_writer():
    assert not hasattr(Judge(), "reconcile_expression")
