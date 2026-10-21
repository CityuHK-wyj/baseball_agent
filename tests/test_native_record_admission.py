"""Native-record inspection must bind predicate, subject, time and exact fragment."""
import pytest

from app.contracts.models import (
    AnswerProposition, CoverageState, EvidenceRequest, RelationKind, RelationSemantics,
    SemanticInterpretation, AnalysisRequest, TemporalExpectation, EvidenceIntent,
)
from app.response.answer_contract import build_answer_package
from app.semantic.goals import RequirementBuilder
from app.verification.judge import Judge
from app.verification.propositions import build_propositions
from tests.test_dynamic_fact_safety_wsa08 import _goal_for_subject, _dynamic_artifact, _needs, TODAY


def case():
    return (_goal_for_subject('Aaron Judge'),
            _dynamic_artifact(player_name='Aaron Judge', entity_id='592450'))


def evaluate(goal, artifact):
    return Judge(max_inspections=0).evaluate(goal, [artifact], _needs())


def test_native_team_record_has_proposition_local_inspectable_frozen_support():
    goal, artifact = case()
    result = evaluate(goal, artifact)
    assert result.claims
    assessment = result.artifact_assessments[0]
    assert assessment.status == 'ELIGIBLE'
    assert assessment.record_support[0].locator == '/people/0/currentTeam'
    assert assessment.record_support[0].predicate == 'TEAM_MEMBERSHIP'
    assert assessment.record_support[0].subject_id == '592450'
    assert assessment.record_support[0].response_hash
    native_claim = next(c for c in result.claims if c.text.startswith('Dynamic fact:'))
    assert native_claim.proposition_ids == assessment.eligible_proposition_ids
    package = build_answer_package(goal, result)
    assert package.artifact_assessments[0] == assessment
    with pytest.raises(TypeError):
        assessment.record_support[0].predicate = 'INJURY_STATUS'
    with pytest.raises(TypeError):
        package.artifact_assessments[0].eligible_proposition_ids.append('foreign')
    artifact.structured_data['facts'][0]['value'] = 'a later mutation'
    assert 'a later mutation' not in native_claim.text


@pytest.mark.parametrize('mutation', ['envelope', 'hash', 'value', 'relation', 'as_of', 'name', 'team_id', 'url'])
def test_native_record_metadata_is_not_its_own_proof(mutation):
    goal, artifact = case()
    data = artifact.structured_data
    fact = data['facts'][0]
    if mutation == 'envelope': data['provider_responses'] = []
    if mutation == 'hash': data['provider_responses'][0]['content_hash'] = 'forged'
    if mutation == 'value': fact['value'] = 'a model-generated value'
    if mutation == 'relation': fact['relation'] = 'INJURY_STATUS'
    if mutation == 'as_of': fact['as_of'] = data['as_of'] = '1980-01-01'
    if mutation == 'name': fact['player_name'] = 'Shohei Ohtani'
    if mutation == 'team_id': fact['team_id'] = 119
    if mutation == 'url': fact['source_url'] = 'https://example.com/unrelated'
    # These presentation/producer confidence labels can never rescue an invalid fragment.
    artifact.confidence = 'STRONG'
    fact['epistemic_status'] = 'VERIFIED_TRUE'
    result = evaluate(goal, artifact)
    assert not result.claims
    assert result.goal_status.value != 'COMPLETE'
    assert not result.artifact_assessments[0].eligible_proposition_ids


def test_current_team_cannot_be_relabelled_as_an_injury_predicate():
    goal, artifact = case()
    owner = next(r for r in goal.requirements if r.id == 'r_ev')
    owner.relation_semantics = RelationSemantics(relation_kind=RelationKind.STATUS,
                                               predicate='INJURY_STATUS')
    owner.statement = 'Aaron Judge current injury status'
    owner.value = {'objective': owner.statement}
    result = evaluate(goal, artifact)
    assert not result.claims
    assert result.goal_status.value != 'COMPLETE'


def test_proposition_metadata_cannot_mint_a_predicate_absent_from_its_requirement():
    goal, artifact = case()
    next(r for r in goal.requirements if r.id == 'r_ev').relation_semantics = None
    goal.propositions = [AnswerProposition(id='p', requirement_id='r_ev', kind='FACT',
        statement='current team', required_information='current team', subject='Aaron Judge',
        relation_semantics=RelationSemantics(predicate='TEAM_MEMBERSHIP'))]
    assert not evaluate(goal, artifact).claims


@pytest.mark.parametrize('mode', ['HISTORICAL', 'AS_OF', 'DATE_RANGE'])
def test_current_record_cannot_attest_a_different_requested_time(mode):
    goal, artifact = case()
    goal.propositions = build_propositions(goal)
    prop = next(p for p in goal.propositions if p.requirement_id == 'r_ev')
    prop.temporal_expectation = TemporalExpectation(mode=mode, as_of='1980-01-01',
                                                    start='1980-01-01', end='1980-12-31')
    result = evaluate(goal, artifact)
    assert not result.claims
    assert prop.id not in result.artifact_assessments[0].eligible_proposition_ids


def test_same_artifact_cannot_broaden_one_native_claim_to_an_unmatched_sibling_slot():
    goal, artifact = case()
    goal.propositions = [
        AnswerProposition(id='team', requirement_id='r_ev', kind='FACT', subject='Aaron Judge',
            statement='current team', required_information='current team',
            relation_semantics=RelationSemantics(predicate='TEAM_MEMBERSHIP')),
        AnswerProposition(id='injury', requirement_id='r_ev', kind='FACT', subject='Aaron Judge',
            statement='injury status', required_information='injury status', information_class='INJURY',
            relation_semantics=RelationSemantics(predicate='INJURY_STATUS')),
    ]
    result = evaluate(goal, artifact)
    assert result.claims
    assert result.goal_status.value != 'COMPLETE'
    assert all(c.proposition_ids == ['team'] for c in result.claims)
    by_id = {p.id: p for p in result.propositions}
    assert by_id['team'].coverage == CoverageState.SATISFIED
    assert by_id['injury'].coverage != CoverageState.SATISFIED
    assert not by_id['injury'].supporting_claim_ids


def test_extra_collected_record_does_not_enter_a_claim_for_the_first_subject():
    goal, artifact = case()
    other = _dynamic_artifact(player_name='Shohei Ohtani', entity_id='660271')
    artifact.structured_data['facts'].extend(other.structured_data['facts'])
    artifact.structured_data['provider_responses'].extend(other.structured_data['provider_responses'])
    artifact.actual_scope.entity_names.extend(['Shohei Ohtani', 'Aaron Judge'])
    artifact.text_content += ' Aaron Judge Shohei Ohtani '
    result = evaluate(goal, artifact)
    assert result.claims
    assert all('Shohei Ohtani' not in c.text for c in result.claims)
    assert {s.record_index for s in result.artifact_assessments[0].record_support} == {0}


def test_ambiguous_subject_cannot_be_resolved_by_a_native_record():
    goal, artifact = case()
    req = next(r for r in goal.requirements if r.id == 'r_ent')
    req.entity_ref.identity_verified = False
    req.entity_ref.canonical_id = None
    # Entity-name overlap and actual_scope are not an independent canonical binding.
    artifact.actual_scope.entity_names.append('Aaron Judge')
    assert not evaluate(goal, artifact).claims


def _analysis_proposition(objective, relation):
    semantic = SemanticInterpretation(raw_query='Show the requested numbers', analysis_requests=[
        AnalysisRequest(text=objective, objective=objective, user_span='Show the requested numbers',
            analytical_role='BATTER', relation_semantics=RelationSemantics(relation_kind=relation))])
    goal = RequirementBuilder().build(semantic)
    requirement = next(r for r in goal.requirements if r.kind.value == 'ANALYSIS')
    return next(p for p in build_propositions(goal) if p.requirement_id == requirement.id)


def test_deterministic_relation_class_wins_over_a_conflicting_interpreter_proposal():
    # A rank/count question must not become a typed comparative claim because the
    # interpreter volunteered COMPARISON, nor because wording contains a loose cue.
    assert _analysis_proposition('most home runs leaderboard', 'COMPARISON').kind.value == 'VALUE'
    assert _analysis_proposition('difference in home runs', 'AGGREGATION').kind.value == 'COMPARISON'


def test_interpreter_relation_supplements_otherwise_undecided_wording():
    # With no deterministic relation cue, the interpreter's governed proposal is used.
    assert _analysis_proposition('opposing pitcher handedness breakdown', 'AGGREGATION').kind.value == 'VALUE'


@pytest.mark.parametrize('mode', ['REPORTED_ACCOUNT', 'FIRSTHAND_ACCOUNT', 'PREDICTION'])
def test_native_state_is_not_a_reporting_account_or_forecast(mode):
    goal, artifact = case()
    owner = next(r for r in goal.requirements if r.id == 'r_ev')
    owner.evidence_intent = EvidenceIntent(mode=mode, information_class='STATUS')
    result = evaluate(goal, artifact)
    assert not result.claims
    assert not result.artifact_assessments[0].eligible_proposition_ids


def test_native_observation_can_support_its_actual_as_of_date():
    goal, artifact = case()
    goal.propositions = build_propositions(goal)
    prop = next(p for p in goal.propositions if p.requirement_id == 'r_ev')
    prop.temporal_expectation = TemporalExpectation(mode='AS_OF', as_of=TODAY)
    assert evaluate(goal, artifact).claims


def test_model_relation_scope_is_rebound_by_requirement_construction():
    semantic = SemanticInterpretation(raw_query='Current team for Aaron Judge', evidence_requests=[
        EvidenceRequest(text='current team', user_span='Current team',
            relation_semantics=RelationSemantics(relation_kind='FACT', predicate='TEAM_MEMBERSHIP',
                                                time_scope={'season': 1980}, population='POSTSEASON'))])
    goal = RequirementBuilder().build(semantic)
    req = next(r for r in goal.requirements if r.kind.value == 'EVIDENCE')
    assert req.relation_semantics.time_scope == {}
    assert req.relation_semantics.population is None
    assert req.relation_semantics.source_text == 'current team'
