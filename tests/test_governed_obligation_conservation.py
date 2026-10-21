import pytest
from app.contracts.models import AnswerProjection, Goal, UserRequirement, GoalStatus, PropositionKind
from app.runtime.orchestrator import Orchestrator
from app.verification.governed_state import GovernedStateError, reaggregate_assessments
from tests.test_whole_governed_state import snapshot


def source_and_omission():
    source=AnswerProjection.model_validate(snapshot().model_dump(mode='json'))
    goal=Goal(id='g',statement='two obligations',raw_query='two obligations',
              requirements=[UserRequirement(id='r',kind='ANALYSIS',statement='two obligations')],
              propositions=source.propositions)
    candidate=AnswerProjection.model_validate(source.model_dump(mode='json'))
    candidate.propositions=candidate.propositions[:1]
    candidate.requirement_assessments=reaggregate_assessments(candidate)
    candidate.goal_status=GoalStatus.COMPLETE
    return goal,candidate


def test_orchestrator_rejects_omitted_mandatory_child_against_source_goal():
    goal,candidate=source_and_omission(); lifecycle=Orchestrator()
    with pytest.raises(GovernedStateError):
        lifecycle.evaluate(lambda:candidate.frozen_snapshot(),goal=goal)
    assert lifecycle.judge_attempts==2 and lifecycle.governed_retries==1


def test_recovery_restores_source_obligation_without_promoting_support():
    from app.verification.governed_state import ExpectedObligations, governed_limited_fallback, validate_governed_projection
    goal,candidate=source_and_omission();expected=ExpectedObligations.from_goal(goal)
    original=candidate.model_dump_json()
    recovered=governed_limited_fallback(candidate,expected=expected)
    validate_governed_projection(recovered,expected=expected)
    assert candidate.model_dump_json()==original
    assert recovered.goal_status==GoalStatus.LIMITED
    assert {p.id for p in recovered.propositions}=={'p1','p2'}
    assert next(p for p in recovered.propositions if p.id=='p2').coverage.value=='UNSUPPORTED'
    assert [c.id for c in recovered.claims]==['c']
    assert recovered.requirement_assessments[0].coverage.value=='PARTIAL'


@pytest.mark.parametrize('field,value',[
    ('statement','substituted meaning'),('subject','different player'),
    ('time_scope',{'season':2030}),('kind',PropositionKind.SCOPE),
])
def test_source_manifest_rejects_semantic_drift(field,value):
    from app.verification.governed_state import ExpectedObligations, validate_governed_projection
    goal,_=source_and_omission();expected=ExpectedObligations.from_goal(goal)
    candidate=AnswerProjection.model_validate(snapshot().model_dump(mode='json'))
    setattr(candidate.propositions[0],field,value)
    with pytest.raises(GovernedStateError):validate_governed_projection(candidate,expected=expected)


def test_source_manifest_is_detached_from_judge_mutation():
    from app.verification.governed_state import ExpectedObligations, validate_governed_projection
    goal,candidate=source_and_omission();expected=ExpectedObligations.from_goal(goal)
    goal.propositions=goal.propositions[:1]
    with pytest.raises(GovernedStateError):validate_governed_projection(candidate,expected=expected)
