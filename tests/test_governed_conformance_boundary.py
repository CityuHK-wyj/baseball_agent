"""Conformance is representation against one immutable package, never a second Judge."""
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from app.contracts.models import CoverageState, QualifiedEvidence
from app.response.answer_contract import AnswerFact, AnswerPackage, RequirementAnswer, build_answer_package
from app.response.composition_contract import parse_composer_draft, parse_conformance_assessment
from app.verification.composition_conformance import conform_composition
from tests.conformance_fixtures import bound_review, complete_review_data
from tests.test_governed_judge_boundary import _evaluate


def _package():
    return AnswerPackage(goal_id="g", goal_status="LIMITED", language="en",
        facts=[AnswerFact(marker="S1", text="A hitter hit 35 home runs.", claim_id="c1")],
        qualified_evidence=[QualifiedEvidence(span_text="An observer reports discomfort.",
                                             limitation="unverified account")],
        limitations=["No current medical record was acquired.", "Historical scope remains unresolved."],
        unresolved=[RequirementAnswer(requirement_id="r1", statement="current injury status",
                                     kind="EVIDENCE", required=True, coverage="UNSUPPORTED")])


def _draft(kind="ANALYSIS", text="The hitter has a torn ligament."):
    return parse_composer_draft({"statements": [{"kind": kind, "text": text}]})


@pytest.mark.parametrize("kind", ["ANALYSIS", "LIMITATION", "FACTUAL", "QUALIFIED", "UNTYPED"])
def test_no_composer_label_authorizes_prose_during_review_outage(kind):
    package = _package()
    draft = _draft(kind)
    result = conform_composition(draft, package)
    assert "torn ligament" not in result.text
    assert "35 home runs" in result.text
    assert "unverified account" in result.text
    assert all(text in result.text for text in package.limitations)
    assert "current injury status" in result.text
    assert package.goal_status == "LIMITED"


@pytest.mark.parametrize("malformed", [None, [], {}, {"statements": "yes"},
    {"statements": [{"index": 0, "epistemic": "ANALYSIS"}]},
    {"statements": [{"index": 0, "epistemic": "LIMITATION", "unsupported_addition": "false"}]}])
def test_malformed_reviews_fail_closed(malformed):
    package, draft = _package(), _draft()
    assessment = parse_conformance_assessment(malformed)
    result = conform_composition(draft, package, assessment)
    assert "torn ligament" not in result.text


def test_partial_review_omits_only_unassessed_free_prose():
    package = _package()
    draft = parse_composer_draft({"statements": [
        {"kind": "FACTUAL", "text": "He recorded 35 homers. [S1]", "citations": ["S1"]},
        {"kind": "ANALYSIS", "text": "The hitter has a torn ligament."},
    ]})
    assessment = bound_review({"statements": [{"index": 0, "epistemic": "FACTUAL",
                                               "approved_claim_backed": True, "claim_markers": ["S1"]}]},
                              package, draft)
    result = conform_composition(draft, package, assessment)
    assert "35 homers" in result.text
    assert "torn ligament" not in result.text


def test_empty_statement_array_does_not_bypass_conformance_via_answer_text():
    from app.response.composition_contract import ComposerDraft
    result = conform_composition(ComposerDraft(answer_text="The hitter has a torn ligament."), _package())
    assert "torn ligament" not in result.text
    assert "35 home runs" in result.text


@pytest.mark.parametrize("field,value", [("index", True), ("index", -1), ("epistemic", "UNRECOGNIZED"),
                                          ("claim_markers", "S1"), ("approved_claim_backed", "true")])
def test_schema_does_not_coerce_authority_relevant_fields(field, value):
    data = complete_review_data({"statements": [{"index": 0, "epistemic": "FACTUAL"}]})
    data["statements"][0][field] = value
    assert parse_conformance_assessment(data) is None


def test_duplicate_indices_are_malformed_not_first_verdict_wins():
    data = complete_review_data({"statements": [{"index": 0, "epistemic": "ANALYSIS"}]})
    data["statements"] *= 2
    assert parse_conformance_assessment(data) is None


@pytest.mark.parametrize("text", [
    "He hit 35 home runs and was the best hitter. [S1]",
    "He hit 35 home runs, exceeding every other hitter. [S1]",
    "He hit 35 home runs and has a torn ligament. [S1]",
])
def test_reviewed_added_clause_is_not_rescued_as_analysis(text):
    package = _package()
    draft = _draft("FACTUAL", text)
    verdict = bound_review({"statements": [{"index": 0, "epistemic": "FACTUAL",
        "approved_claim_backed": True, "claim_markers": ["S1"], "unsupported_addition": True}]}, package, draft)
    result = conform_composition(draft, package, verdict)
    assert text not in result.text
    assert "35 home runs" in result.text
    assert "UNSUPPORTED_FACTUAL" in {v.kind for v in result.violations}


def test_valid_paraphrase_analysis_and_limitation_are_retained():
    package = _package()
    draft = parse_composer_draft({"statements": [
        {"kind": "ANALYSIS", "text": "He recorded 35 homers. [S1]"},
        {"kind": "FACTUAL", "text": "My interpretation is that this was productive hitting."},
        {"kind": "LIMITATION", "text": "A current medical record is still unavailable."},
    ]})
    verdict = bound_review({"statements": [
        {"index": 0, "epistemic": "FACTUAL", "approved_claim_backed": True, "claim_markers": ["S1"]},
        {"index": 1, "epistemic": "ANALYSIS", "material_conclusion": True, "framed_as_interpretation": True},
        {"index": 2, "epistemic": "LIMITATION", "limitation_ids": ["L1"]},
    ]}, package, draft)
    result = conform_composition(draft, package, verdict)
    assert "recorded 35 homers" in result.text
    assert "productive hitting" in result.text
    assert "current medical record is still unavailable" in result.text
    assert package.limitations[1] in result.text


def test_review_is_bound_to_both_package_and_draft():
    package, draft = _package(), _draft("FACTUAL", "He recorded 35 homers. [S1]")
    verdict = bound_review({"statements": [{"index": 0, "epistemic": "FACTUAL",
        "approved_claim_backed": True, "claim_markers": ["S1"]}]}, package, draft)
    changed_draft = _draft("FACTUAL", "He has a torn ligament. [S1]")
    result = conform_composition(changed_draft, package, verdict)
    assert "torn ligament" not in result.text
    assert "ASSESSMENT_IDENTITY_MISMATCH" in {v.kind for v in result.violations}
    other = AnswerPackage(goal_id="other", goal_status="LIMITED", language="en")
    assert "35 homers" not in conform_composition(draft, other, verdict).text


def test_package_is_self_contained_deeply_immutable_and_detached():
    goal, projection = _evaluate()
    package = build_answer_package(goal, projection)
    assert package.governed_requirements and package.requirement_assessments
    assert package.propositions and package.claims and package.citation_sources
    identity = package.package_id
    with pytest.raises(FrozenInstanceError):
        package.goal_status = "FAILED"
    with pytest.raises(TypeError):
        package.facts.clear()
    with pytest.raises(TypeError):
        package.governed_requirements[0].statement = "new user authority"
    goal.requirements[0].statement = "different mutable goal"
    assert package.package_id == identity
    assert package.governed_requirements[0].statement != goal.requirements[0].statement
    assert package.requirement_assessments[0].coverage == CoverageState.SATISFIED


def test_same_package_independently_reaches_composer_and_conformance(monkeypatch):
    from app.response.composer import ResponseComposer
    from app.contracts.models import SemanticInterpretation
    goal, projection = _evaluate()
    package = build_answer_package(goal, projection)
    seen = []
    class Model:
        configured = True
        def complete_json(self, **kwargs):
            return SimpleNamespace(data={"statements": [{"kind": "FACTUAL", "text": "unsafe prose"}]})
    composer = ResponseComposer(Model())
    original = composer._payload
    def payload(g, s, p, supplied):
        seen.append(("composer", supplied))
        return original(g, s, p, supplied)
    def review(llm, supplied, draft, **kwargs):
        seen.append(("conformance", supplied))
        return None
    monkeypatch.setattr(composer, "_payload", payload)
    monkeypatch.setattr("app.response.composer.request_conformance_assessment", review)
    monkeypatch.setattr("app.response.composer.build_answer_package", lambda *a, **k: pytest.fail("package reconstructed"))
    composer.compose(goal, SemanticInterpretation(raw_query=goal.raw_query), projection, package=package)
    assert seen == [("composer", package), ("conformance", package)]
    assert all(item is package for _, item in seen)
