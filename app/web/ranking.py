"""Relationship-aware candidate ranking (M2-3, ADR 0027) plus the M1-T7 trust contract.

M1 locked the monotone ``trust_weight`` direction (larger = more trusted) but did not use it
in the production selection path. M2 adds a deterministic :func:`score_candidate` that scores
a search hit by **what the mandatory proposition needs** — subject, required information,
time/population scope and intent — and by source authority, then orders candidates with a
stable tie-break so concurrent discovery cannot change the fetched set.

Authority is deliberately bounded: ``trust_weight`` contributes at most
``WEIGHTS["trust"]``. A highly authoritative but irrelevant page therefore cannot outrank a
directly relevant credible source, which is exactly the *relationship relevance > generic
source prestige* rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, TypeVar

from app.semantic.relations import FRESHNESS_QUERY_CUES, QUOTE_QUERY_CUES, STATUS_QUERY_CUES, VALUE_SUPPORT_CUES
from app.web.sources.base import SourceDescriptor

T = TypeVar("T")

# trust_weight bands from ADR 0027 (higher = more trusted).
SOURCE_TRUST_DEFAULTS: tuple[tuple[str, float], ...] = (
    ("statsapi", 1.00),
    ("official", 1.00),
    ("wikipedia", 0.85),
    ("reference", 0.85),
    ("publisher_feeds", 0.55),
    ("feed:", 0.55),
    ("broad_search", 0.25),
    ("endpoint", 0.25),
    ("google_news", 0.00),
)
DEFAULT_TRUST = 0.25

# Named, testable weights. Relationship terms dominate authority so a relevant credible
# source outranks an authoritative-but-irrelevant page.
WEIGHTS: dict[str, float] = {
    "subject": 3.0,
    "required": 4.0,
    "scope": 1.5,
    "intent": 1.0,
    "trust": 0.5,
    "freshness": 0.5,
    "discovery_penalty": 1.5,
    "duplicate_domain_penalty": 1.0,
}

_STOPWORDS = {
    "the", "a", "an", "of", "for", "and", "or", "to", "in", "on", "at", "is", "are",
    "was", "were", "be", "been", "with", "about", "into", "from", "as", "by", "over",
    "under", "between", "find", "explain", "recent", "report", "reports", "information",
}


@dataclass(frozen=True)
class CandidateContext:
    subjects: tuple[str, ...] = ()
    required_tokens: frozenset[str] = frozenset()
    season: int | None = None
    population: str | None = None
    freshness: str = "MIXED"
    intent: str = "RELATION"
    discovery_only_engines: frozenset[str] = frozenset()
    descriptors: tuple[SourceDescriptor, ...] = ()
    alias_tokens: frozenset[str] = frozenset()
    duplicate_domains: frozenset[str] = frozenset()
    source_intent: str = "ANALYSIS"


@dataclass
class CandidateScore:
    hit: Any
    score: float
    breakdown: dict[str, float] = field(default_factory=dict)


def trust_score(descriptor: SourceDescriptor) -> float:
    """The monotone authority signal: larger means more trusted."""
    return float(descriptor.trust_weight)


def rank_by_trust(descriptors: Iterable[SourceDescriptor]) -> list[SourceDescriptor]:
    """Stable descending ordering by ``trust_weight`` (ties broken by ``source_id``).

    This intentionally ignores lexical match so the direction is unambiguous and testable;
    the production ranker adds the lexical terms around this fixed authority term.
    """
    return sorted(descriptors, key=lambda descriptor: (-trust_score(descriptor), descriptor.source_id))


def _tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for match in re.finditer(r"[0-9]+(?:\.[0-9]+)?|[a-z][a-z0-9'\-]+|[\u4e00-\u9fff]+", (text or "").lower()):
        token = match.group(0)
        if token in _STOPWORDS:
            continue
        if all("\u4e00" <= ch <= "\u9fff" for ch in token):
            if len(token) >= 2:
                tokens.add(token)
        elif len(token) >= 3:
            tokens.add(token)
    return tokens


def _mentions(haystack: str, token: str) -> bool:
    if not token:
        return False
    if all(ord(ch) < 128 for ch in token):
        pattern = rf"(?<![a-z0-9]){re.escape(token)}(?:s|es|d|ed|ing)?(?![a-z0-9])"
        return re.search(pattern, haystack) is not None
    return token in haystack


def _coverage(haystack: str, tokens: Iterable[str]) -> float:
    tokens = [token for token in tokens if token]
    if not tokens:
        return 0.0
    return sum(1 for token in tokens if _mentions(haystack, token)) / len(tokens)


def source_trust(engine: str, descriptors: Iterable[SourceDescriptor] = ()) -> float:
    """Resolve the monotone ``trust_weight`` for a provider identifier."""
    engine_lower = (engine or "").lower()
    for descriptor in descriptors:
        if descriptor.source_id.lower() == engine_lower:
            return float(descriptor.trust_weight)
    for prefix, weight in SOURCE_TRUST_DEFAULTS:
        if engine_lower == prefix or engine_lower.startswith(prefix):
            return weight
    return DEFAULT_TRUST


def is_discovery_only(engine: str, discovery_only: Iterable[str]) -> bool:
    engine_lower = (engine or "").lower()
    return any(engine_lower == item.lower() for item in discovery_only)


def _host(url: str) -> str:
    match = re.match(r"https?://([^/]+)", url or "")
    return (match.group(1) if match else "").lower()


def score_candidate(hit: Any, context: CandidateContext) -> CandidateScore:
    title = str(getattr(hit, "title", "") or "")
    snippet = str(getattr(hit, "snippet", "") or "")
    haystack = f"{title} {snippet}".lower()

    subject_tokens = set()
    for subject in context.subjects:
        subject_tokens |= _tokens(subject)
    subject_tokens |= set(context.alias_tokens)

    subject_coverage = _coverage(haystack, subject_tokens)
    required_coverage = _coverage(haystack, context.required_tokens)
    score = (
        WEIGHTS["subject"] * subject_coverage
        + WEIGHTS["required"] * required_coverage
    )
    breakdown = {
        "subject": round(WEIGHTS["subject"] * subject_coverage, 4),
        "required": round(WEIGHTS["required"] * required_coverage, 4),
    }

    scope_hit = False
    if context.season is not None and re.search(rf"(?<!\d){context.season}(?!\d)", haystack):
        scope_hit = True
    if context.population and context.population.lower().replace("_", " ") in haystack:
        scope_hit = True
    if scope_hit:
        score += WEIGHTS["scope"]
    breakdown["scope"] = WEIGHTS["scope"] if scope_hit else 0.0

    intent_hit = _intent_matches(haystack, context.intent)
    if intent_hit:
        score += WEIGHTS["intent"]
    breakdown["intent"] = WEIGHTS["intent"] if intent_hit else 0.0
    # A retrievable direct interview/transcript is closer to the requested speaker
    # than an encyclopedia summary. This is a preference, never evidence authority.
    proximity = 0.0
    if context.source_intent in {"INTERVIEW", "PRIMARY_STATEMENT"}:
        direct = any(term in haystack for term in ("transcript", "press conference", "interview", "official statement"))
        if direct and "wikipedia" not in str(getattr(hit, "engine", "")).lower():
            proximity = 1.5
    score += proximity
    breakdown["source_proximity"] = proximity
    reference_penalty = 0.0
    if context.source_intent in {"INTERVIEW", "PRIMARY_STATEMENT", "COMMUNITY_DISCUSSION", "LOCAL_REPORTING"}:
        if "wikipedia" in str(getattr(hit, "engine", "")).lower():
            reference_penalty = -2.0
    score += reference_penalty
    breakdown["reference_for_specific_intent"] = reference_penalty

    trust = source_trust(str(getattr(hit, "engine", "") or ""), context.descriptors)
    score += WEIGHTS["trust"] * trust
    breakdown["trust"] = round(WEIGHTS["trust"] * trust, 4)

    if context.freshness == "VOLATILE" and re.search(r"\b(20\d{2})\b", haystack):
        score += WEIGHTS["freshness"]
        breakdown["freshness"] = WEIGHTS["freshness"]
    else:
        breakdown["freshness"] = 0.0

    if is_discovery_only(str(getattr(hit, "engine", "") or ""), context.discovery_only_engines):
        score -= WEIGHTS["discovery_penalty"]
        breakdown["discovery_penalty"] = -WEIGHTS["discovery_penalty"]
    return CandidateScore(hit=hit, score=round(score, 6), breakdown=breakdown)


def _intent_matches(haystack: str, intent: str) -> bool:
    # Canonical relation cues only: relationship relevance is carried by the requirement's
    # RelationSemantics, so the ranker does not keep its own event vocabulary.
    if intent == "INTERVIEW":
        return any(term in haystack for term in QUOTE_QUERY_CUES)
    if intent == "FRESHNESS":
        return any(term in haystack for term in (*STATUS_QUERY_CUES, *FRESHNESS_QUERY_CUES))
    if intent == "METRIC":
        return any(term in haystack for term in VALUE_SUPPORT_CUES)
    # EVENT / RELATION / MISSING add no hardcoded event vocabulary: the requested relationship
    # is already carried by the proposition's required tokens, so no named-event list exists.
    return False


def _rank_scored(hits: Iterable[Any], context: CandidateContext) -> list[CandidateScore]:
    scored = [score_candidate(hit, context) for hit in hits]
    # Stable initial order: score desc, then URL/title so completion order is irrelevant.
    scored.sort(key=lambda item: (-item.score, str(getattr(item.hit, "url", "")), str(getattr(item.hit, "title", ""))))
    seen_domains: set[str] = set()
    adjusted: list[CandidateScore] = []
    for item in scored:
        domain = _host(str(getattr(item.hit, "url", "")))
        penalty = 0.0
        if domain and domain in seen_domains:
            penalty = WEIGHTS["duplicate_domain_penalty"]
        if domain:
            seen_domains.add(domain)
        adjusted.append(
            CandidateScore(hit=item.hit, score=round(item.score - penalty, 6), breakdown={**item.breakdown, "duplicate_domain_penalty": -penalty})
        )
    adjusted.sort(key=lambda item: (-item.score, str(getattr(item.hit, "url", "")), str(getattr(item.hit, "title", ""))))
    return adjusted


def rank_candidates(hits: Iterable[Any], context: CandidateContext) -> list[Any]:
    """Deterministic relationship-aware ordering with a duplicate-domain penalty.

    Ties break on ``(url, title)`` so the order is independent of discovery/completion order.
    The duplicate-domain demotion is computed in a stable two-pass ranking rather than from
    whichever duplicate happened to arrive first.
    """
    return [item.hit for item in _rank_scored(hits, context)]


def ranked_candidates(hits: Iterable[Any], context: CandidateContext) -> list[CandidateScore]:
    """The scored form of :func:`rank_candidates` (for tests and diagnostics)."""
    return _rank_scored(hits, context)
