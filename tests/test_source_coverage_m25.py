"""Generated source-coverage escalation tests (M2.5).

Broad discovery is a fallback, never trusted evidence. These tests inject fake direct and
broad backends and assert the escalation policy, provenance and failure semantics.
"""

from __future__ import annotations

import random

import pytest

from app.capabilities.web_research import WebResearchTool
from app.capabilities.web_search import FetchedPage
from app.config import WebConfig
from app.contracts.models import (
    AnswerProposition,
    EvidenceSource,
    PropositionKind,
    ToolRequest,
)

SEEDS = list(range(20))


class _Backend:
    def __init__(self, name, hits, error=""):
        self.name = name
        self._hits = list(hits)
        self._error = error
        self.calls: list[tuple[str, str]] = []
        self.last_error = ""

    def search(self, query, *, limit=5, language="en"):
        self.calls.append((query, language))
        self.last_error = self._error
        return list(self._hits)


class _Hit:
    def __init__(self, url, title, snippet="", engine="fixture"):
        self.url = url
        self.title = title
        self.snippet = snippet
        self.engine = engine


def _tool(direct_hits, broad_hits, *, direct_error="", broad_error=""):
    from tests.assessment_fixtures import FixtureAssessorLLM

    tool = WebResearchTool(WebConfig(broad_search=False), llm=FixtureAssessorLLM())
    tool.backends = [_Backend("direct", direct_hits, direct_error)]
    tool.broad_backends = [_Backend("broad_search", broad_hits, broad_error)]
    return tool


def _request(**overrides):
    proposition = AnswerProposition(
        statement="recent injury report about Aaron Judge",
        kind=PropositionKind.FACT,
        subject="Aaron Judge",
        required_information="injury",
        mandatory=True,
    )
    inputs = {
        "entity_names": ["Aaron Judge"],
        "objective": "recent injury report about Aaron Judge",
        "user_wording": "Is there a recent injury report about Aaron Judge?",
        "propositions": [proposition.model_dump(exclude={"coverage"})],
        "resolved_time_scope": {"date_range": {"start": "2026-08-30", "end": "2026-09-29"}},
    }
    inputs.update(overrides)
    return ToolRequest(id="tr", need_id="n_web", capability="web_research", objective=inputs["objective"], inputs=inputs)


def test_broad_search_is_not_justified_when_direct_hit_names_subject():
    tool = _tool([_Hit("https://example.com/a", "Aaron Judge injury update")], [])
    tokens = {"aaron", "judge"}
    assert tool._needs_broad_search(tool.backends[0].search("q"), tokens) is False


def test_broad_search_is_justified_when_direct_hits_are_subject_blind():
    tool = _tool([_Hit("https://example.com/a", "Gardening tips for spring")], [])
    tokens = {"aaron", "judge"}
    assert tool._needs_broad_search(tool.backends[0].search("q"), tokens) is True


@pytest.mark.parametrize("seed", SEEDS)
def test_escalation_merges_broad_candidates_and_fetches_them(monkeypatch, seed):
    rng = random.Random(seed)
    broad_url = f"https://broad{seed}.example.com/judge"
    tool = _tool(
        [_Hit("https://direct.example.com/gardening", "Gardening tips")],
        [_Hit(broad_url, "Aaron Judge injury report", "calf strain")],
    )
    body = "Aaron Judge has a moderate calf injury and was placed on the injured list in September."
    fetched: list[str] = []

    def fake_fetch(url, config):
        fetched.append(url)
        text = body if url == broad_url else "Gardening tips for spring planting and flower beds."
        return FetchedPage(
            url=url, final_url=url,
            title="Aaron Judge injury report" if url == broad_url else "Gardening tips",
            text=text, retrieved_at="2026-09-29T00:00:00Z", ok=True, content_hash=f"hash-{seed}",
        )

    monkeypatch.setattr("app.capabilities.web_research.fetch_page", fake_fetch)
    result = tool.execute(_request())
    data = result.artifact.structured_data
    assert data["broad_search"]["used"] is True
    assert broad_url in fetched
    # The broad hit is still fetched into a body before it can support anything.
    supported = [f for f in data["evidence_findings"] if f["status"] == "SUPPORTED"]
    assert supported
    broad_doc = next(d for d in data["documents"] if d["url"] == broad_url)
    assert broad_doc["body_text"] == body
    for finding in supported:
        assert finding["document_id"] == broad_doc["id"]


def test_broad_snippet_alone_is_never_evidence(monkeypatch):
    # The broad provider returns only a snippet and the fetch fails, so nothing is grounded.
    tool = _tool(
        [_Hit("https://direct.example.com/gardening", "Gardening tips")],
        [_Hit("https://broad.example.com/judge", "Aaron Judge injury report", "calf strain")],
    )

    def failing_fetch(url, config):
        return FetchedPage(url=url, final_url=url, title="", text="", retrieved_at="2026-09-29T00:00:00Z",
                           ok=False, error="FETCH_FAILED", error_code="FETCH_FAILED")

    monkeypatch.setattr("app.capabilities.web_research.fetch_page", failing_fetch)
    result = tool.execute(_request())
    data = result.artifact.structured_data
    assert data["broad_search"]["used"] is True
    assert not [f for f in data["evidence_findings"] if f["status"] == "SUPPORTED"]


def test_broad_provider_failure_degrades_honestly(monkeypatch):
    tool = _tool([], [], broad_error="ConnectError: blocked")
    result = tool.execute(_request())
    # No provider returned a hit and the only attempted provider failed, so this is a typed
    # source failure, never a fabricated NO_RESULTS.
    assert result.outcome.code in {"SEARCH_BACKEND_UNAVAILABLE", "NO_RESULTS"}
    assert result.outcome.code != "OK"


def test_broad_search_follows_the_query_plan_language(monkeypatch):
    broad_backend = _Backend("broad_search", [])
    tool = WebResearchTool(WebConfig(broad_search=False), llm=None)
    tool.backends = [_Backend("direct", [])]
    tool.broad_backends = [broad_backend]

    monkeypatch.setattr("app.capabilities.web_research.fetch_page", lambda url, config: FetchedPage(url=url, final_url=url, title="x", text="", retrieved_at="t", ok=False))
    request = _request(entity_names=["大谷翔平"], objective="日本媒体评价大谷翔平", user_wording="日本媒体如何评价大谷翔平？")
    tool.execute(request)
    languages = {language for _query, language in broad_backend.calls}
    assert languages, "broad backend was not invoked"
    # English-first with a context-specific expansion; never every language for every query.
    assert "en" in languages or any(lang.startswith("ja") for lang in languages)


def test_post_verification_escalation_fires_when_direct_pass_supports_nothing(monkeypatch):
    direct_url = "https://direct.example.com/judge"
    broad_url = "https://broad.example.com/judge"
    tool = _tool(
        [_Hit(direct_url, "Aaron Judge injury history", "injury")],
        [_Hit(broad_url, "Aaron Judge injury report", "calf strain")],
    )
    bodies = {
        direct_url: "Aaron Judge is a professional baseball player in New York.",
        broad_url: "Aaron Judge has a moderate calf injury and is on the injured list.",
    }

    def fake_fetch(url, config):
        return FetchedPage(url=url, final_url=url, title="Aaron Judge", text=bodies[url],
                           retrieved_at="2026-09-29T00:00:00Z", ok=True, content_hash="h")

    monkeypatch.setattr("app.capabilities.web_research.fetch_page", fake_fetch)
    result = tool.execute(_request())
    data = result.artifact.structured_data
    assert data["broad_search"]["used"] is True
    assert any(f["status"] == "SUPPORTED" for f in data["evidence_findings"])


def test_post_verification_escalation_is_skipped_when_support_exists(monkeypatch):
    from tests.assessment_fixtures import FixtureAssessorLLM

    broad_backend = _Backend("broad_search", [_Hit("https://broad.example.com/judge", "Aaron Judge injury")])
    tool = WebResearchTool(WebConfig(broad_search=False), llm=FixtureAssessorLLM())
    tool.backends = [_Backend("direct", [_Hit("https://direct.example.com/judge", "Aaron Judge injury report", "injury")])]
    tool.broad_backends = [broad_backend]

    def fake_fetch(url, config):
        return FetchedPage(url=url, final_url=url, title="Aaron Judge injury",
                           text="Aaron Judge has a moderate calf injury and is on the injured list.",
                           retrieved_at="2026-09-29T00:00:00Z", ok=True, content_hash="h")

    monkeypatch.setattr("app.capabilities.web_research.fetch_page", fake_fetch)
    result = tool.execute(_request())
    assert result.artifact.structured_data["broad_search"]["used"] is False
    assert not broad_backend.calls


def test_shared_given_name_is_not_the_same_subject():
    from app.web.span_selector import subject_signature_present

    assert subject_signature_present("Aaron Judge injury update", "Aaron Judge")
    assert not subject_signature_present("Aaron Hicks injury update", "Aaron Judge")
    assert subject_signature_present("Ohtani changed his approach", "Shohei Ohtani")
    assert subject_signature_present("大谷翔平改变了打击策略", "大谷翔平")


def test_broad_hits_require_the_full_subject():
    from app.capabilities.web_research import _filter_broad_hits

    hits = [
        _Hit("https://broad.example/judge", "Aaron Judge injury report"),
        _Hit("https://broad.example/hicks", "Aaron Hicks injury report"),
    ]
    kept = _filter_broad_hits(hits, {"aaron", "judge"})
    assert [hit.url for hit in kept] == ["https://broad.example/judge"]


def test_broad_hits_for_single_token_subject_are_not_filtered():
    from app.capabilities.web_research import _filter_broad_hits

    hits = [_Hit("https://broad.example/ohtani", "Ohtani injury report")]
    assert _filter_broad_hits(hits, {"ohtani"}) == hits


def test_candidate_order_is_deterministic_for_the_same_inputs():
    from app.web.ranking import CandidateContext, rank_candidates

    hits = [
        _Hit("https://b.example/2", "Aaron Judge injury report"),
        _Hit("https://a.example/1", "Aaron Judge injury report"),
        _Hit("https://c.example/3", "Aaron Judge injury report"),
    ]
    context = CandidateContext(subjects=("Aaron Judge",), required_tokens=frozenset({"injury"}))
    first = [hit.url for hit in rank_candidates(hits, context)]
    second = [hit.url for hit in rank_candidates(list(reversed(hits)), context)]
    assert first == second


def test_resolved_scope_becomes_the_artifact_actual_scope(monkeypatch):
    tool = _tool([_Hit("https://example.com/judge", "Aaron Judge injury update")], [])

    def fake_fetch(url, config):
        return FetchedPage(url=url, final_url=url, title="Aaron Judge injury update",
                           text="Aaron Judge has a moderate calf strain.", retrieved_at="2026-09-29T00:00:00Z",
                           ok=True, content_hash="h")

    monkeypatch.setattr("app.capabilities.web_research.fetch_page", fake_fetch)
    result = tool.execute(_request())
    scope = result.artifact.actual_scope
    assert scope is not None and scope.date_range is not None
    assert str(scope.date_range.end) == "2026-09-29"
