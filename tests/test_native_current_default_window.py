"""A default acquisition lookback is not a historical state-interval obligation."""
from datetime import date
import pytest
from app.contracts.models import (
    SemanticInterpretation,EvidenceRequest,EntityMention,EvidenceIntent,RelationSemantics,
    RelationKind,Artifact,Constraint,PlannerNeed,
)
from app.semantic.goals import RequirementBuilder
from app.verification.judge import Judge
from tests.test_dynamic_fact_safety_wsa08 import _dynamic_artifact


def current_goal(*,explicit=False,reporting=False):
    text='Which team does Aaron Judge currently play for?'
    g=RequirementBuilder().build(SemanticInterpretation(
        raw_query=text,language='en',entities=[EntityMention(mention='Aaron Judge',type_hint='player')],
        constraints=[Constraint(kind='date_range',value={'start':'2023-01-01','end':'2023-12-31'})] if explicit else [],
        evidence_requests=[EvidenceRequest(text=text,user_span=text,objective=text,
            evidence_intent=EvidenceIntent(mode='REPORTED_ACCOUNT' if reporting else 'CONFIRMED_STATE',information_class='STATUS'),
            relation_semantics=RelationSemantics(relation_kind=RelationKind.FACT,
                relation_kinds=[RelationKind.FACT],predicate='TEAM_MEMBERSHIP'))]),anchor_date=date.today())
    return g


def test_default_current_state_has_a_positive_native_claim():
    g=current_goal()
    identity=Artifact(id='identity',kind='ENTITY_RESOLUTION',status='ACCEPTED',structured_data={
        'resolved':[{'mention':'Aaron Judge','id':592450,'name':'Aaron Judge'}]})
    record=_dynamic_artifact(player_name='Aaron Judge',entity_id='592450')
    p=Judge().evaluate(g,[identity,record],[PlannerNeed(id=record.need_id,capability='dynamic_fact',objective=g.raw_query,
        requires=[r.id for r in g.requirements])])
    assert p.claims, [a.reasons for a in p.artifact_assessments]
    assert p.goal_status.value=='COMPLETE'


@pytest.mark.parametrize('explicit,reporting',[(True,False),(False,True)])
def test_current_record_does_not_satisfy_explicit_history_or_reporting(explicit,reporting):
    g=current_goal(explicit=explicit,reporting=reporting)
    record=_dynamic_artifact(player_name='Aaron Judge',entity_id='592450')
    p=Judge().evaluate(g,[record],[])
    assert not p.claims
    assert p.goal_status.value!='COMPLETE'
