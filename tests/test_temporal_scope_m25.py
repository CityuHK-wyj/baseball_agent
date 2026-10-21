"""Generated temporal-scope tests (M2.5).

The resolver is deterministic and anchored at Goal creation. A seeded generator varies the
anchor date, information class, expression and wording; every failing case is replayable from
its seed via ``pytest -k "seed_<n>"``.
"""

from __future__ import annotations

import datetime as dt
import random

import pytest

from app.contracts.models import (
    AnalysisRequest,
    Constraint,
    EntityMention,
    EntityReference,
    RelationKind,
    SemanticInterpretation,
)
from app.semantic.goals import RequirementBuilder
from app.semantic.temporal import (
    DEFAULT_WINDOW_DAYS,
    default_window_days,
    information_class,
    resolve_temporal_scope,
)
from app.verification.propositions import build_propositions

SEEDS = list(range(30))

EXPRESSIONS: dict[str, tuple[str, str]] = {
    "injury": ("recent injury report", RelationKind.STATUS),
    "trade": ("latest trade news", RelationKind.STATUS),
    "interview": ("recent interview about the change", RelationKind.QUOTE),
    "community": ("community opinion about the signing", RelationKind.GENERAL),
    "performance": ("recent performance trend", RelationKind.COMPARISON),
}

ENTITIES = ["Shohei Ohtani", "Aaron Judge", "Mookie Betts", "Juan Soto", "Gerrit Cole"]


def _anchor(seed: int) -> dt.date:
    rng = random.Random(seed ^ 0xA11CE)
    return dt.date(2024, 1, 1) + dt.timedelta(days=rng.randint(0, 1000))


@pytest.mark.parametrize("seed", SEEDS)
def test_same_goal_same_anchor_resolves_identically(seed):
    anchor = _anchor(seed)
    text, relation = EXPRESSIONS[list(EXPRESSIONS)[seed % len(EXPRESSIONS)]]
    semantic = _semantic(text, relation)
    first = RequirementBuilder().build(semantic, anchor_date=anchor)
    second = RequirementBuilder().build(semantic, anchor_date=anchor)
    assert _scopes_by_requirement(first) == _scopes_by_requirement(second)


@pytest.mark.parametrize("seed", SEEDS)
def test_anchor_date_moves_the_relative_window(seed):
    text, relation = EXPRESSIONS[list(EXPRESSIONS)[seed % len(EXPRESSIONS)]]
    anchor_a = _anchor(seed)
    anchor_b = anchor_a + dt.timedelta(days=11)
    scope_a = resolve_temporal_scope(text, relation_kinds=[relation], anchor_date=anchor_a)
    scope_b = resolve_temporal_scope(text, relation_kinds=[relation], anchor_date=anchor_b)
    if scope_a.is_window():
        assert scope_a.end == anchor_a.isoformat()
        assert scope_b.end == anchor_b.isoformat()
        assert scope_a.window_days == scope_b.window_days


@pytest.mark.parametrize("seed", SEEDS)
def test_harmless_paraphrase_preserves_scope(seed):
    rng = random.Random(seed)
    anchor = _anchor(seed)
    relation = rng.choice(list(EXPRESSIONS.values()))[1]
    first = resolve_temporal_scope("recent injury update", relation_kinds=[relation], anchor_date=anchor)
    second = resolve_temporal_scope("latest injury update", relation_kinds=[relation], anchor_date=anchor)
    assert (first.information_class, first.window_days) == (second.information_class, second.window_days)


@pytest.mark.parametrize("seed", SEEDS)
def test_unrelated_wording_does_not_change_window(seed):
    anchor = _anchor(seed)
    base = resolve_temporal_scope("recent injury report about the player", relation_kinds=[RelationKind.STATUS], anchor_date=anchor)
    noisy = resolve_temporal_scope(
        "please find, if you can, a recent injury report about the player in question",
        relation_kinds=[RelationKind.STATUS], anchor_date=anchor,
    )
    assert (base.kind, base.window_days) == (noisy.kind, noisy.window_days)


def test_explicit_year_overrides_inferred_window():
    anchor = dt.date(2026, 9, 29)
    scope = resolve_temporal_scope("recent injury report in 2021", relation_kinds=[RelationKind.STATUS], anchor_date=anchor)
    assert scope.kind == "SEASON"
    assert scope.season == 2021
    assert scope.confirmed is True


def test_explicit_constraint_overrides_text():
    anchor = dt.date(2026, 9, 29)
    constraints = [Constraint(kind="season", value=1994)]
    scope = resolve_temporal_scope("recent effects", relation_kinds=[RelationKind.EFFECT], constraints=constraints, anchor_date=anchor)
    assert scope.kind == "SEASON"
    assert scope.season == 1994


def test_explicit_duration_is_honoured():
    anchor = dt.date(2026, 9, 29)
    scope = resolve_temporal_scope("trades in the past 3 weeks", relation_kinds=[RelationKind.STATUS], anchor_date=anchor)
    assert scope.kind == "RELATIVE"
    assert scope.window_days == 21
    assert scope.start == (anchor - dt.timedelta(days=21)).isoformat()
    assert scope.end == anchor.isoformat()


def test_unresolved_without_anchor_is_explicit():
    scope = resolve_temporal_scope("recent injury report", relation_kinds=[RelationKind.STATUS], anchor_date=None)
    assert scope.kind == "UNRESOLVED"
    assert scope.confirmed is False
    assert "anchor" in scope.note


def test_information_class_defaults_are_distinct_and_central():
    assert default_window_days("INJURY") != default_window_days("INTERVIEW")
    assert default_window_days("GENERIC") in DEFAULT_WINDOW_DAYS.values()
    assert information_class([RelationKind.STATUS], "injury") == "INJURY"
    assert information_class([RelationKind.QUOTE], "interview") == "INTERVIEW"


@pytest.mark.parametrize("seed", SEEDS)
def test_propositions_inherit_the_canonical_resolved_scope(seed):
    anchor = _anchor(seed)
    text, relation = EXPRESSIONS[list(EXPRESSIONS)[seed % len(EXPRESSIONS)]]
    semantic = _semantic(text, relation)
    goal = RequirementBuilder().build(semantic, anchor_date=anchor)
    analysis = next((r for r in goal.requirements if r.kind.value == "ANALYSIS"), None)
    if analysis is None or analysis.resolved_scope is None:
        pytest.skip("no analysis requirement")
    propositions = build_propositions(goal)
    matching = [p for p in propositions if p.requirement_id == analysis.id]
    if not analysis.resolved_scope.is_window():
        pytest.skip("no relative window in this case")
    assert matching
    for proposition in matching:
        window = proposition.time_scope.get("date_range")
        assert isinstance(window, dict)
        assert window.get("end") == analysis.resolved_scope.end


def _semantic(text: str, relation: RelationKind) -> SemanticInterpretation:
    entity = ENTITIES[sum(ord(ch) for ch in text) % len(ENTITIES)]
    return SemanticInterpretation(
        raw_query=text,
        language="en",
        entities=[EntityMention(mention=entity, type_hint="player")],
        entity_references=[EntityReference(surface=entity, type_hint="player")],
        analysis_requests=[AnalysisRequest(text=text, objective=text)],
    )


def _scopes_by_requirement(goal) -> list[tuple[str, str]]:
    return sorted(
        (
            requirement.kind.value,
            requirement.resolved_scope.model_dump_json() if requirement.resolved_scope else "",
        )
        for requirement in goal.requirements
    )
