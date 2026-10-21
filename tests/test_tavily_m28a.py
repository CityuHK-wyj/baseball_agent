"""Seeded Tavily transport tests; no real credential or network call is used."""

import hashlib
import random

import pytest

from app.capabilities.web_search import FetchedPage, SearchHit, WebSearchConfig
from app.config import WebConfig
from app.web.fetch_ladder import fetch_with_fallback
from app.web.outcomes import SourceOutcome, classify_search_result
from app.web.source_intent import SourceIntent, source_type_for_hit
from app.web.sources.tavily import TavilySearchBackend, extract_representation


@pytest.mark.parametrize("seed", range(40), ids=lambda seed: f"search_seed_{seed}")
def test_seeded_search_keeps_snippet_as_discovery_and_forwards_language(seed, monkeypatch):
    rng = random.Random(seed)
    language = rng.choice(["en", "es", "ja", "ko", "zh"])
    host = f"publisher{rng.randrange(100000)}.example.org"
    source_url = f"https://{host}/article/{rng.randrange(100000)}"
    secret = "fake-secret-never-log"
    requests = []

    class Response:
        status_code = 200

        def json(self):
            return {"results": [{"title": "Direct report", "url": source_url,
                                 "content": "Discovery snippet only", "raw_content": "must be ignored"}],
                    "usage": {"credits": 1}}

    def post(_url, **kwargs):
        requests.append(kwargs["json"])
        return Response()

    monkeypatch.setattr('app.web.sources.tavily.request', lambda method,url,**kwargs: post(url,**kwargs))
    backend = TavilySearchBackend(secret, WebSearchConfig())
    hits = backend.search(f"report {language}", language=language)
    assert len(hits) == 1 and hits[0].url == source_url
    assert hits[0].snippet == "Discovery snippet only"
    assert hits[0].engine == "tavily"
    assert source_type_for_hit("tavily", SourceIntent.COMMUNITY_DISCUSSION) == "DISCOVERED_WEB"
    assert requests[0]["language"] == language
    assert requests[0]["include_raw_content"] is False
    assert requests[0]["include_usage"] is True
    assert backend.last_usage_credits == 1
    assert secret not in repr(hits) + repr(backend) + backend.last_error


@pytest.mark.parametrize("seed", range(40, 80), ids=lambda seed: f"extract_seed_{seed}")
def test_seeded_extract_labels_representation_and_typed_failures(seed, monkeypatch):
    rng = random.Random(seed)
    url = f"https://source{rng.randrange(100000)}.example.org/story/{rng.randrange(100000)}"
    kind = rng.choice(["success", "unsupported", "inaccessible", "empty", "quota", "rate", "auth"])
    body = "Informe original en español sobre el partido. " * 10

    class Response:
        status_code = {"quota": 402, "rate": 429, "auth": 401}.get(kind, 200)

        def json(self):
            if kind == "success":
                return {"results": [{"url": url, "raw_content": body}], "usage": {"credits": 0.2}}
            error = {"unsupported": "Unsupported URL", "inaccessible": "Access denied"}.get(kind, "Empty page")
            return {"results": [], "failed_results": [{"url": url, "error": error}], "usage": {"credits": 0}}

    monkeypatch.setattr("app.web.sources.tavily.assert_safe_url", lambda candidate: candidate)
    monkeypatch.setattr('app.web.sources.tavily.request', lambda *_args, **_kwargs: Response())
    page = extract_representation(url, "fake-secret", WebSearchConfig())
    assert page.url == url and page.retrieval_method == "tavily_extract"
    if kind == "success":
        assert page.ok and page.text == body
        assert page.content_hash == hashlib.sha256(body.encode()).hexdigest()
        assert page.provider_credits == 0.2
    else:
        expected = {"unsupported": "UNSUPPORTED_URL", "inaccessible": "SOURCE_INACCESSIBLE",
                    "empty": "NO_BODY", "quota": "QUOTA_EXCEEDED", "rate": "RATE_LIMITED",
                    "auth": "AUTH_REQUIRED"}[kind]
        assert not page.ok and page.error_code == expected and not page.text


def test_tavily_is_escalation_and_config_repr_is_secret_free():
    from app.capabilities.web_research import WebResearchTool
    from app.contracts.models import ToolRequest

    secret = "fake-secret-never-log"
    tool = WebResearchTool(WebConfig(tavily_api_key=secret, broad_search=True), llm=None)
    assert "tavily" not in [backend.name for backend in tool.backends]
    assert "tavily" in [backend.name for backend in tool.broad_backends]
    assert secret not in repr(tool.config)
    request = ToolRequest(id="test", need_id="need", capability="web_research", objective="public report")
    artifact = tool._artifact(request, "public report", [], [], [], 0, [], [],
                              documents=[], evidence_findings=[], coverage={},
                              retrieval_code="NO_RESULTS", propositions=[])
    assert secret not in artifact.model_dump_json()
    assert SourceOutcome.QUOTA_EXCEEDED == classify_search_result(error="HTTP 402")
    keyless = WebResearchTool(WebConfig(tavily_api_key="", broad_search=True), llm=None)
    assert "tavily" not in [backend.name for backend in keyless.broad_backends]


def test_extract_fallback_only_after_recoverable_failure_and_retains_outcome():
    url = "https://source.example.org/story"
    now = "2026-09-30T00:00:00Z"
    calls = []

    def fetch(_url, _config):
        return FetchedPage(url, url, "", "", now, False, error_code="NO_BODY")

    def extract(_url, _config):
        calls.append("extract")
        return FetchedPage(url, url, "", "", now, False, error_code="UNSUPPORTED_URL",
                           retrieval_method="tavily_extract", provider_credits=0.2)

    result = fetch_with_fallback(url, WebSearchConfig(), fetch=fetch, api_extract=extract)
    assert calls == ["extract"] and result.error_code == "UNSUPPORTED_URL"
    assert result.fallback_steps == ("http:NO_BODY", "tavily_extract:UNSUPPORTED_URL")
    assert result.provider_credits == 0.2
    blocked = fetch_with_fallback(url, WebSearchConfig(),
                                  fetch=lambda *_: FetchedPage(url, url, "", "", now, False, error_code="BLOCKED"),
                                  api_extract=extract)
    assert blocked.error_code == "BLOCKED" and calls == ["extract"]


def test_tavily_body_provenance_and_cross_provider_dedup(monkeypatch):
    from app.capabilities.web_research import WebResearchTool

    url = "https://publisher.example.org/story"
    body = "Public report from the underlying publisher about an interview. " * 8
    tool = WebResearchTool(WebConfig(tavily_api_key="fake-secret", broad_search=True), llm=None)
    monkeypatch.setattr("app.capabilities.web_research.fetch_with_fallback",
                        lambda candidate, _config, **_kwargs: FetchedPage(
                            candidate, url, "Report", body, "2026-09-30T00:00:00Z", True,
                            retrieval_method="tavily_extract", content_hash=hashlib.sha256(body.encode()).hexdigest(),
                            canonical_url=url, provider_credits=0.2,
                            fallback_steps=("http:NO_BODY", "tavily_extract:SUCCESS")))
    _, _, _, _, documents = tool._retrieve(
        [SearchHit("Report", url, engine="publisher_feeds"), SearchHit("Report", url + "?ref=tavily", engine="tavily")],
        "interview", [], set(), 2,
    )
    assert len(documents) == 1
    doc = documents[0]
    assert doc["url"] == url and doc["final_url"] == url and doc["canonical_url"] == url
    assert doc["representation_provider"] == "tavily" and doc["retrieval_method"] == "tavily_extract"
    assert doc["body_hash"] == hashlib.sha256(body.encode()).hexdigest()
    assert doc["extractor_version"] == "tavily-extract-v1"
    assert tool._fetch_attempts[-1]["outcome"] == "DUPLICATE_REPRESENTATION"
    assert all(attempt["credential_dependency"] == "TAVILY_API_KEY" for attempt in tool._fetch_attempts)
