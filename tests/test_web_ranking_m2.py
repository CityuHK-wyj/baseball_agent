"""M2-3: relationship-aware candidate ranking."""

from __future__ import annotations

from app.web.ranking import (
    CandidateContext,
    WEIGHTS,
    rank_candidates,
    ranked_candidates,
    score_candidate,
    source_trust,
)
from app.web.sources.base import SourceDescriptor


class _Hit:
    def __init__(self, url, title="", snippet="", engine="broad_search"):
        self.url = url
        self.title = title
        self.snippet = snippet
        self.engine = engine


def _wikipedia_descriptor():
    return SourceDescriptor(
        source_id="wikipedia",
        kind="ENCYCLOPEDIA",
        discovery=True,
        full_body=True,
        structured=False,
        historical=True,
        current=True,
        trust_weight=0.85,
    )


def test_relationship_relevance_outranks_generic_source_prestige():
    """A highly authoritative but irrelevant page must not outrank a relevant credible one."""
    authoritative_irrelevant = _Hit(
        "https://www.mlb.com/news/standings", title="MLB standings", snippet="league table",
        engine="publisher_feeds",
    )
    relevant_credible = _Hit(
        "https://blogs.fangraphs.com/strike", title="The 1994 strike salary cap cause",
        snippet="salary cap caused the 1994 strike", engine="publisher_feeds",
    )
    context = CandidateContext(
        subjects=("1994 Major League Baseball strike",),
        required_tokens=frozenset({"causes", "salary", "cap"}),
        season=1994,
        descriptors=(_wikipedia_descriptor(),),
    )
    ranked = rank_candidates([authoritative_irrelevant, relevant_credible], context)
    assert ranked[0].url == relevant_credible.url


def test_authority_contributes_but_is_bounded():
    assert WEIGHTS["trust"] < WEIGHTS["required"]
    assert WEIGHTS["trust"] < WEIGHTS["subject"]


def test_trust_banding_is_monotone():
    assert source_trust("statsapi") > source_trust("wikipedia") > source_trust("publisher_feeds")
    assert source_trust("publisher_feeds") > source_trust("broad_search") > source_trust("google_news")


def test_ranking_is_deterministic_regardless_of_input_order():
    hits = [
        _Hit("https://b.example/2", "strike salary cap", engine="publisher_feeds"),
        _Hit("https://a.example/1", "strike salary cap", engine="publisher_feeds"),
        _Hit("https://c.example/3", "strike salary cap", engine="publisher_feeds"),
    ]
    context = CandidateContext(subjects=("1994 strike",), required_tokens=frozenset({"salary", "cap"}), season=1994)
    first = [hit.url for hit in rank_candidates(hits, context)]
    second = [hit.url for hit in rank_candidates(list(reversed(hits)), context)]
    assert first == second


def test_discovery_only_engine_is_penalised():
    direct = _Hit("https://source.example/a", "strike salary cap", engine="publisher_feeds")
    discovery = _Hit("https://news.google.com/x", "strike salary cap", engine="google_news")
    context = CandidateContext(
        subjects=("1994 strike",),
        required_tokens=frozenset({"salary", "cap"}),
        discovery_only_engines=frozenset({"google_news"}),
    )
    ranked = rank_candidates([discovery, direct], context)
    assert ranked[0].url == direct.url


def test_duplicate_domain_is_demoted():
    first = _Hit("https://same.example/1", "strike salary cap cause", engine="publisher_feeds")
    second = _Hit("https://same.example/2", "strike salary cap cause", engine="publisher_feeds")
    other = _Hit("https://other.example/1", "strike salary cap", engine="publisher_feeds")
    context = CandidateContext(subjects=("1994 strike",), required_tokens=frozenset({"salary", "cap"}))
    scored = ranked_candidates([first, second, other], context)
    penalties = {item.hit.url: item.breakdown.get("duplicate_domain_penalty", 0.0) for item in scored}
    assert penalties["https://same.example/1"] == 0.0
    assert penalties["https://same.example/2"] == -WEIGHTS["duplicate_domain_penalty"]


def test_score_breakdown_exposes_signals():
    hit = _Hit("https://x.example/1", "1994 strike salary cap cause", engine="publisher_feeds")
    context = CandidateContext(subjects=("1994 strike",), required_tokens=frozenset({"salary", "cap"}), season=1994, intent="EVENT")
    score = score_candidate(hit, context)
    assert score.breakdown["subject"] > 0
    assert score.breakdown["required"] > 0
    assert score.breakdown["scope"] > 0
