"""Independent freeze-review counterexamples, promoted BEFORE mechanism changes.

These tests deliberately do not infer grounding from Python dependency occurrence.
Original evidence remains unchanged in .scratch/fresh-freeze-review/.
"""
import gzip
import socket
from types import SimpleNamespace

import httpx
import pytest

from tests.test_post_audit_derived_dependency import _goal, _project, _run
from app.contracts.models import AnswerProjection, AnswerProposition, RequirementAssessment
from app.verification.governed_state import (
    GovernedStateError, governed_limited_fallback, validate_governed_projection,
)


@pytest.mark.parametrize('expression', [
    '(a-a)-(b-b-73129)', '(a+319)-(b-67)', 'b-a', 'a-a', 'a+b',
])
def test_audit_python_cannot_mint_derived_authority(expression):
    artifact, roots = _run('a=inputs["n2023"][0]["home_runs"]\nb=inputs["n2024"][0]["home_runs"]\noutput=[{"difference":'+expression+'}]')
    projection = _project(_goal('Calculate 2023 home runs minus 2024 home runs.'), artifact, roots)
    assert not projection.claims


def test_audit_fabricated_numeric_sibling_cannot_mint_authority():
    artifact, roots = _run('a=inputs["n2023"][0]["home_runs"]\nb=inputs["n2024"][0]["home_runs"]\noutput=[{"difference":a-b,"career_titles":a*0+73129}]')
    assert not _project(_goal('Calculate the difference in home runs.'), artifact, roots).claims


def test_complete_with_partial_children_is_invalid():
    projection = AnswerProjection(goal_id='g', goal_status='COMPLETE', propositions=[
        AnswerProposition(id='p', goal_id='g', requirement_id='r', kind='FACT', statement='fact', coverage='PARTIAL')])
    with pytest.raises(GovernedStateError):
        validate_governed_projection(projection)


def test_fallback_reaggregates_requirement_coverage():
    projection = AnswerProjection(goal_id='g', goal_status='COMPLETE', propositions=[
        AnswerProposition(id='p', goal_id='g', requirement_id='r', kind='FACT', statement='fact', coverage='SATISFIED')],
        requirement_assessments=[RequirementAssessment(requirement_id='r', status='SATISFIED')])
    fallback = governed_limited_fallback(projection)
    assert fallback.requirement_assessments[0].status != 'SATISFIED'


def test_rss_uses_raw_stream_not_httpx_preload(monkeypatch):
    from app.web.fetch_ladder import _feed_item
    from app.capabilities.web_search import WebSearchConfig
    raw = gzip.compress(b'<rss>'+b'x'*4_000_000+b'</rss>')
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield raw
    original = httpx.Client
    def factory(*args, **kwargs):
        kwargs['transport'] = httpx.MockTransport(lambda req: httpx.Response(
            200, headers={'content-encoding':'gzip'}, stream=Stream()))
        return original(*args, **kwargs)
    monkeypatch.setattr(socket, 'getaddrinfo', lambda host, port, *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', port))])
    monkeypatch.setattr(httpx, 'Client', factory)
    def forbidden(*args, **kwargs):
        pytest.fail('unbounded preloading acquisition')
    monkeypatch.setattr(httpx, 'get', forbidden)
    page = _feed_item('http://owned.example/feed.xml', 'http://owned.example/article', WebSearchConfig(max_bytes=12289))
    assert len(page.raw_content or b'') <= 12289


def test_actual_sdk_http_attempts_not_sdk_calls(monkeypatch):
    from app.config import LLMConfig
    from app.llm.client import LLMClient, LLMError
    import openai
    calls = []
    original = httpx.Client
    def handler(req):
        calls.append(req)
        return httpx.Response(500, json={'error':{'message':'owned failure'}}, headers={'retry-after':'0'})
    def factory(*args, **kwargs):
        kwargs['transport'] = httpx.MockTransport(handler)
        return original(*args, **kwargs)
    client = LLMClient(LLMConfig(api_key='fixture', base_url='http://owned.example/v1', provider_max_retries=1))
    client._client = openai.OpenAI(api_key='fixture', base_url='http://owned.example/v1',
        max_retries=1, http_client=original(transport=httpx.MockTransport(handler)))
    with pytest.raises(LLMError):
        client.complete_json(role='semantic', system='s', user='u')
    assert len(calls) == 4
    assert client.transport_attempts == len(calls)
