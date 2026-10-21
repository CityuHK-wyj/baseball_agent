"""Post-audit provider-retry accounting regression suite.

The audit observed 2 application attempts producing 6 actual HTTP attempts because the
provider SDK retried beneath the application accounting layer. The repair makes the
application own retry policy (SDK `max_retries=0` by default) and records both logical
application attempts and actual transport attempts explicitly.
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.config import LLMConfig
from app.llm.client import LLMClient, LLMError


def _ok_response(model="fixture-model"):
    return SimpleNamespace(model=model, usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
                           choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))])


def test_provider_sdk_is_constructed_without_hidden_retries(monkeypatch):
    import openai

    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    client = LLMClient(LLMConfig(api_key="fixture", provider_max_retries=0))
    client._get_client()
    assert captured["max_retries"] == 0


def test_failed_provider_call_consumes_application_budget():
    client = LLMClient(LLMConfig(api_key="fixture", provider_max_retries=0))

    def create(**kwargs):
        raise RuntimeError("fixture network failure")

    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with pytest.raises(LLMError):
        client.complete_json(role="semantic", system="s", user="u")
    assert client.application_attempts == 2
    assert client.provider_attempts == 2
    assert client.transport_attempts == 0  # mocked SDK: no HTTP transport used
    assert len(client.usage_records) == 2
    assert all(record["usage_available"] is False for record in client.usage_records)
    assert all(record["application_attempt"] >= 1 for record in client.usage_records)


def test_timeout_consumes_application_budget():
    client = LLMClient(LLMConfig(api_key="fixture", provider_max_retries=0))

    def create(**kwargs):
        raise TimeoutError("fixture timeout")

    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with pytest.raises(LLMError):
        client.complete_json(role="planner", system="s", user="u")
    assert client.application_attempts == 2
    assert client.provider_attempts == 2
    assert client.transport_attempts == 0  # mocked SDK is not an HTTP attempt


def test_malformed_response_consumes_application_budget():
    client = LLMClient(LLMConfig(api_key="fixture", provider_max_retries=0))
    response = SimpleNamespace(model="m", usage=None,
                               choices=[SimpleNamespace(message=SimpleNamespace(content="not json"))])
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: response)))
    with pytest.raises(LLMError):
        client.complete_json(role="analytics", system="s", user="u")
    assert client.application_attempts == 2
    assert client.provider_attempts == 2
    assert client.transport_attempts == 0
    assert len(client.usage_records) == 2


def _openai_client_with_503_counter(monkeypatch, max_retries):
    import openai

    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(503, json={"error": {"message": "unavailable", "type": "server_error"}},
                              headers={"content-type": "application/json"})

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    client = LLMClient(LLMConfig(api_key="fixture", provider_max_retries=max_retries))
    client._client = openai.OpenAI(
        api_key="fixture", base_url="http://provider.test/v1", timeout=5.0,
        max_retries=max_retries, http_client=http_client,
    )
    return client, calls


def test_http_attempts_match_application_attempts_when_retries_disabled(monkeypatch):
    client, calls = _openai_client_with_503_counter(monkeypatch, 0)
    with pytest.raises(LLMError):
        client.complete_json(role="semantic", system="s", user="u")
    # Exactly one real HTTP attempt per logical application attempt; no hidden SDK loop.
    assert len(calls) == 2
    assert client.application_attempts == 2
    assert client.transport_attempts == 2
    assert len(client.usage_records) == 2


def test_zero_transport_report_after_network_attempts_is_impossible(monkeypatch):
    client, calls = _openai_client_with_503_counter(monkeypatch, 0)
    with pytest.raises(LLMError):
        client.complete_json(role="semantic", system="s", user="u")
    assert calls, "network attempts were made"
    assert client.transport_attempts > 0
    assert all(record["transport_attempts"] > 0 for record in client.usage_records)
    assert all(record["provider_max_retries"] == 0 for record in client.usage_records)


def test_explicit_provider_retries_are_bounded_and_recorded(monkeypatch):
    client, calls = _openai_client_with_503_counter(monkeypatch, 1)
    with pytest.raises(LLMError):
        client.complete_json(role="semantic", system="s", user="u")
    # 2 application attempts x (1 + 1 provider retry) = 4 bounded HTTP attempts.
    assert len(calls) == 4
    assert client.application_attempts == 2
    assert client.provider_attempts == 2
    assert client.transport_attempts == 4  # actual HTTP attempts, including SDK retries
    assert client.provider_retry_attempts == 2
    assert all(record["provider_max_retries"] == 1 for record in client.usage_records)
