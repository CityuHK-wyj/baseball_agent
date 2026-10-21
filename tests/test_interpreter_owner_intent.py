"""Per-owner typed intent survives interpretation; no sibling-role inheritance."""
from copy import deepcopy
import json
from types import SimpleNamespace
from app.semantic.interpreter import SemanticInterpreter
from app.semantic.goals import RequirementBuilder
from app.verification.propositions import build_propositions


class Replies:
    configured = True

    def __init__(self, *data):
        self.data = data
        self.calls = []

    def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(data=deepcopy(self.data[min(len(self.calls)-1, len(self.data)-1)]))


def proposal(query, *, mode=None):
    owner = {'text': query, 'objective': query, 'user_span': query}
    if mode:
        owner['evidence_intent'] = {'mode': mode, 'information_class': 'INJURY'}
    return {'analysis_requests': [owner], 'evidence_requests': [dict(owner,
        evidence_intent={'mode': 'PREDICTION', 'information_class': 'INJURY'})]}


def test_missing_analysis_intent_gets_one_schema_feedback_not_sibling_inheritance():
    query = 'Assess an uncertain return trajectory, retaining the range of outcomes.'
    llm = Replies(proposal(query), proposal(query, mode='PREDICTION'))
    semantic = SemanticInterpreter(llm).interpret(query)
    assert len(llm.calls) == 2
    assert 'intent' in json.loads(llm.calls[1]['user'])['schema_feedback'].lower()
    assert semantic.analysis_requests[0].evidence_intent.mode == 'PREDICTION'
    goal = RequirementBuilder().build(semantic)
    props = build_propositions(goal)
    owners = [r for r in goal.requirements if r.kind.value in {'ANALYSIS', 'EVIDENCE'}]
    assert owners and all(r.evidence_intent.mode == 'PREDICTION' for r in owners)
    assert all(p.evidence_intent.mode == 'PREDICTION' and p.assertion_expectation.allow_prediction
               for p in props if p.requirement_id in {r.id for r in owners})
    assert not [r for r in goal.requirements if r.kind.value == 'MEASURE']


def test_existing_owner_intents_do_not_get_merged_in_mixed_historical_and_forecast_task():
    historical = 'Report the already recorded home-run total.'
    future = 'Assess the uncertain future contribution.'
    query = historical + ' ' + future
    data = {'analysis_requests': [
        dict(text=historical, objective=historical, user_span=historical,
             evidence_intent={'mode': 'CONFIRMED_STATE', 'information_class': 'PERFORMANCE'}),
        dict(text=future, objective=future, user_span=future,
             evidence_intent={'mode': 'PREDICTION', 'information_class': 'PERFORMANCE'})]}
    llm = Replies(data)
    semantic = SemanticInterpreter(llm).interpret(query)
    assert len(llm.calls) == 1
    assert [r.evidence_intent.mode for r in semantic.analysis_requests] == ['CONFIRMED_STATE', 'PREDICTION']
    owners = [r for r in RequirementBuilder().build(semantic).requirements if r.kind.value == 'ANALYSIS']
    assert [r.evidence_intent.mode for r in owners] == ['CONFIRMED_STATE', 'PREDICTION']


def test_exhausted_missing_intent_is_material_uncertainty_not_confirmed_state():
    query = 'Describe the possible recovery path.'
    llm = Replies(proposal(query))
    semantic = SemanticInterpreter(llm).interpret(query)
    assert len(llm.calls) == 2
    assert any(a.material for a in semantic.ambiguities)
    assert semantic.evidence_requests[0].evidence_intent.mode == 'PREDICTION'
    assert semantic.analysis_requests[0].evidence_intent is None


def test_historical_rows_are_premises_not_a_verified_prediction():
    from app.verification.judge import Judge
    from tests.governed_operation_fixture import roots
    query = 'Calculate the projected home runs.'
    semantic = SemanticInterpreter(Replies(proposal(query, mode='PREDICTION'))).interpret(query)
    owner = next(r for r in RequirementBuilder().build(semantic).requirements if r.kind.value == 'ANALYSIS')
    judge = Judge()
    judge._expected_propositions = {}
    state = judge._analysis_state(owner, roots()[:1], {})
    assert state.status.value == 'PARTIAL'
    assert state.support_artifact_ids == [roots()[0].id]


def test_planner_and_composer_receive_existing_typed_owner_role():
    from app.planner.planner import Planner
    from app.capabilities.analytics.catalog import CATALOG
    from app.capabilities.registry import CapabilityRegistry
    from app.response.answer_contract import build_answer_package
    from app.response.composer import ResponseComposer
    from app.verification.judge import Judge
    query = 'Calculate the projected home runs.'
    semantic = SemanticInterpreter(Replies(proposal(query, mode='PREDICTION'))).interpret(query)
    goal = RequirementBuilder().build(semantic)
    owner = next(r for r in goal.requirements if r.kind.value == 'ANALYSIS')
    planner_llm = Replies({'needs': []})
    Planner(planner_llm, CapabilityRegistry(), CATALOG).plan(
        goal=goal, semantic=semantic, artifacts=[], needs=[], outcomes=[])
    payload = json.loads(planner_llm.calls[0]['user'])
    planned_owner = next(r for r in payload['requirements'] if r['id'] == owner.id)
    assert planned_owner['evidence_intent']['mode'] == 'PREDICTION'
    projection = Judge().evaluate(goal, [], [])
    package = build_answer_package(goal, projection)
    assert next(r for r in package.governed_requirements if r.id == owner.id).evidence_intent.mode == 'PREDICTION'
    rendered = ResponseComposer(None)._payload(goal, semantic, projection, package)
    assert '\"mode\": \"PREDICTION\"' in rendered
    assert owner.id in rendered


def test_explicit_confirmed_intent_is_not_overwritten_by_prediction_sibling():
    query = 'Separate observed status from the supplied outlook.'
    llm = Replies(proposal(query, mode='CONFIRMED_STATE'))
    semantic = SemanticInterpreter(llm).interpret(query)
    assert len(llm.calls) == 1
    assert semantic.analysis_requests[0].evidence_intent.mode == 'CONFIRMED_STATE'
    assert semantic.evidence_requests[0].evidence_intent.mode == 'PREDICTION'
