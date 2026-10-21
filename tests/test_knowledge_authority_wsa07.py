"""WSA-07 / GA-08: Shared Knowledge authority boundary.

Stable semantic knowledge (definitions, aliases, terminology, rules) remains usable for
interpretation, definitions and identity. Mutable factual state is *evidence*: it must
re-enter the normal evidence path and can never be asserted merely because it is cached in
Shared Knowledge.
"""

from __future__ import annotations

from tools.requirement_ancestry_fixture import Goal
from app.contracts.models import (
    Artifact,
    GoalStatus,
    RequirementCompletion,
    RequirementKind,
    Scope,
    UserRequirement,
)
from app.knowledge.store import KnowledgeStore, knowledge_class
from app.verification.judge import Judge


def _knowledge_artifact(items: list[dict], *, need_id: str = "n1") -> Artifact:
    return Artifact(
        id="art_knowledge", kind="KNOWLEDGE_ITEM",
        structured_data={"items": items},
        text_content="\n".join(f"{i.get('title')}: {i.get('summary')}" for i in items),
        actual_scope=Scope(source="SHARED_KNOWLEDGE"),
        need_id=need_id,
    )


def _explanation_goal(statement: str = "explain OPS") -> Goal:
    return Goal(
        statement=statement, raw_query=f"what is {statement}?",
        requirements=[UserRequirement(
            id="r1", kind=RequirementKind.ANALYSIS, statement=statement,
            completion=RequirementCompletion.EXPLANATION, background_ok=True,
        )],
    )


def _evidence_goal(statement: str, objective: str, subject: str = "Shohei Ohtani") -> Goal:
    return Goal(
        statement=statement, raw_query=statement,
        requirements=[UserRequirement(
            id="r1", kind=RequirementKind.EVIDENCE, statement=statement,
            value={"objective": objective, "mention": subject},
        )],
    )


# ------------------------------------------------------------------- classification


def test_knowledge_class_splits_stable_from_mutable():
    assert knowledge_class("DEFINITION") == "SEMANTIC"
    assert knowledge_class("ENTITY_ALIAS") == "SEMANTIC"
    assert knowledge_class("RULE") == "SEMANTIC"
    assert knowledge_class("FACT") == "MUTABLE_FACT"
    assert knowledge_class("INJURY_STATUS") == "MUTABLE_FACT"
    assert knowledge_class("TRANSACTION") == "MUTABLE_FACT"
    assert knowledge_class("something-new") == "UNKNOWN"


# --------------------------------------------------------------- scenario A/B: usable


def test_A_stable_definition_remains_usable():
    goal = _explanation_goal("explain OPS")
    artifact = _knowledge_artifact([
        {"id": "GLOSSARY:OPS", "type": "DEFINITION", "title": "OPS", "summary": "On-base Plus Slugging."}
    ])
    projection = Judge().evaluate(goal, [artifact], [])
    assert projection.claims, "a stable definition must still ground a definition obligation"
    assert any("On-base Plus Slugging" in claim.text for claim in projection.claims)


def test_B_entity_alias_lookup_is_retained():
    store = KnowledgeStore()
    item = store.match_alias("Aaron Judge")
    assert item is not None
    assert item.knowledge_class == "SEMANTIC"
    assert item.id.startswith("PLAYER:")
    # An entity alias artifact remains usable for an identity/definition obligation.
    goal = _explanation_goal("who is Aaron Judge")
    artifact = _knowledge_artifact([
        {"id": item.id, "type": "ENTITY_ALIAS", "title": item.title,
         "summary": item.summary, "aliases": item.aliases}
    ])
    projection = Judge().evaluate(goal, [artifact], [])
    assert projection.claims


# --------------------------------------------------------- scenario C/D: not usable


def test_C_mutable_knowledge_cannot_satisfy_a_current_factual_requirement():
    goal = _evidence_goal("Is Shohei Ohtani currently injured?", "current injury status of Shohei Ohtani")
    artifact = _knowledge_artifact([
        {"id": "FACT:injury", "type": "FACT", "title": "Shohei Ohtani injury status",
         "summary": "Shohei Ohtani is currently injured.",
         "source_refs": ["https://example.test/report"]}
    ])
    projection = Judge().evaluate(goal, [artifact], [])
    # No factual Claim is minted from cached mutable state.
    assert not projection.claims
    assert projection.goal_status != GoalStatus.COMPLETE
    # It is preserved as qualified (cached) evidence with provenance and a limitation.
    assert projection.qualified_evidence
    cached = projection.qualified_evidence[0]
    assert cached.kind == "MUTABLE_KNOWLEDGE"
    assert cached.source_url == "https://example.test/report"
    assert "cached mutable knowledge" in cached.limitation


def test_D_cached_mutable_evidence_does_not_bypass_evidence_governance():
    """A curated current-state item with no fresh evidence leaves the requirement unresolved."""
    goal = _evidence_goal("What is Shohei Ohtani's current team?", "current team of Shohei Ohtani")
    artifact = _knowledge_artifact([
        {"id": "FACT:team", "type": "CURRENT_STATE", "title": "Shohei Ohtani current team",
         "summary": "Shohei Ohtani currently plays for the Dodgers.",
         "source_refs": ["https://example.test/roster"]}
    ])
    projection = Judge().evaluate(goal, [artifact], [])
    assert not projection.claims
    assert projection.goal_status == GoalStatus.LIMITED
    assert projection.qualified_evidence


def test_unknown_knowledge_type_is_not_sufficient():
    goal = _evidence_goal("Is Shohei Ohtani currently injured?", "current injury status of Shohei Ohtani")
    artifact = _knowledge_artifact([
        {"id": "X:1", "type": "SOMETHING_NEW", "title": "Shohei Ohtani injury status",
         "summary": "Shohei Ohtani is currently injured."}
    ])
    projection = Judge().evaluate(goal, [artifact], [])
    assert not projection.claims
    assert projection.goal_status != GoalStatus.COMPLETE
