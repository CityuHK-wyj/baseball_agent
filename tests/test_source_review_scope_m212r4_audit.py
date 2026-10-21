"""Audit GA-01 regression: the source review must cover the whole authoritative wording.

`bind_obligations` builds a synthetic source-review clause. It must never inherit a
narrower ``bound_source_clause`` from another requirement, otherwise an interpreter
``source_text`` that names only one clause silently drops the remaining mandatory clauses
and the requested contract can become COMPLETE for a question the user did not ask.
"""
from __future__ import annotations

import pytest

from app.capabilities.analytics.conformance import bind_obligations, constraint_coverage
from app.contracts.models import (
    AnalysisRequest,
    Constraint,
    EntityMention,
    RequirementKind,
    SemanticInterpretation,
)
from app.semantic.goals import RequirementBuilder


def _requirements(text: str, analysis_text: str, extra=()):
    semantic = SemanticInterpretation(
        raw_query=text,
        understanding=text,
        analysis_requests=[AnalysisRequest(text=analysis_text, objective=analysis_text)],
        entities=[EntityMention(mention="Ohtani", type_hint="player")] if "Ohtani" in text else [],
        constraints=list(extra),
    )
    goal = RequirementBuilder().build(semantic)
    return [r.model_dump(mode="json") for r in goal.requirements if r.required]


def _bound(text: str, analysis_text: str, extra=()):
    reqs = _requirements(text, analysis_text, extra)
    return bind_obligations({}, dict(analytical_requirements=reqs, bound_user_intent=text, raw_query=text))


def _filters(result):
    return result["execution_constraints"].get("filters") or []


# A narrowed interpreter analysis text must not hide the second mandatory clause.
@pytest.mark.parametrize("text,analysis", [
    ("Report home runs and only against lefties", "Report home runs"),
    ("Report walks and also versus southpaws", "Report walks"),
    ("统计本垒打 并且只面对左投手", "统计本垒打"),
])
def test_narrow_source_text_does_not_hide_a_mandatory_clause(text, analysis):
    result, issues = _bound(text, analysis)
    # The handedness clause must survive into the requested contract …
    assert any(f.get("field") == "p_throws" and f.get("value") == "L" for f in _filters(result)), (text, result)
    # … and the request must not silently claim completeness: either the source review or
    # the clause ledger reports the unexplained residue.
    states = [c.get("state") for c in result.get("clause_coverage", [])]
    assert issues or "COMPLETE" not in states or len(states) > 1, (text, issues, states)


# Metamorphic: reordering / synonym changes must never silently drop the obligation.
@pytest.mark.parametrize("text,analysis", [
    ("only against lefties and report home runs", "report home runs"),
    ("report home runs; only against lefties", "report home runs"),
    ("report home runs and also only versus LHP", "report home runs"),
])
def test_reordered_clauses_keep_the_handedness_obligation(text, analysis):
    result, issues = _bound(text, analysis)
    has_filter = any(f.get("field") == "p_throws" and f.get("value") == "L" for f in _filters(result))
    silently_complete = (
        not issues
        and not result.get("unresolved_obligations")
        and all(c.get("state") == "COMPLETE" for c in result.get("clause_coverage", []))
    )
    assert has_filter or not silently_complete, (text, result, issues)


# Control: a single fully-specified clause still resolves without a spurious source-review issue.
def test_fully_specified_clause_still_completes():
    result, issues = _bound(
        "Report Ohtani home runs in 2025",
        "Report Ohtani home runs in 2025",
        extra=[Constraint(kind="season", value=2025, explicit=True)],
    )
    assert "Report" not in " ".join(str(u) for u in result.get("unresolved_obligations") or [])
    assert not any("source_review" in issue for issue in issues), issues


# The source-review entry must be its own scope, not an alias of another requirement.
def test_source_review_is_a_distinct_requirement_scope():
    result, _ = _bound("Report home runs and only against lefties", "Report home runs")
    ids = [c.get("requirement_id") for c in result.get("clause_coverage", [])]
    assert "source_review" in ids
