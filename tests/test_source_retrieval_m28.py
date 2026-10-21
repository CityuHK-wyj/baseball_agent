"""Seeded retrieval metamorphisms; replay one with -k seed_17."""

import random

import pytest

from app.capabilities.web_search import FetchedPage, SearchHit, WebSearchConfig
from app.web.fetch_ladder import fetch_with_fallback
from app.web.source_intent import SourceIntent, infer_source_intent, source_type_for_hit


def _page(url, *, ok=False, code="NO_BODY", alternate=(), canonical="", method="http"):
    return FetchedPage(url=url, final_url=url, title="", text="body " * 60 if ok else "",
                       retrieved_at="2026-09-30T00:00:00Z", ok=ok, error_code="" if ok else code,
                       content_hash="hash", alternate_urls=tuple(alternate), canonical_url=canonical,
                       retrieval_method=method)


@pytest.mark.parametrize("seed", range(40), ids=lambda seed: f"seed_{seed}")
def test_seeded_fallback_and_access_boundary(seed, monkeypatch):
    rng = random.Random(seed)
    host = f"source{rng.randrange(100000)}.example.org"
    original = f"https://{host}/story/{rng.randrange(100000)}"
    alternate = f"https://{host}/print/{rng.randrange(100000)}"
    blocker = rng.choice(["BLOCKED", "AUTH_REQUIRED", "PAYWALLED", "RATE_LIMITED"])
    calls = []
    def fetch(url, _config):
        calls.append(url)
        return _page(url, ok=url == alternate, alternate=(alternate,) if url == original else ())
    result = fetch_with_fallback(original, WebSearchConfig(), fetch=fetch)
    assert result.ok and result.url == original and result.final_url == alternate
    assert result.retrieval_method == "alternate_html"
    assert len(result.fallback_steps) == 2 and calls == [original, alternate]
    calls.clear()
    result = fetch_with_fallback(original, WebSearchConfig(), fetch=lambda url, _: calls.append(url) or _page(url, code=blocker, alternate=(alternate,)))
    assert not result.ok and result.error_code == blocker and calls == [original]
    assert result.fallback_steps == (f"http:{blocker}",)
    # Changing the alternate host alone invalidates automatic same-source fallback.
    foreign = alternate.replace(host, "unrelated.example.net")
    calls.clear()
    result = fetch_with_fallback(original, WebSearchConfig(), fetch=lambda url, _: calls.append(url) or _page(url, alternate=(foreign,)))
    assert not result.ok and calls == [original]


def test_public_js_rendering_path_is_explicit_and_bounded(monkeypatch):
    url = "https://public.example.org/story"
    monkeypatch.setattr("app.web.fetch_ladder.render_public_page", lambda candidate, _: _page(candidate, ok=True, method="browser"))
    result = fetch_with_fallback(url, WebSearchConfig(), browser_enabled=True,
                                 fetch=lambda candidate, _: _page(candidate, code="JS_RENDER_REQUIRED"))
    assert result.ok and result.retrieval_method == "browser"
    assert result.fallback_steps == ("http:JS_RENDER_REQUIRED", "browser:SUCCESS")


def test_source_intent_is_a_preference_and_original_language_is_retained(monkeypatch):
    assert infer_source_intent("日本媒体の interview transcript") == SourceIntent.INTERVIEW
    assert infer_source_intent("中文球迷社区如何讨论？") == SourceIntent.COMMUNITY_DISCUSSION
    assert infer_source_intent("historical background") == SourceIntent.HISTORICAL_REFERENCE
    assert infer_source_intent("日本のスポーツメディアはどう報じましたか") == SourceIntent.LOCAL_REPORTING
    assert source_type_for_hit("broad_search", SourceIntent.COMMUNITY_DISCUSSION) == "DISCOVERED_WEB"
    from app.capabilities.web_research import WebResearchTool
    from app.config import WebConfig
    from app.contracts.models import ToolRequest
    tool = WebResearchTool(WebConfig(broad_search=False), llm=None)
    request = ToolRequest(id="r", need_id="n", capability="web_research", objective="日本媒体如何评价这次采访？",
                          inputs={"language": "zh", "user_wording": "日本媒体如何评价这次采访？"})
    tool._source_intent = SourceIntent.LOCAL_REPORTING
    plan = tool._build_query_plan(request, request.objective, "zh", ["選手"], request.inputs["user_wording"])
    assert plan.research_languages[0] == "ja"
    assert plan.variants[0].language == "ja"
    spanish = ToolRequest(id="s", need_id="n", capability="web_research",
                          objective="¿Qué informó la prensa local sobre el partido?",
                          inputs={"language": "es", "user_wording": "¿Qué informó la prensa local sobre el partido?"})
    tool._source_intent = SourceIntent.LOCAL_REPORTING
    spanish_plan = tool._build_query_plan(spanish, spanish.objective, "es", ["Jugador"], spanish.inputs["user_wording"])
    assert spanish_plan.research_languages[0] == "es"
    assert spanish_plan.variants[0].language == "es"


def test_duplicate_representations_do_not_create_two_documents(monkeypatch):
    from app.capabilities.web_research import WebResearchTool
    from app.config import WebConfig
    tool = WebResearchTool(WebConfig(broad_search=False), llm=None)
    body = "Public report about a player and an interview. " * 8
    def fetched(url, _config):
        return FetchedPage(url, url, "Report", body, "2026-09-30T00:00:00Z", True,
                           content_hash="same", canonical_url="https://example.org/canonical")
    monkeypatch.setattr("app.capabilities.web_research.fetch_page", fetched)
    hits = [SearchHit("Report", "https://example.org/article", engine="broad_search"),
            SearchHit("Report", "https://example.org/print", engine="broad_search")]
    _, _, fetched_count, _, documents = tool._retrieve(hits, "interview", [], set(), 2)
    assert fetched_count == 2 and len(documents) == 1
    assert tool._fetch_attempts[-1]["outcome"] == "DUPLICATE_REPRESENTATION"


def test_authenticated_provider_requires_key_and_never_serializes_it(monkeypatch):
    from app.web.sources.tavily import TavilySearchBackend, extract_representation
    backend = TavilySearchBackend("", WebSearchConfig())
    assert backend.search("public interview") == []
    assert "AUTH_REQUIRED" in backend.last_error
    missing = extract_representation("https://example.org/story", "", WebSearchConfig())
    assert missing.error_code == "AUTH_REQUIRED"
    secret = "test-secret-value"
    class Response:
        status_code = 200
        def json(self):
            return {"results": [{"title": "Interview", "url": "https://example.org/story", "content": "snippet only"}]}
    import httpx
    monkeypatch.setattr('app.web.sources.tavily.request', lambda *args, **kwargs: Response())
    backend = TavilySearchBackend(secret, WebSearchConfig())
    hits = backend.search("interview")
    assert len(hits) == 1 and hits[0].snippet == "snippet only"
    assert secret not in repr(hits) + backend.last_error


@pytest.mark.parametrize("seed", range(40, 60), ids=lambda seed: f"redirect_seed_{seed}")
def test_seeded_http_redirect_and_typed_status(seed, monkeypatch):
    from app.capabilities.web_search import fetch_page
    import httpx
    rng = random.Random(seed)
    original = f"https://public.example.org/start/{rng.randrange(100000)}"
    final = f"https://public.example.org/article/{rng.randrange(100000)}"
    code = rng.choice([200, 401, 402, 403, 429])
    body = b"<html><title>Public report</title><body>" + b"The source states a public fact. " * 12 + b"</body></html>"
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if str(request.url) == original:
            return httpx.Response(302, headers={"location": final}, content=b"")
        return httpx.Response(code, headers={"content-type": "text/html"}, content=body)

    real_client = httpx.Client
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: real_client(*a, transport=transport, **k))
    monkeypatch.setattr(
        "app.capabilities.web_search._pinned_target",
        lambda url: (url, "public.example.org", {"Host": "public.example.org"}, {}),
    )
    page = fetch_page(original, WebSearchConfig())
    assert calls == [original, final] and page.url == original and page.final_url == final
    assert page.status_code == code
    assert page.error_code == {200: "", 401: "AUTH_REQUIRED", 402: "PAYWALLED",
                               403: "BLOCKED", 429: "RATE_LIMITED"}[code]
    assert page.ok is (code == 200)


def test_feed_item_is_labeled_representation_of_exact_article(monkeypatch):
    from app.web.fetch_ladder import _feed_item
    import httpx
    article = "https://public.example.org/article/alpha"
    feed = "https://public.example.org/feed.xml"
    xml = ("<rss><channel><item><title>Alpha</title><link>" + article + "</link>"
           "<description>" + "Public report text about a direct interview. " * 8 + "</description>"
           "</item><item><title>Other</title><link>https://public.example.org/other</link>"
           "<description>Unrelated report.</description></item></channel></rss>").encode()
    class Response:
        status_code = 200
        extensions = {}
        content = xml
        def raise_for_status(self):
            pass
    monkeypatch.setattr("app.web.fetch_ladder.assert_safe_url", lambda url: url)
    monkeypatch.setattr('app.web.transport.request', lambda *args, **kwargs: Response())
    page = _feed_item(feed, article, WebSearchConfig())
    assert page.ok and page.url == article and page.final_url == feed
    assert page.retrieval_method == "rss_item" and page.canonical_url == article
    assert "Unrelated" not in page.text


def test_tavily_extract_is_labeled_api_body_not_search_snippet(monkeypatch):
    from app.web.sources.tavily import extract_representation
    import httpx
    url = "https://public.example.org/article"
    body = "Original language interview answer. " * 10
    class Response:
        status_code = 200
        def json(self):
            return {"results": [{"url": url, "raw_content": body}]}
    monkeypatch.setattr("app.web.sources.tavily.assert_safe_url", lambda value: value)
    monkeypatch.setattr('app.web.sources.tavily.request', lambda *args, **kwargs: Response())
    page = extract_representation(url, "test-key", WebSearchConfig())
    assert page.ok and page.text == body
    assert page.retrieval_method == "tavily_extract" and page.canonical_url == url


def test_interview_source_preference_does_not_promote_reference_page():
    from app.capabilities.web_research import _apply_source_plan
    from app.web.ranking import CandidateContext, rank_candidates
    from app.contracts.models import AnswerProposition, PropositionKind
    proposition = AnswerProposition(statement="interview in 2024", kind=PropositionKind.EXPLANATION,
                                    required_information="interview", time_scope={"season": 2024})
    interview = SearchHit("Official interview transcript", "https://public.example.org/interview", engine="publisher_feeds")
    reference = SearchHit("Interview background", "https://reference.example.org/page", engine="wikipedia")
    context = CandidateContext(source_intent="INTERVIEW", intent="INTERVIEW")
    ranked = rank_candidates([reference, interview], context)
    assert ranked[0] is interview
    assert _apply_source_plan(ranked, [proposition], proposition.statement, SourceIntent.INTERVIEW)[0] is interview


def test_reference_hit_does_not_close_community_discovery_gap():
    from app.capabilities.web_research import WebResearchTool
    from app.config import WebConfig
    tool = WebResearchTool(WebConfig(broad_search=True), llm=None)
    tool._source_intent = SourceIntent.COMMUNITY_DISCUSSION
    hit = SearchHit("Fan discussion background", "https://reference.example.org/page", engine="wikipedia")
    assert tool._needs_broad_search([hit], {"fan"}, {"discussion"})


def test_fallback_extractor_version_and_offsets_are_preserved(monkeypatch):
    from app.capabilities.web_research import WebResearchTool
    from app.config import WebConfig
    tool = WebResearchTool(WebConfig(broad_search=False), llm=None)
    body = "The interview was published as a public feed entry. " * 8
    def fallback(url, _config, **_kwargs):
        return FetchedPage(url, "https://public.example.org/feed.xml", "Interview", body,
                           "2026-09-30T00:00:00Z", True, content_hash="rawhash",
                           retrieval_method="rss_item", canonical_url=url,
                           fallback_steps=("http:NO_BODY", "rss_item:SUCCESS"))
    monkeypatch.setattr("app.capabilities.web_research.fetch_with_fallback", fallback)
    findings, _, _, _, documents = tool._retrieve(
        [SearchHit("Interview", "https://public.example.org/article", engine="publisher_feeds")],
        "interview", [], set(), 1,
    )
    doc = documents[0]
    assert doc["extractor_version"] == "rss-item-v1"
    assert doc["initial_url"] == "https://public.example.org/article"
    assert doc["final_url"] == "https://public.example.org/feed.xml"
    assert doc["body_hash"] and doc["retrieval_method"] == "rss_item"
    assert all(doc["body_text"][span["start"]:span["end"]] == span["text"]
               for span in findings[0]["evidence_spans"])


def test_discovery_aggregator_body_never_becomes_article_evidence(monkeypatch):
    from app.capabilities.web_research import WebResearchTool
    from app.config import WebConfig
    tool = WebResearchTool(WebConfig(broad_search=False), llm=None)
    url = "https://news.example.org/item"
    monkeypatch.setattr("app.capabilities.web_research.fetch_with_fallback",
                        lambda candidate, _config, **_kwargs: _page(candidate, ok=True))
    findings, discovery, _, _, documents = tool._retrieve(
        [SearchHit("News listing", url, engine="google_news")], "news", [], set(), 1,
    )
    assert not findings and discovery
    assert documents[0]["retrieval_status"] == "DISCOVERY_ONLY"
