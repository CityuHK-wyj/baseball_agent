"""Seeded metamorphic checks for proposition-local directed attribution."""

import random

import pytest

from app.contracts.models import AnswerProposition, RelationKind, RelationSemantics
from app.semantic.relation_attribution import directed_support
from app.web.span_selector import select_spans


def _proposition(kind, source="", target="", season=None, population=None):
    return AnswerProposition(
        statement="directed relation", subject=source or target,
        required_information="directed relation",
        time_scope={"season": season} if season else {}, population=population,
        relation_semantics=RelationSemantics(
            relation_kind=kind, relation_kinds=[kind],
            relation_source=source, relation_target=target,
        ),
    )


@pytest.mark.parametrize("seed", range(50), ids=lambda n: f"seed_{n}")
def test_generated_direction_and_locality(seed):
    rng = random.Random(seed)
    names = [f"{rng.choice(['North', 'West', 'East', 'South'])}{rng.randrange(10000,99999)}"
             for _ in range(4)]
    a, b, c, d = names
    while len(set(names)) < 4:
        names = [f"Node{rng.randrange(10000,99999)}" for _ in range(4)]
        a, b, c, d = names
    cause = _proposition(RelationKind.CAUSE, target=b)
    effect = _proposition(RelationKind.EFFECT, source=b)
    full = _proposition(RelationKind.CAUSE, source=a, target=b)
    correct = f"{a} caused {b}."
    passive = f"{b} was caused by {a}."
    reversed_edge = f"{b} caused {a}."
    other_target = f"{a} caused {c}."
    consequence = f"As a result of {b}, {c} was canceled."
    nearby = f"{d} caused {c}."
    for passage in (correct, passive, f"{a} led to {b}.", f"{b} happened because {a}."):
        assert directed_support(cause, passage), (seed, passage)
        assert directed_support(full, passage), (seed, passage)
        assert not directed_support(effect, passage), (seed, passage)
    assert not directed_support(full, reversed_edge)
    assert not directed_support(full, other_target)
    assert not directed_support(cause, consequence)
    assert directed_support(effect, consequence)
    assert not directed_support(cause, consequence + " " + nearby)
    assert directed_support(cause, nearby + " " + correct)
    assert not directed_support(full, f"{a} was discussed. {d} caused {b}.")
    assert not directed_support(full, f"{a} caused {c}. {d} caused {b}.")
    scoped = _proposition(RelationKind.CAUSE, source=a, target=b,
                          season=2000 + rng.randrange(25), population="Postseason")
    year = scoped.time_scope["season"]
    assert directed_support(scoped, f"In the {year} Postseason, {a} caused {b}.")
    assert not directed_support(scoped, f"In the {year + 1} Postseason, {a} caused {b}.")
    assert not directed_support(scoped, f"In the {year} Regular Season, {a} caused {b}.")
    assert not directed_support(scoped, f"The {year} Postseason began. {a} caused {b}.")


def test_selector_prefers_same_proposition_directed_clause():
    cause = _proposition(RelationKind.CAUSE, target="Lockout")
    effect = _proposition(RelationKind.EFFECT, source="Lockout")
    text = "Lockout caused Cancelation. Salary dispute caused Lockout."
    cause_spans = select_spans(text, propositions=[cause], max_spans=1)
    effect_spans = select_spans(text, propositions=[effect], max_spans=1)
    assert "Salary dispute caused Lockout" in cause_spans[0].text
    assert "Lockout caused Cancelation" in effect_spans[0].text


def test_web_candidate_admission_is_proposition_local():
    from app.capabilities.web_research import WebResearchTool
    from app.config import WebConfig
    from app.contracts.models import EvidenceSpan, RetrievedDocument

    text = "As a result of Lockout, Games were canceled."
    document = RetrievedDocument(id="d", url="https://example.org/record",
                                 retrieved_at="2026-09-30T00:00:00Z", content_hash="x",
                                 body_text=text, retrieval_status="OK")
    span = EvidenceSpan(id="s", document_id="d", text=text, start=0, end=len(text))
    candidate = {**span.model_dump(mode="json"), "quality": "prose", "span_score": 3.0}
    finding = {"document_id": "d", "evidence_spans": [candidate]}
    cause = _proposition(RelationKind.CAUSE, target="Lockout")
    effect = _proposition(RelationKind.EFFECT, source="Lockout")
    cause.required_information = "causes of Lockout"
    effect.required_information = "effects of Lockout"
    from tests.assessment_fixtures import FixtureAssessorLLM

    results = WebResearchTool(WebConfig(broad_search=False), llm=FixtureAssessorLLM())._verify(
        [cause, effect], [finding], [document.model_dump(mode="json")]
    )
    assert all(item.proposition_id != cause.id for item in results)
    assert any(item.proposition_id == effect.id and item.status.value == "SUPPORTED" for item in results)
