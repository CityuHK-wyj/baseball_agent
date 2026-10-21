"""Exact independent revalidation probes; credentials are synthetic fixtures."""
from types import SimpleNamespace
import json
import pytest
from tests.test_phase5b_final_repair import chain, value_projection
from tests.assessment_fixtures import FixedAssessor
from app.contracts.models import *
from app.verification.evidence import verify_span
from app.verification.consistency import check_status_consistency, repair_answer
from app.observe.diagnostics import DiagnosticRecorder


def test_scope_borrowing_exact():
    p, d, s, _ = chain('In the 2024 postseason, play began. Shohei Ohtani hit 25 home runs.')
    p.population = 'POSTSEASON'
    assert verify_span(p, s, d, allow_llm=False).status != VerificationStatus.SUPPORTED


@pytest.mark.parametrize('value,ok', [('30', False), ('25', True)])
def test_semantic_value_owner_exact(value, ok):
    p, d, s, _ = chain('In 2024 Shohei Ohtani hit 25 home runs, while Aaron Judge hit 30 home runs.')
    p.kind = PropositionKind.VALUE
    fact = dict(subject='Shohei Ohtani', metric='home runs', value=value, scope='2024')
    llm = FixedAssessor({'status': 'SUPPORTED', 'fact': fact})
    assert (verify_span(p, s, d, llm=llm).status == VerificationStatus.SUPPORTED) == ok


@pytest.mark.parametrize('prefix,ok', [('The verified result is false: ', False), ('', True), ('It is not true that ', False)])
def test_retracted_exact_claim(prefix, ok):
    g, out = value_projection('Shohei Ohtani hit 54 home runs.')
    assert (check_status_consistency(g, out, prefix + out.claims[0].text).status == ConsistencyStatus.CONSISTENT) == ok


def test_empty_repair_header_exact():
    g, out = value_projection('Shohei Ohtani hit 54 home runs.')
    answer = 'Result omitted.\n\n[已验证结果 / Verified results]'
    check = check_status_consistency(g, out, answer)
    repaired = repair_answer(out, answer, check.retracted_proposition_ids)
    assert check_status_consistency(g, out, repaired).status == ConsistencyStatus.CONSISTENT
    assert repair_answer(out, repaired, check.retracted_proposition_ids) == repaired


@pytest.mark.parametrize('body', ['https://x.test/?client_secret=UNIQUESECRET123',
    'https://x.test/?client%5Fsecret=UNIQUESECRET123',
    'https%3A%2F%2Fx.test%2F%3Fclient%255Fsecret%3DUNIQUESECRET123',
    'Authorization: Bearer UNIQUESECRET123', 'x-api-key: UNIQUESECRET123',
    '{"client_secret": "UNIQUESECRET123"}'])
@pytest.mark.parametrize('research', [False, True])
def test_credentials_before_persistence(tmp_path, body, research):
    rec = DiagnosticRecorder(tmp_path / 'diag.json', research_content=research)
    rec.record('test', documents=[{'body_text': body, 'metadata': {'client%5Fsecret':'UNIQUESECRET123'}}],
               error=RuntimeError(body), input_tokens=12, span_id='span_test')
    rec.write()
    raw = rec.path.read_text()
    assert 'UNIQUESECRET123' not in raw
    assert json.loads(raw)[0]['data']['input_tokens'] == 12
    assert json.loads(raw)[0]['data']['span_id'] == 'span_test'


def test_write_boundary_sanitizes_mutated_records(tmp_path):
    rec = DiagnosticRecorder(tmp_path / 'diag.json', research_content=True)
    rec.record('test', status='OK')
    rec.records[0]['data']['documents'] = [{'body_text':'https://x.test/?client_secret=UNIQUESECRET123'}]
    rec.write()
    assert 'UNIQUESECRET123' not in rec.path.read_text()


def test_scope_borrowing_cannot_be_upgraded_by_llm():
    p, d, s, _ = chain('In the 2024 postseason, play began. Shohei Ohtani hit 25 home runs.')
    p.population = 'POSTSEASON'
    class LLM:
        configured = True
        def complete_json(self, **kw):
            pytest.fail('known named clause has no requested scope')
    assert verify_span(p, s, d, llm=LLM()).status != VerificationStatus.SUPPORTED


def test_unrelated_limitation_preserves_expression():
    g, out = value_projection('Shohei Ohtani hit 54 home runs.')
    answer = 'No evidence for the injury claim. Shohei Ohtani hit 54 home runs.'
    check = check_status_consistency(g, out, answer)
    assert check_status_consistency(g, out, repair_answer(out, answer, check.retracted_proposition_ids)).status == ConsistencyStatus.CONSISTENT


def test_repair_output_bound():
    g, out = value_projection('x' * 17000)
    assert repair_answer(out, 'omitted', ['p']) == 'omitted'


def test_write_boundary_envelope_and_encoded_nested_key(tmp_path):
    rec = DiagnosticRecorder(tmp_path / 'diag.json', research_content=True)
    rec.record('test', documents=[{'metadata': {'client%255Fsecret':'UNIQUESECRET123'}}])
    rec.records[0]['extra'] = {'authorization': 'UNIQUESECRET123'}
    rec.write()
    assert 'UNIQUESECRET123' not in rec.path.read_text()
