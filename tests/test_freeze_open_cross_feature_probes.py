"""Audit counterexamples retained as strict OPEN findings, not safety passes.

Ordinary tests record repaired mechanisms; strict xfails retain still-open findings.
Neither substitutes for the final independent freeze audit.
"""
from types import SimpleNamespace

import pytest

from app.capabilities.dynamic_fact import DynamicFactTool
from app.contracts.models import AnswerProposition, AuthorityNeed, CoverageState
from app.response.answer_contract import AnswerPackage, _requirement_coverage
from app.response.composition_contract import parse_composer_draft
from app.verification.composition_conformance import conform_composition
from app.verification.evidence_semantics import derive_authority_expectation
from app.verification.judge import Judge
from tests.test_dynamic_fact_safety_wsa08 import _config, _dynamic_artifact, _FakeStream, _person_payload, _needs
from tests.test_web_evidence_semantics_wsa01_02 import _injury_current_goal, _prose_proposition


def test_current_team_does_not_satisfy_injury_requirement():
    goal, _ = _injury_current_goal()
    artifact = _dynamic_artifact(player_name="Shohei Ohtani", entity_id="660271")
    projection = Judge(max_inspections=0).evaluate(goal, [artifact], _needs())
    assert not projection.claims
    assert projection.goal_status.value != "COMPLETE"


def test_current_team_provider_does_not_attest_arbitrary_historical_as_of(monkeypatch):
    import httpx
    monkeypatch.setattr("app.capabilities.web_search.assert_safe_url", lambda url: url)
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: _FakeStream(_person_payload()))
    fact = DynamicFactTool(_config())._current_team(123, "1980-01-01")
    assert fact is None or fact.get("as_of") != "1980-01-01"


@pytest.mark.parametrize("kind,text", [
    ("ANALYSIS", "Player X is injured."),
    ("ANALYSIS", "Player B has the higher average."),
    ("LIMITATION", "Player X is injured."),
])
def test_missing_conformance_cannot_render_unsupported_factual_prose(kind, text):
    package = AnswerPackage(goal_id="g", goal_status="LIMITED", language="en")
    result = conform_composition(parse_composer_draft({"statements": [
        {"kind": kind, "text": text}]}), package, assessment=None)
    assert text not in result.text


def test_negated_fan_reaction_does_not_weaken_current_injury_authority():
    prop = _prose_proposition("Current injury status, NOT fan reaction",
                             "Current injury status, NOT fan reaction")
    assert derive_authority_expectation(prop).need == AuthorityNeed.OFFICIAL_STATE


def test_compound_requirement_requires_every_mandatory_child():
    supported = AnswerProposition(requirement_id="parent", statement="child A", mandatory=True,
                                  coverage=CoverageState.SATISFIED)
    unresolved = AnswerProposition(requirement_id="parent", statement="child B", mandatory=True,
                                   coverage=CoverageState.UNSUPPORTED)
    assert _requirement_coverage([supported, unresolved]) != CoverageState.SATISFIED.value
