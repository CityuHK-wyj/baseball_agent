"""Bounded relation/expression/privacy regressions; all secrets/providers are fixtures."""
import json
from types import SimpleNamespace

import pytest

from app.contracts.models import ConsistencyStatus, GoalStatus, PropositionKind, SemanticInterpretation, VerificationStatus
from app.observe.diagnostics import DiagnosticRecorder
from app.response.composer import ResponseComposer
from app.verification.consistency import check_status_consistency, repair_answer
from app.verification.evidence import verify_span
from app.verification.judge import Judge
from tests.assessment_fixtures import FixtureAssessorLLM
from tests.test_phase5b_final_repair import chain, projection, value_projection


class _SemanticLLM:
    configured = True

    def __init__(self, value):
        self.value = value

    def complete_json(self, *, role="response", system="", user="", **kwargs):
        from tests.assessment_fixtures import grounding_receipts, passage_from_user, reviewer_reply
        reply = reviewer_reply(system)
        if reply is not None:
            return reply
        passage = passage_from_user(user)
        return SimpleNamespace(data={
            'status': 'SUPPORTED', 'fact': {'subject': 'Shohei Ohtani', 'metric': 'home runs',
                                            'value': self.value, 'scope': '2024'},
            'grounding': grounding_receipts(passage),
        })


def semantic(value):
    return _SemanticLLM(value)


def test_f1_exact_evidence_to_judge():
    p,d,s,_ = chain('In 2024 Shohei Ohtani hit 25 home runs. Shohei Ohtani hit 30 home runs.')
    p.kind = PropositionKind.VALUE
    p.statement = 'Shohei Ohtani hit 30 home runs'
    f = verify_span(p,s,d,allow_llm=False)
    assert f.status != VerificationStatus.SUPPORTED
    assert projection(p,d,s,f,[d.model_dump(mode='json')]).goal_status != GoalStatus.COMPLETE


def test_f1_exact_semantic_relation():
    p,d,s,_ = chain('In 2024 Shohei Ohtani hit 25 home runs. Shohei Ohtani hit 30 home runs.')
    p.kind = PropositionKind.VALUE
    assert verify_span(p,s,d,llm=semantic('30')).status != VerificationStatus.SUPPORTED


@pytest.mark.parametrize('answer', [
    'Shohei Ohtani hit 54 home runs is untrue.',
    'The assertion Shohei Ohtani hit 54 home runs is fabricated.',
    'Shohei Ohtani hit 54 home runs? No.',
    'Shohei Ohtani hit 54 home runs. That assertion is fabricated.',
    'Shohei Ohtani hit 54 home runs. The preceding sentence is untrue.',
])
def test_f2_retraction_is_not_expression(answer):
    g,out = value_projection('Shohei Ohtani hit 54 home runs.')
    c = check_status_consistency(g,out,answer)
    assert c.status != ConsistencyStatus.CONSISTENT
    fixed = repair_answer(out,answer,c.retracted_proposition_ids)
    assert answer not in fixed
    assert check_status_consistency(g,out,fixed).status == ConsistencyStatus.CONSISTENT


@pytest.mark.parametrize('text', ['Shohei Ohtani did not pitch in 2024.',
                                'Player Alpha never pitched in 2023.',
                                'The injury report was false.'])
def test_f3_accepted_negative_fact(text):
    g,out = value_projection(text)
    out.propositions[0].kind = PropositionKind.FACT
    assert check_status_consistency(g,out,text).status == ConsistencyStatus.CONSISTENT
    fixed = repair_answer(out,'omitted',['p'])
    assert check_status_consistency(g,out,fixed).status == ConsistencyStatus.CONSISTENT


def test_f4_exact_final_boundary(tmp_path):
    rec = DiagnosticRecorder(tmp_path/'diag.json',research_content=True)
    rec.record('research',input_tokens=12)
    rec.records[0]['data']['documents'] = [{'body_text': '{"client_secret": "prefix FRESHSECRET789"}'}]
    rec.write()
    assert 'FRESHSECRET789' not in rec.path.read_text()
    assert json.loads(rec.path.read_text())[0]['data']['input_tokens'] == 12


@pytest.mark.parametrize('text,value,ok', [
    ('In 2024 Player Alpha hit 25 home runs. Player Alpha hit 30 home runs.', '30', False),
    ('In 2024 Player Alpha hit 25 home runs and in 2023 hit 30 home runs.', '30', False),
    ('Player Alpha hit 25 home runs in 2023 compared with 30 in 2024.', '25', False),
    ('In 2024 Player Alpha hit 25 home runs and 30 doubles.', '30', False),
    ('In 2024 Player Alpha hit 25 home runs while Player Beta hit 30 home runs.', '30', False),
    ('In 2024 Player Alpha hit 25 home runs. In 2023 Player Alpha hit 30 home runs.', '30', False),
    ('In 2024 Player Alpha hit 25 home runs. In 2024 Player Alpha hit 30 home runs.', '30', True),
    ('In 2024 Player Alpha hit 25 home runs while Player Beta hit 30 home runs.', '25', True),
    ('In 2024 Player Alpha hit 25 home runs and hit 30 home runs.', '30', True),
    ("In 2024 Player Alpha's 25 home runs exceeded Player Beta's 20 home runs.", '25.0', True),
])
def test_one_compatible_relation(text,value,ok):
    p,d,s,_ = chain(text)
    p.subject = 'Player Alpha'
    p.statement = 'Player Alpha hit ' + value + ' home runs'
    p.kind = PropositionKind.VALUE
    # WSA-04: deterministic token/value binding is candidate eligibility, not entailment. A
    # competent assessor supplies the typed relation; the deterministic gate still verifies
    # the value/attribution. The negative cases never invoke the assessor.
    f = verify_span(p,s,d,llm=FixtureAssessorLLM() if ok else None,allow_llm=ok)
    assert (f.status == VerificationStatus.SUPPORTED) == ok
    out = projection(p,d,s,f,[d.model_dump(mode='json')])
    assert (out.goal_status == GoalStatus.COMPLETE) == ok
    if ok:
        answer = ResponseComposer(None).compose(value_projection('')[0],SemanticInterpretation(raw_query='result'),out)
        assert check_status_consistency(value_projection('')[0],out,answer).status == ConsistencyStatus.CONSISTENT


def test_unknown_attribution_never_calls_model():
    p,d,s,_ = chain('In 2024 Shohei Ohtani and Aaron Judge discussed 25 home runs.')
    class Model:
        configured = True
        def complete_json(self,**kwargs):
            pytest.fail('UNKNOWN attribution must not invoke semantic refinement')
    assert verify_span(p,s,d,llm=Model()).status != VerificationStatus.SUPPORTED


@pytest.mark.parametrize('text', ['Shohei Ohtani hit 54 home runs.',
                                'Shohei Ohtani did not pitch in 2024.'])
def test_composer_mandatory_results_survive_model_failure(text):
    """When the composer model is unavailable, the deterministic factual rendering still
    expresses every mandatory result with its citation (M2.6)."""
    g,out = value_projection(text)
    out.propositions[0].kind = PropositionKind.FACT
    class Model:
        configured = True
        def complete_text(self,**kwargs):
            raise RuntimeError('model unavailable')
    answer = ResponseComposer(Model()).compose(g,SemanticInterpretation(raw_query='result'),out)
    assert text in answer
    assert '[S1]' in answer
    assert check_status_consistency(g,out,answer).status == ConsistencyStatus.CONSISTENT


@pytest.mark.parametrize('text', ['{"client_secret": "prefix FRESHSECRET789"}',
    '{"password": {"nested": ["prefix FRESHSECRET789"]}}',
    'https%3A%2F%2Fx.test%2F%3Fclient_secret%3Dprefix%2520FRESHSECRET789',
    'Malformed JSON {"client_secret": "prefix FRESHSECRET789',
    {'status':'FRESHSECRET789','nested':['other prose']},
    'Public article with an unlabeled FRESHSECRET789'])
@pytest.mark.parametrize('research',[False,True])
def test_raw_body_is_disabled_at_write(text,tmp_path,research):
    rec = DiagnosticRecorder(tmp_path/'diag.json',research_content=research)
    rec.record('research',status='OK',total_tokens=19)
    rec.records[0]['data']['documents'] = [{'body_text':text,'metadata':{
        'client%255Fsecret':{'nested':['prefix FRESHSECRET789']}}}]
    rec.write()
    raw = rec.path.read_text()
    assert 'FRESHSECRET789' not in raw
    data = json.loads(raw)[0]['data']
    assert data['total_tokens'] == 19 and data['status'] == 'OK'
    assert data['documents'][0]['metadata']['client%255Fsecret'] == '***'


@pytest.mark.parametrize('answer,ok', [
    ('Player Alpha 2024 regular season AVG .300',True),
    ('Player Alpha 2024 regular season AVG .300 is fabricated',False),
    ('Player Alpha 2024 regular season AVG .300 .500',False),
    ('Player Alpha 2024 regular season AVG .350',False),
    ('Player Beta 2024 regular season AVG .300',False),
])
def test_structured_values_are_expression_units(answer,ok):
    g,out = value_projection('table has auxiliary count 1234')
    out.propositions[0].requirement_id = 'r'
    out.claims[0].answer_values = [{'requirement_id':'r','entity':'Player Alpha','metric':'avg',
        'value':.3,'season':2024,'population':'REGULAR_SEASON','unit':None}]
    assert (check_status_consistency(g,out,answer).status == ConsistencyStatus.CONSISTENT) == ok


@pytest.mark.parametrize('text,value,ok', [
    ('In the 2024 postseason Player Alpha hit 25 home runs. Player Alpha hit 30 home runs.','30',False),
    ('In the 2024 postseason Player Alpha hit 25 home runs while Player Beta hit 30 home runs.','25',True),
    ('In the 2024 postseason Player Alpha hit 25 home runs while Player Alpha hit 30 home runs in the regular season.','30',False),
])
def test_population_and_value_are_one_relation(text,value,ok):
    p,d,s,_ = chain(text)
    p.subject = 'Player Alpha'
    p.population = 'POSTSEASON'
    p.statement = 'Player Alpha hit ' + value + ' home runs'
    p.kind = PropositionKind.VALUE
    finding = verify_span(p,s,d,llm=FixtureAssessorLLM() if ok else None,allow_llm=ok)
    assert (finding.status == VerificationStatus.SUPPORTED) == ok


def test_rounding_preserves_counts_and_display_precision():
    g,out = value_projection('Shohei Ohtani hit 54 home runs.')
    assert check_status_consistency(g,out,'Shohei Ohtani hit 54.4 home runs.').status != ConsistencyStatus.CONSISTENT
    g,out = value_projection('AVG .281505')
    assert check_status_consistency(g,out,'AVG .282').status == ConsistencyStatus.CONSISTENT


def test_equivalent_units_optional_omission_and_bounded_repair():
    g,out = value_projection('The player was traded; OPS went from .750 to .900.')
    assert check_status_consistency(g,out,'The player was traded and his OPS changed from .750 to .900.').status == ConsistencyStatus.CONSISTENT
    assert check_status_consistency(g,out,'The player was traded and his OPS changed from .900 to .750.').status != ConsistencyStatus.CONSISTENT
    g,optional = value_projection('optional result',mandatory=False)
    assert check_status_consistency(g,optional,'omitted').status == ConsistencyStatus.CONSISTENT
    g,oversized = value_projection('x'*17000)
    c = check_status_consistency(g,oversized,'omitted')
    repaired = repair_answer(oversized,'omitted',c.retracted_proposition_ids)
    assert repaired == 'omitted'
    # Presentation failure no longer writes Judge truth. The lifecycle must fallback
    # or initiate a fresh upstream pass; an oversized expression is not evidence loss.
    before = oversized.model_dump()
    check_status_consistency(g,oversized,repaired)
    assert oversized.model_dump() == before
    assert oversized.goal_status == GoalStatus.COMPLETE


@pytest.mark.parametrize('negative', [False,True])
def test_runtime_replaces_retracted_prose_preserves_accepted_polarity(tmp_path,negative):
    from tests.test_phase5b_evidence_completion import _engine_without_live_sources, _StubInterpreter, _StubPlanner
    from app.contracts.models import AnswerProjection, AnalysisRequest, Claim, CoverageState, EntityMention
    from tests.governed_state_fixture import approved_snapshot
    engine = _engine_without_live_sources(tmp_path)
    engine.interpreter = _StubInterpreter(SemanticInterpretation(raw_query='result',
        entities=[EntityMention(mention='Shohei Ohtani',type_hint='player')],
        analysis_requests=[AnalysisRequest(text='result',objective='result')]))
    engine.planner = _StubPlanner(lambda kwargs:[])
    text = 'Shohei Ohtani did not pitch in 2024.' if negative else 'Shohei Ohtani hit 54 home runs.'
    def evaluate(goal,*args,**kwargs):
        for p in goal.propositions:
            p.coverage = CoverageState.SATISFIED
        return approved_snapshot(AnswerProjection(goal_id=goal.id,goal_status=GoalStatus.COMPLETE,
            propositions=goal.propositions,claims=[Claim(text=text,proposition_ids=[p.id for p in goal.propositions])]))
    engine.judge.evaluate = evaluate
    engine.composer.compose = lambda *args,**kwargs:text+' That assertion is fabricated.'
    result = engine.ask('bounded','result',trace_level='off')
    assert result.projection.goal_status == GoalStatus.COMPLETE
    assert result.consistency.method == 'deterministic_repair'
    assert result.projection.consistency is None
    assert text in result.answer and 'fabricated' not in result.answer
