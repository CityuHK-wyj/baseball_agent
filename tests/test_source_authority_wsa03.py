"""WSA-03: contextual source authority and evidence role.

Authority is relational (Source Role x Requirement Need), not a universal scalar trust score.
A high-authority domain does not automatically approve a fact; a community source is not
globally downgraded. The source role is proposed by the assessor and checked by the
deterministic authority gate; it never overrides relation/assertion/temporal compatibility.

These tests exercise the mechanism with fresh wording; production contains no benchmark
vocabulary and no domain->authority table.
"""

from __future__ import annotations

from tools.requirement_ancestry_fixture import Goal
from app.contracts.models import (
    AnswerProposition,
    AuthorityExpectation,
    AuthorityNeed,
    CoverageState,
    PropositionKind,
    RequirementKind,
    UserRequirement,
    VerificationStatus,
)
from app.verification.evidence import verify_span
from app.verification.evidence_semantics import derive_assertion_expectation, derive_temporal_expectation
from tests.assessment_fixtures import FixtureAssessorLLM
from tests.test_web_evidence_semantics_wsa01_02 import _document, _judge, _span, _web_artifact


# --------------------------------------------------------------------------- helpers


def _prop(required: str, *, need: str = "OFFICIAL_STATE", info: str = "INJURY",
          subject: str = "Shohei Ohtani") -> AnswerProposition:
    proposition = AnswerProposition(
        id="prop_wsa03", requirement_id="r_ev", statement=f"{subject} {required}",
        kind=PropositionKind.FACT, subject=subject, required_information=required,
        time_scope={}, information_class=info,
    )
    proposition.authority_expectation = AuthorityExpectation(
        need=AuthorityNeed(need), required=need != "GENERAL"
    )
    proposition.temporal_expectation = derive_temporal_expectation(proposition)
    proposition.assertion_expectation = derive_assertion_expectation(proposition)
    return proposition


def _goal_for(proposition: AnswerProposition):
    requirement = UserRequirement(
        id="r_ev", kind=RequirementKind.EVIDENCE, statement=proposition.statement,
        value={"objective": proposition.required_information},
    )
    goal = Goal(statement=proposition.statement, raw_query=proposition.statement, requirements=[requirement])
    goal.propositions = [proposition]
    return goal, "r_ev"


def _verify(proposition, text: str, publication: str | None = "2026-10-01", *, llm=None):
    document = _document(text, publication)
    return verify_span(proposition, _span(text, document), document, llm=llm, allow_llm=llm is not None)


def _role_llm(
    *,
    source_class: str,
    content_role: str,
    evidential_basis: str,
    relation: str = "ENTAILS",
    strength: str = "ASSERTED",
    status: str = "SUPPORTED",
):
    return FixtureAssessorLLM(
        relation=relation, strength=strength, status=status,
        source_class=source_class, content_role=content_role, evidential_basis=evidential_basis,
    )


def _end_to_end(proposition, text, llm, publication="2026-10-01"):
    """Verify and judge over one shared document/span so provenance resolves."""
    goal, requirement_id = _goal_for(proposition)
    document = _document(text, publication)
    span = _span(text, document)
    finding = verify_span(proposition, span, document, llm=llm, allow_llm=llm is not None)
    artifact = _web_artifact(goal, proposition, document, span, finding)
    return _judge(goal, artifact, requirement_id)


# --------------------------------------------------------------- scenario A: primary


def test_A_first_party_structured_record_can_be_approved():
    proposition = _prop("signing transaction", info="TRANSACTION")
    text = "The Dodgers signing transaction is complete: they have signed Shohei Ohtani."
    finding = _verify(
        proposition, text,
        llm=_role_llm(source_class="STRUCTURED", content_role="PRIMARY_RECORD",
                      evidential_basis="PRIMARY"),
    )
    assert finding.status == VerificationStatus.SUPPORTED
    assert finding.checks["authority_compatible"] is True
    projection = _end_to_end(
        proposition, text,
        _role_llm(source_class="STRUCTURED", content_role="PRIMARY_RECORD", evidential_basis="PRIMARY"),
    )
    assert projection.goal_status.value == "COMPLETE"


def test_A2_official_domain_is_not_automatically_authoritative():
    """The same domain publishing speculation does not become an official record."""
    proposition = _prop("signing transaction", info="TRANSACTION")
    text = "The Dodgers signing transaction column: they may pursue Shohei Ohtani."
    finding = _verify(
        proposition, text,
        llm=_role_llm(source_class="LEAGUE", content_role="SPECULATION", evidential_basis="SECONDARY",
                      strength="SPECULATIVE", status="SUPPORTED"),
    )
    assert finding.status != VerificationStatus.SUPPORTED
    assert (finding.checks["authority_compatible"] is False
            or finding.checks["assertion_compatible"] is False)


# ------------------------------------------------------------- scenario B/C: weak


def test_B_official_domain_editorial_speculation_is_not_approved():
    proposition = _prop("signing transaction", info="TRANSACTION")
    text = "Shohei Ohtani signing transaction: an editorial argues they may pursue a deal."
    finding = _verify(
        proposition, text,
        llm=_role_llm(source_class="NEWS", content_role="SPECULATION", evidential_basis="SECONDARY",
                      strength="SPECULATIVE", status="SUPPORTED"),
    )
    assert finding.status == VerificationStatus.INSUFFICIENT
    projection = _end_to_end(
        proposition, text,
        _role_llm(source_class="NEWS", content_role="SPECULATION", evidential_basis="SECONDARY",
                  strength="SPECULATIVE", status="SUPPORTED"),
    )
    assert projection.goal_status.value != "COMPLETE"
    assert not [c for c in projection.claims if proposition.id in c.proposition_ids]
    assert any(q.proposition_id == proposition.id for q in projection.qualified_evidence)


def test_C_community_post_cannot_establish_official_roster_status():
    proposition = _prop("roster status", info="INJURY")
    text = "Shohei Ohtani roster status: a community post says he was optioned today."
    finding = _verify(
        proposition, text,
        llm=_role_llm(source_class="COMMUNITY", content_role="FIRSTHAND", evidential_basis="UNKNOWN"),
    )
    assert finding.status == VerificationStatus.INSUFFICIENT
    assert finding.checks["authority_compatible"] is False
    projection = _end_to_end(
        proposition, text,
        _role_llm(source_class="COMMUNITY", content_role="FIRSTHAND", evidential_basis="UNKNOWN"),
    )
    assert projection.goal_status.value != "COMPLETE"
    assert projection.qualified_evidence
    assert projection.qualified_evidence[0].kind == "AUTHORITY"


# ------------------------------------------------------ scenario D/E: contextual


def test_D_community_evidence_can_be_authoritative_for_community_requirement():
    proposition = _prop("fan sentiment", need="FIRSTHAND_ACCOUNT", info="COMMUNITY")
    text = "Shohei Ohtani fan sentiment: supporters in the forum say he is their favourite."
    finding = _verify(
        proposition, text,
        llm=_role_llm(source_class="COMMUNITY", content_role="FIRSTHAND", evidential_basis="PRIMARY"),
    )
    assert finding.status == VerificationStatus.SUPPORTED
    assert finding.checks["authority_compatible"] is True


def test_E_specialist_reporting_is_appropriate_for_credible_reporting_need():
    proposition = _prop("injury status", info="INJURY")
    text = "Shohei Ohtani injury status: a beat reporter writes he has a sore elbow."
    finding = _verify(
        proposition, text,
        llm=_role_llm(source_class="SPECIALIST", content_role="REPORTING", evidential_basis="SECONDARY"),
    )
    assert finding.status == VerificationStatus.SUPPORTED
    assert finding.checks["authority_compatible"] is True


# --------------------------------------------------------- scenario F: unknown role


def test_F_unknown_source_role_fails_closed_but_remains_visible():
    proposition = _prop("roster status", info="INJURY")
    text = "Shohei Ohtani roster status: this page says he is on the active roster."
    finding = _verify(
        proposition, text,
        llm=_role_llm(source_class="UNKNOWN", content_role="UNKNOWN", evidential_basis="UNKNOWN"),
    )
    assert finding.status != VerificationStatus.SUPPORTED
    assert finding.checks["authority_compatible"] is False
    projection = _end_to_end(
        proposition, text,
        _role_llm(source_class="UNKNOWN", content_role="UNKNOWN", evidential_basis="UNKNOWN"),
    )
    assert not [c for c in projection.claims if proposition.id in c.proposition_ids]
    assert projection.qualified_evidence  # not discarded


# ---------------------------------------------------- authority vs other dimensions


def test_high_authority_cannot_override_speculative_modality():
    """Authority is not truth: a first-party speculative sentence cannot confirm a fact."""
    proposition = _prop("signing transaction", info="TRANSACTION")
    text = "Shohei Ohtani signing transaction: the club's own site says they may sign him."
    finding = _verify(
        proposition, text,
        llm=_role_llm(source_class="FIRST_PARTY", content_role="OFFICIAL_STATEMENT",
                      evidential_basis="PRIMARY", strength="SPECULATIVE", status="SUPPORTED"),
    )
    assert finding.status == VerificationStatus.INSUFFICIENT
    assert finding.checks["assertion_compatible"] is False


def test_high_authority_cannot_override_stale_temporal_state():
    proposition = _prop("injury status", info="INJURY")
    finding = _verify(
        proposition, "Shohei Ohtani injury status: the official report lists him as injured.", "2019-05-01",
        llm=_role_llm(source_class="FIRST_PARTY", content_role="OFFICIAL_STATEMENT",
                      evidential_basis="PRIMARY"),
    )
    assert finding.status != VerificationStatus.SUPPORTED


# --------------------------------------------------------------- fresh corpus metrics


# (required, need, info, source_class, content_role, basis, strength, expect_approved)
_CORPUS = [
    ("signing transaction", "OFFICIAL_STATE", "TRANSACTION",
     "STRUCTURED", "PRIMARY_RECORD", "PRIMARY", "ASSERTED", True),
    ("signing transaction", "OFFICIAL_STATE", "TRANSACTION",
     "AGGREGATOR", "ANALYSIS", "TERTIARY", "SPECULATIVE", False),
    ("roster status", "OFFICIAL_STATE", "INJURY",
     "COMMUNITY", "FIRSTHAND", "UNKNOWN", "ASSERTED", False),
    ("fan sentiment", "FIRSTHAND_ACCOUNT", "COMMUNITY",
     "COMMUNITY", "FIRSTHAND", "PRIMARY", "ASSERTED", True),
    ("injury status", "OFFICIAL_STATE", "INJURY",
     "SPECIALIST", "REPORTING", "SECONDARY", "ASSERTED", True),
]


def test_fresh_corpus_authority_closure_targets():
    low_authority_approved = 0
    community_rejected_for_community_need = 0
    valid_approved = 0
    for required, need, info, source_class, content_role, basis, strength, expect_approved in _CORPUS:
        proposition = _prop(required, need=need, info=info)
        text = f"Shohei Ohtani {required}: report about {required}."
        finding = _verify(
            proposition, text,
            llm=_role_llm(source_class=source_class, content_role=content_role,
                          evidential_basis=basis, strength=strength, status="SUPPORTED"),
        )
        approved = finding.status == VerificationStatus.SUPPORTED
        if need == "OFFICIAL_STATE" and source_class in {"AGGREGATOR", "COMMUNITY"} and approved:
            low_authority_approved += 1
        if need == "FIRSTHAND_ACCOUNT" and source_class == "COMMUNITY" and not approved:
            community_rejected_for_community_need += 1
        if expect_approved and approved:
            valid_approved += 1
    assert low_authority_approved == 0
    assert community_rejected_for_community_need == 0
    assert valid_approved >= 2


def test_qualified_evidence_never_changes_requirement_or_goal_status():
    proposition = _prop("roster status", info="INJURY")
    text = "Shohei Ohtani roster status: a community post says he was optioned today."
    projection = _end_to_end(
        proposition, text,
        _role_llm(source_class="COMMUNITY", content_role="FIRSTHAND", evidential_basis="UNKNOWN"),
    )
    updated = next(p for p in projection.propositions if p.id == proposition.id)
    assert updated.coverage != CoverageState.SATISFIED
    assert projection.qualified_evidence
    assert not [c for c in projection.claims if proposition.id in c.proposition_ids]
    assert projection.goal_status.value != "COMPLETE"
