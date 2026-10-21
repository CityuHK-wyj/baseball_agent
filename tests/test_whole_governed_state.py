"""Whole-state adversaries; presentation never edits a governed snapshot."""
import pytest
from app.contracts.models import AnswerProjection, AnswerProposition, Claim, Reference, CoverageState, GoalStatus, RequirementStatus
from app.verification.governed_state import validate_governed_projection, governed_limited_fallback, GovernedStateError
from tests.governed_state_fixture import approved_snapshot


def snapshot():
    return approved_snapshot(AnswerProjection(goal_id='g',goal_status='LIMITED',propositions=[
        AnswerProposition(id='p1',requirement_id='r',kind='FACT',statement='first',coverage='SATISFIED'),
        AnswerProposition(id='p2',requirement_id='r',kind='FACT',statement='second',coverage='PARTIAL')],
        claims=[Claim(id='c',text='approved first fact',proposition_ids=['p1'],
                      support_refs=[Reference(ref_type='ARTIFACT',ref_id='source')])]))


@pytest.mark.parametrize('attack',[
    'false_complete','stale_satisfied','wrong_coverage','stale_support','stale_gap','missing_assessment',
    'unproved_owner','dangling_claim','unowned_claim','unbacked_claim','duplicate_claim',
    'dangling_support','cyclic_dependency','missing_dependency','failed_with_support','dangling_artifact',
])
def test_whole_state_rejects_incoherence(attack):
    p=AnswerProjection.model_validate(snapshot().model_dump(mode='json'))
    if attack=='false_complete': p.goal_status='COMPLETE'
    elif attack=='stale_satisfied': p.requirement_assessments[0].status='SATISFIED'
    elif attack=='wrong_coverage': p.requirement_assessments[0].coverage='SATISFIED'
    elif attack=='stale_support': p.requirement_assessments[0].support_artifact_ids=['forged']
    elif attack=='stale_gap': p.requirement_assessments[0].coverage_gaps=['invented']
    elif attack=='missing_assessment': p.requirement_assessments=[]
    elif attack=='unproved_owner': p.requirement_assessments[0].authority_status='UNPROVEN'
    elif attack=='dangling_claim': p.claims[0].proposition_ids=['missing']
    elif attack=='unowned_claim': p.claims[0].proposition_ids=[]
    elif attack=='unbacked_claim': p.claims[0].support_refs=[]
    elif attack=='duplicate_claim': p.claims.append(p.claims[0])
    elif attack=='dangling_support': p.propositions[0].supporting_claim_ids=['missing']
    elif attack=='cyclic_dependency': p.propositions[0].depends_on=['p2'];p.propositions[1].depends_on=['p1']
    elif attack=='missing_dependency': p.propositions[0].depends_on=['missing']
    elif attack=='failed_with_support': p.goal_status='FAILED'
    elif attack=='dangling_artifact': p.claims[0].support_refs[0].ref_id='missing'
    with pytest.raises(GovernedStateError): validate_governed_projection(p)


def test_finalization_errors_retain_bounded_orchestrator_recovery(monkeypatch):
    from app.verification.judge import Judge
    from app.runtime.orchestrator import Orchestrator
    from tests.governed_operation_fixture import compute
    g,artifact,source = compute()
    def reject(projection, **kwargs):
        raise GovernedStateError('owned finalization inconsistency')
    monkeypatch.setattr('app.verification.governed_state.validate_governed_projection',reject)
    lifecycle = Orchestrator()
    with pytest.raises(GovernedStateError):
        lifecycle.evaluate(lambda:Judge().evaluate(g,[*source,artifact],[]))
    assert lifecycle.judge_attempts == 2 and lifecycle.governed_retries == 1
    assert lifecycle.last_invalid_projection is not None
    assert lifecycle.last_invalid_projection.is_governed_frozen


def test_recovery_retains_valid_partial_claim_and_reaggregates_every_parent():
    p=AnswerProjection.model_validate(snapshot().model_dump(mode='json'))
    p.propositions[1].coverage=CoverageState.SATISFIED
    p.goal_status=GoalStatus.COMPLETE
    p.requirement_assessments[0].status=RequirementStatus.SATISFIED
    p.requirement_assessments[0].coverage=CoverageState.SATISFIED
    source=p.frozen_snapshot();before=source.model_dump_json()
    new=governed_limited_fallback(source)
    validate_governed_projection(new)
    assert source.model_dump_json()==before
    assert new is not source and new.goal_status.value=='LIMITED'
    assert [c.id for c in new.claims]==['c']
    assert new.propositions[0].coverage.value=='SATISFIED'
    assert new.propositions[1].coverage.value=='PARTIAL'
    assert new.requirement_assessments[0].status.value=='PARTIAL'
    with pytest.raises(TypeError): new.requirement_assessments[0].status='SATISFIED'
