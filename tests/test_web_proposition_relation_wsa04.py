"""WSA-04: proposition relation / polarity preservation for Web evidence.

WSA-04 is not "negation detection". It is the asymmetric invariant that required-token
overlap is *candidate eligibility*, never proposition entailment:

* the Evidence Semantic Assessor proposes a typed ``PropositionRelation`` (ENTAILS /
  CONTRADICTS / UNCERTAIN / IRRELEVANT), separate from assertion strength and temporal
  semantics;
* the deterministic layer maps that typed relation plus the typed expectations to
  SUPPORTED / CONTRADICTED / INSUFFICIENT and never invents ENTAILS from token overlap;
* a bounded finite negation/denial cue may only *fail closed* (contradiction hint); the
  absence of a cue proves nothing;
* the no-LLM fallback fails closed rather than fabricating semantic certainty.

Fresh metamorphic language is used only in the tests; production contains no benchmark
vocabulary.
"""

from __future__ import annotations

import json

import pytest

from app.contracts.models import (
    AssertionStrength,
    CoverageState,
    EvidenceAssessment,
    PropositionRelation,
    TemporalMode,
    VerificationStatus,
)
from app.verification.evidence import _negation_near, verify_span
from app.verification.evidence_semantics import assess_compatibility, evidence_expectations
from tests.assessment_fixtures import FixtureAssessorLLM
from tests.test_web_evidence_semantics_wsa01_02 import (
    _document,
    _goal,
    _injury_current_goal,
    _judge,
    _prose_proposition,
    _span,
    _web_artifact,
)


# --------------------------------------------------------------------------- helpers


def _injury_current():
    return _prose_proposition("Is Shohei Ohtani currently injured?", "current injury status of Shohei Ohtani")


def _injury_historical(year: int = 2023):
    return _prose_proposition(
        f"Was Shohei Ohtani injured in {year}?", f"Shohei Ohtani injury in {year}", year=year
    )


def _verify(proposition, text: str, publication: str | None = "2026-10-01", *, llm=None):
    document = _document(text, publication)
    return verify_span(proposition, _span(text, document), document, llm=llm, allow_llm=llm is not None)


def _assessment(
    relation: str,
    strength: str,
    temporal: str = "CURRENT_STATE",
    *,
    source: str = "llm",
    polarity: str | None = None,
    source_role=None,
) -> EvidenceAssessment:
    from app.contracts.models import ContentRole, EvidentialBasis, SourceClass, SourceRoleAssessment

    return EvidenceAssessment(
        subject_match=True,
        source_role=source_role or SourceRoleAssessment(
            source_class=SourceClass.NEWS, content_role=ContentRole.REPORTING,
            evidential_basis=EvidentialBasis.SECONDARY, source="llm",
        ),
        relation=PropositionRelation(relation),
        polarity=polarity
        or {"ENTAILS": "AFFIRMED", "CONTRADICTS": "NEGATED"}.get(relation, "UNKNOWN"),
        assertion_strength=AssertionStrength(strength),
        temporal_relevance=temporal,
        source=source,
    )


def _compat(proposition, assessment, *, document=None, span_text="", temporal=None, assertion=None):
    temporal = temporal or evidence_expectations(proposition)[0]
    assertion = assertion or evidence_expectations(proposition)[1]
    return assess_compatibility(proposition, temporal, assertion, assessment, document, span_text)


# --------------------------------------------------------- typed relation is separate


def test_relation_is_a_distinct_typed_dimension():
    from app.contracts.models import ASSERTION_RANK

    assert {item.value for item in PropositionRelation} == {
        "ENTAILS", "CONTRADICTS", "UNCERTAIN", "IRRELEVANT"
    }
    assessment = _assessment("ENTAILS", "SPECULATIVE")
    assert assessment.relation == PropositionRelation.ENTAILS
    assert assessment.assertion_strength == AssertionStrength.SPECULATIVE
    assert assessment.temporal_relevance == "CURRENT_STATE"
    # Relation, strength and temporal are independent (no overloaded AssertionStrength).
    assert ASSERTION_RANK[AssertionStrength.SPECULATIVE] < ASSERTION_RANK[AssertionStrength.ASSERTED]


# ----------------------------------------------------- deterministic relation mapping


def test_entails_asserted_entails_compatible_support():
    proposition = _injury_current()
    result = _compat(
        proposition, _assessment("ENTAILS", "ASSERTED"),
        document=_document("Shohei Ohtani is injured.", "2026-10-01"),
        span_text="Shohei Ohtani is injured.",
    )
    assert result.verdict == "COMPATIBLE"


def test_contradicts_asserted_maps_to_contradicted():
    result = _compat(_injury_current(), _assessment("CONTRADICTS", "ASSERTED"))
    assert result.verdict == "CONTRADICTED"


def test_contradicts_speculative_is_not_asserted_contradiction():
    result = _compat(_injury_current(), _assessment("CONTRADICTS", "SPECULATIVE"))
    assert result.verdict == "INSUFFICIENT"


def test_uncertain_and_irrelevant_are_insufficient():
    proposition = _injury_current()
    assert _compat(proposition, _assessment("UNCERTAIN", "ASSERTED")).verdict == "INSUFFICIENT"
    assert _compat(proposition, _assessment("IRRELEVANT", "ASSERTED")).verdict == "INSUFFICIENT"


def test_entails_below_required_strength_is_insufficient():
    proposition = _injury_current()
    result = _compat(proposition, _assessment("ENTAILS", "SPECULATIVE"))
    assert result.verdict == "INSUFFICIENT"


def test_entails_current_state_without_a_temporal_basis_is_insufficient():
    proposition = _injury_current()
    # No document publication date and no literal date in the span.
    result = _compat(
        proposition, _assessment("ENTAILS", "ASSERTED", "CURRENT_STATE"),
        document=None, span_text="he is injured",
    )
    assert result.verdict == "INSUFFICIENT"


# --------------------------------------------------------------- scenario A-H (verify)


def test_A_direct_entailment_can_support():
    finding = _verify(
        _injury_current(), "Shohei Ohtani injury status: he is injured.", "2026-10-01",
        llm=FixtureAssessorLLM(relation="ENTAILS", strength="ASSERTED"),
    )
    assert finding.relation_state == "ENTAILS"
    assert finding.status == VerificationStatus.SUPPORTED


def test_B_direct_contradiction_is_contradicted():
    finding = _verify(
        _injury_current(), "Shohei Ohtani injury status: he is not injured and is active.", "2026-10-01",
        llm=FixtureAssessorLLM(relation="CONTRADICTS", strength="ASSERTED", status="CONTRADICTED"),
    )
    assert finding.relation_state == "CONTRADICTS"
    assert finding.status == VerificationStatus.CONTRADICTED


@pytest.mark.parametrize("text", [
    "Shohei Ohtani injury status: he has been cleared to play.",
    "Shohei Ohtani injury status: he has recovered from the injury.",
    "Shohei Ohtani injury status: he avoided a serious injury.",
    "Shohei Ohtani injury status: there is no indication that he is injured.",
    "Shohei Ohtani injury status: he is no longer on the injured list.",
])
def test_C_paraphrased_contradiction_is_never_false_support(text):
    proposition = _injury_current()
    # Without an assessor the relation is unresolved: never support.
    fallback = _verify(proposition, text, "2026-10-01")
    assert fallback.status != VerificationStatus.SUPPORTED
    # A competent assessor resolves the paraphrase to CONTRADICTS.
    resolved = _verify(
        proposition, text, "2026-10-01",
        llm=FixtureAssessorLLM(relation="CONTRADICTS", strength="ASSERTED", status="CONTRADICTED"),
    )
    assert resolved.status == VerificationStatus.CONTRADICTED
    assert resolved.relation_state == "CONTRADICTS"


def test_D_recovery_state_transition_is_not_positive_for_current_state():
    proposition = _injury_current()
    text = (
        "Shohei Ohtani injury status: he was previously injured but has now been cleared "
        "to play and returned to the lineup."
    )
    assert _verify(proposition, text, "2026-10-01").status != VerificationStatus.SUPPORTED
    finding = _verify(
        proposition, text, "2026-10-01",
        llm=FixtureAssessorLLM(relation="CONTRADICTS", strength="ASSERTED", status="CONTRADICTED"),
    )
    assert finding.status == VerificationStatus.CONTRADICTED


def test_E_negated_speculation_preserves_relation_and_speculative_strength():
    finding = _verify(
        _injury_current(), "Shohei Ohtani injury status: he may not be injured.", "2026-10-01",
        llm=FixtureAssessorLLM(relation="CONTRADICTS", strength="SPECULATIVE", status="CONTRADICTED"),
    )
    assert finding.relation_state == "CONTRADICTS"
    assert finding.assertion_state == AssertionStrength.SPECULATIVE.value
    assert finding.status == VerificationStatus.INSUFFICIENT
    assert finding.status != VerificationStatus.CONTRADICTED


def test_F_historical_denial_is_contradicted_and_history_compatible():
    proposition = _injury_historical(2023)
    text = "Shohei Ohtani injury in 2023: he was not injured that season and played 135 games."
    assert _verify(proposition, text, "2023-08-01").status != VerificationStatus.SUPPORTED
    finding = _verify(
        proposition, text, "2023-08-01",
        llm=FixtureAssessorLLM(relation="CONTRADICTS", strength="ASSERTED", status="CONTRADICTED",
                               temporal_relevance="HISTORICAL"),
    )
    assert finding.status == VerificationStatus.CONTRADICTED
    assert finding.temporal_state == TemporalMode.HISTORICAL.value


def test_H_ambiguous_source_stays_insufficient():
    finding = _verify(
        _injury_current(), "Shohei Ohtani injury status: the situation remains unclear.", "2026-10-01",
        llm=FixtureAssessorLLM(relation="UNCERTAIN", strength="UNKNOWN", status="INSUFFICIENT"),
    )
    assert finding.status == VerificationStatus.INSUFFICIENT
    assert finding.status != VerificationStatus.SUPPORTED


# ----------------------------------------------------- no-LLM asymmetry / fallback


def test_token_overlap_alone_never_supports_without_an_assessor():
    finding = _verify(_injury_current(), "Shohei Ohtani injury status: he is injured.", "2026-10-01")
    assert finding.checks["subject"] is True
    assert finding.checks["matched_tokens"]
    assert finding.relation_state == PropositionRelation.UNCERTAIN.value
    assert finding.status == VerificationStatus.INSUFFICIENT


def test_finite_contradiction_hint_fails_closed_but_absence_is_not_support():
    assert _negation_near("shohei ohtani did not suffer an injury", ["injury"]) is True
    assert _negation_near("shohei ohtani is injured", ["injury"]) is False
    contradicted = _verify(_injury_current(), "Shohei Ohtani did not suffer an injury.", "2026-10-01")
    assert contradicted.status == VerificationStatus.CONTRADICTED
    # Absence of the finite cue changes nothing: the positive span is still not support.
    positive = _verify(_injury_current(), "Shohei Ohtani injury status: he is injured.", "2026-10-01")
    assert positive.status != VerificationStatus.SUPPORTED


def test_llm_relation_cannot_self_certify_satisfaction_over_a_hard_gate():
    # A stale source with a confident ENTAILS proposal must still be rejected by the
    # temporal expectation before it can become a claim.
    finding = _verify(
        _injury_current(), "Shohei Ohtani injury status: he is on the injured list.", "2019-05-01",
        llm=FixtureAssessorLLM(relation="ENTAILS", strength="ASSERTED"),
    )
    assert finding.status != VerificationStatus.SUPPORTED


def test_finite_contrast_hint_cannot_override_the_llm_into_false_contradiction():
    # "however" is a finite fail-closed cue, but the passage still asserts the injury. The
    # finite hint must not seize polarity from the assessor: the conflict fails closed to
    # INSUFFICIENT instead of a fabricated CONTRADICTED or SUPPORTED.
    text = "Shohei Ohtani injury status: he is injured; however, the team expects a quick recovery."
    finding = _verify(
        _injury_current(), text, "2026-10-01",
        llm=FixtureAssessorLLM(relation="ENTAILS", strength="ASSERTED"),
    )
    assert finding.relation_state == PropositionRelation.UNCERTAIN.value
    assert finding.status == VerificationStatus.INSUFFICIENT
    assert finding.status != VerificationStatus.CONTRADICTED


def test_deterministic_strength_hedge_weakens_a_confident_relation():
    # The model proposes a confirmed ENTAILS, but the passage is speculative; the
    # deterministic hedge net lowers the observed strength and blocks support.
    finding = _verify(
        _injury_current(), "Shohei Ohtani injury status: he could be injured.", "2026-10-01",
        llm=FixtureAssessorLLM(relation="ENTAILS", strength="ASSERTED"),
    )
    assert finding.status != VerificationStatus.SUPPORTED
    assert finding.assertion_state == AssertionStrength.SPECULATIVE.value


# --------------------------------------------------------------- fresh corpus metrics


# (text, simulated assessor relation, simulated strength) - audit-only language.
_METAMORPHIC_CORPUS = [
    ("Shohei Ohtani injury status: the team confirmed he is active and available.",
     "CONTRADICTS", "ASSERTED"),
    ("Shohei Ohtani injury status: reports indicate no lingering issue; he is in the lineup.",
     "CONTRADICTS", "ASSERTED"),
    ("Shohei Ohtani injury status: he avoided the worst-case scenario and will play.",
     "CONTRADICTS", "ASSERTED"),
    ("Shohei Ohtani injury status: was initially feared injured, yet the diagnosis came back clean.",
     "CONTRADICTS", "ASSERTED"),
    ("Shohei Ohtani injury status: he might not be hurt after all.",
     "CONTRADICTS", "SPECULATIVE"),
    ("Shohei Ohtani injury status: not only is he active, he is thriving.",
     "ENTAILS", "ASSERTED"),
    ("Shohei Ohtani injury status: he has been dealing with a sore elbow.",
     "ENTAILS", "ASSERTED"),
    ("Shohei Ohtani injury status: his availability for tonight is uncertain.",
     "UNCERTAIN", "UNKNOWN"),
    ("Shohei Ohtani injury status: teammates were asked about the weather.",
     "IRRELEVANT", "UNKNOWN"),
]


def test_fresh_metamorphic_corpus_has_no_false_support_and_keeps_utility():
    proposition = _injury_current()
    false_support = 0
    unsafe_fallback_support = 0
    promoted_unknown = 0
    utility = 0
    for text, relation, strength in _METAMORPHIC_CORPUS:
        # No assessor: the deterministic fallback must never invent support.
        fallback = _verify(proposition, text, "2026-10-01")
        if fallback.status == VerificationStatus.SUPPORTED:
            unsafe_fallback_support += 1
        # Simulated competent assessor relation.
        assessor = FixtureAssessorLLM(
            relation=relation,
            strength=strength,
            status="SUPPORTED" if relation == "ENTAILS" else "INSUFFICIENT",
        )
        finding = _verify(proposition, text, "2026-10-01", llm=assessor)
        if relation in {"CONTRADICTS", "UNCERTAIN", "IRRELEVANT"}:
            if finding.status == VerificationStatus.SUPPORTED:
                false_support += 1
            if relation == "UNCERTAIN" and finding.status == VerificationStatus.SUPPORTED:
                promoted_unknown += 1
        if relation == "ENTAILS" and strength == "ASSERTED" and finding.status == VerificationStatus.SUPPORTED:
            utility += 1
    assert false_support == 0
    assert unsafe_fallback_support == 0
    assert promoted_unknown == 0
    assert utility >= 1, "valid assessed support must not collapse to zero"


# --------------------------------------------------------- Judge drill-down (scenario G)


class _ContextAssessor:
    """Returns a relation only when the bounded context contains a resolving marker."""

    configured = True

    def __init__(self, marker: str, relation: str, strength: str):
        self.marker = marker
        self.relation = relation
        self.strength = strength
        self.usage_records: list = []

    def complete_json(self, *, role="response", system="", user="", **kwargs):
        if role == "planner":
            return __import__("types").SimpleNamespace(data={"queries": []})
        from tests.assessment_fixtures import grounding_receipts, reviewer_reply
        reply = reviewer_reply(system)
        if reply is not None:
            return reply
        payload = json.loads(user or "{}")
        evidence = payload.get("evidence") or {}
        context = " ".join(str(evidence.get(key) or "") for key in ("context_before", "span", "context_after"))
        relation = self.relation if self.marker in context else "UNCERTAIN"
        return __import__("types").SimpleNamespace(data={
            "status": "CONTRADICTED" if relation == "CONTRADICTS" else "INSUFFICIENT",
            "relation": relation,
            "assertion_strength": self.strength if relation != "UNCERTAIN" else "UNKNOWN",
            "temporal_relevance": "CURRENT_STATE",
            "source_role": {
                "source_class": "NEWS", "content_role": "REPORTING",
                "evidential_basis": "SECONDARY",
            },
            "grounding": grounding_receipts(context) if relation == "ENTAILS" else [],
            "fact": {},
        })


def test_G_judge_drill_down_resolves_relation_from_neighbor_context_without_new_fetch():
    goal, requirement_id = _injury_current_goal()
    proposition = next(p for p in goal.propositions if p.kind.value == "FACT")
    # The span alone does not establish the relation; the neighboring sentence denies it.
    before = "2026-10-05 injury update: "
    span_text = "Shohei Ohtani injury status: he has been cleared."
    after = " The team confirmed there is no injury."
    body = before + span_text + after
    document = _document(body, "2026-10-05")
    from app.contracts.models import EvidenceSpan

    span = EvidenceSpan(document_id=document.id, text=span_text, start=len(before), end=len(before) + len(span_text))
    finding = verify_span(proposition, span, document, allow_llm=False)
    assert finding.status == VerificationStatus.INSUFFICIENT

    artifact = _web_artifact(goal, proposition, document, span, finding)
    projection = _judge(goal, artifact, requirement_id, llm=_ContextAssessor("no injury", "CONTRADICTS", "ASSERTED"))
    assert projection.source_inspections
    inspection = projection.source_inspections[0]
    assert inspection.no_new_fetch is True
    assert inspection.document_id == document.id
    assert inspection.resolved_status == VerificationStatus.CONTRADICTED.value
    # The inspected context is the already-retrieved neighbor, not a new fetch.
    assert "no injury" in (inspection.before + inspection.span_text + inspection.after)


def test_H_judge_drill_down_leaves_unresolved_context_insufficient():
    goal, requirement_id = _injury_current_goal()
    proposition = next(p for p in goal.propositions if p.kind.value == "FACT")
    text = "Shohei Ohtani injury status: he has been cleared."
    document = _document(text, "2026-10-05")
    span = _span(text, document)
    finding = verify_span(proposition, span, document, allow_llm=False)
    artifact = _web_artifact(goal, proposition, document, span, finding)
    projection = _judge(goal, artifact, requirement_id, llm=_ContextAssessor("never-present", "CONTRADICTS", "ASSERTED"))
    if projection.source_inspections:
        assert projection.source_inspections[0].resolved_status == VerificationStatus.INSUFFICIENT.value
    assert projection.goal_status.value != "COMPLETE"


# ------------------------------------------------------- lexical-authority probes


@pytest.mark.parametrize("text", [
    # required-token overlap
    "Shohei Ohtani injury status: injured.",
    # population synonym overlap
    "Shohei Ohtani injury status in the postseason: injured.",
    # metric synonym / attribution surface
    "Shohei Ohtani injury report: 55.",
    # negation-adjacent wording
    "Shohei Ohtani injury status, however, injured.",
])
def test_lexical_rules_never_establish_support_without_an_assessor(text):
    """No lexical rule may substitute for the typed entailment assessment (WSA-04 §8-10)."""
    finding = _verify(_injury_current(), text, "2026-10-01")
    assert finding.status != VerificationStatus.SUPPORTED
    assert finding.semantic_compatibility in {"INSUFFICIENT", "CONTRADICTED"}


def test_required_token_coverage_is_eligibility_not_entailment_proof():
    """``target_ok`` may be True while the relation is unresolved; that is not support."""
    finding = _verify(_injury_current(), "Shohei Ohtani injury status: he is injured.", "2026-10-01")
    assert finding.checks["target_ok"] is True
    assert finding.checks["semantic_compatibility"] == "INSUFFICIENT"
    assert finding.status == VerificationStatus.INSUFFICIENT


# ----------------------------------------------------- end-to-end Judge invariants


def test_llm_entailment_cannot_bypass_the_eligibility_token_gate():
    # Only one of the required-information tokens is present, so coverage is below the
    # eligibility threshold; even a model ENTAILS cannot yield support.
    proposition = _injury_current()
    finding = _verify(
        proposition, "Shohei Ohtani injury update: he is hurt.", "2026-10-01",
        llm=FixtureAssessorLLM(relation="ENTAILS", strength="ASSERTED"),
    )
    assert finding.checks["target_ok"] is False
    assert finding.status == VerificationStatus.INSUFFICIENT


def test_typed_contradiction_never_becomes_a_positive_claim():
    goal, requirement_id = _injury_current_goal()
    proposition = next(p for p in goal.propositions if p.kind.value == "FACT")
    text = "Shohei Ohtani injury status: he is not injured and is active."
    document = _document(text, "2026-10-01")
    span = _span(text, document)
    finding = verify_span(
        proposition, span, document,
        llm=FixtureAssessorLLM(relation="CONTRADICTS", strength="ASSERTED", status="CONTRADICTED"),
        allow_llm=True,
    )
    assert finding.status == VerificationStatus.CONTRADICTED
    artifact = _web_artifact(goal, proposition, document, span, finding)
    projection = _judge(goal, artifact, requirement_id)
    injury = next(p for p in projection.propositions if p.id == proposition.id)
    assert injury.coverage == CoverageState.CONTRADICTED
    assert not [c for c in projection.claims if proposition.id in c.proposition_ids]
    assert projection.goal_status.value != "COMPLETE"


def test_llm_supported_status_without_entailment_cannot_satisfy():
    """A model 'SUPPORTED' label does not substitute for a typed ENTAILS relation."""
    goal, requirement_id = _injury_current_goal()
    proposition = next(p for p in goal.propositions if p.kind.value == "FACT")
    text = "Shohei Ohtani injury status: the situation remains unclear."
    document = _document(text, "2026-10-01")
    span = _span(text, document)
    finding = verify_span(
        proposition, span, document,
        llm=FixtureAssessorLLM(relation="UNCERTAIN", strength="ASSERTED", status="SUPPORTED"),
        allow_llm=True,
    )
    assert finding.status != VerificationStatus.SUPPORTED
    artifact = _web_artifact(goal, proposition, document, span, finding)
    projection = _judge(goal, artifact, requirement_id)
    injury = next(p for p in projection.propositions if p.id == proposition.id)
    assert injury.coverage != CoverageState.SATISFIED
