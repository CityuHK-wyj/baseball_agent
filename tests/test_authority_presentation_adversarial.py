"""WSA-03 / WSA-07 / qualified-presentation adversarial corpus (audit-only wording).

Consolidates the milestone closure targets:

* low-authority evidence incorrectly approved = 0
* high-authority source incorrectly overriding speculative semantics = 0
* mutable KnowledgeItem incorrectly satisfying a current fact = 0
* qualified evidence incorrectly creating a Claim = 0
* qualified evidence incorrectly changing Goal to COMPLETE = 0
* valid partial result incorrectly hidden = 0
* invalid evidence incorrectly surfaced = 0
* community evidence incorrectly rejected for community/sentiment Requirements = 0
"""

from __future__ import annotations

from app.contracts.models import CoverageState, GoalStatus, VerificationStatus
from app.verification.evidence import verify_span
from app.verification.judge import Judge
from tests.test_knowledge_authority_wsa07 import _evidence_goal, _knowledge_artifact
from tests.test_source_authority_wsa03 import _end_to_end, _goal_for, _prop, _role_llm, _verify
from tests.test_web_evidence_semantics_wsa01_02 import _document, _judge, _span, _web_artifact


# (required, need, info, source_class, content_role, basis, strength, allowed)
_AUTHORITY_CASES = [
    ("signing transaction", "OFFICIAL_STATE", "TRANSACTION",
     "STRUCTURED", "PRIMARY_RECORD", "PRIMARY", "ASSERTED", True),
    ("signing transaction", "OFFICIAL_STATE", "TRANSACTION",
     "NEWS", "REPORTING", "SECONDARY", "ASSERTED", True),
    ("signing transaction", "OFFICIAL_STATE", "TRANSACTION",
     "AGGREGATOR", "ANALYSIS", "TERTIARY", "SPECULATIVE", False),
    ("signing transaction", "OFFICIAL_STATE", "TRANSACTION",
     "COMMUNITY", "FIRSTHAND", "UNKNOWN", "ASSERTED", False),
    ("roster status", "OFFICIAL_STATE", "INJURY",
     "COMMUNITY", "FIRSTHAND", "UNKNOWN", "ASSERTED", False),
    ("roster status", "OFFICIAL_STATE", "INJURY",
     "FIRST_PARTY", "OFFICIAL_STATEMENT", "PRIMARY", "ASSERTED", True),
    ("fan sentiment", "FIRSTHAND_ACCOUNT", "COMMUNITY",
     "COMMUNITY", "FIRSTHAND", "PRIMARY", "ASSERTED", True),
    ("fan sentiment", "FIRSTHAND_ACCOUNT", "COMMUNITY",
     "NEWS", "REPORTING", "SECONDARY", "ASSERTED", False),
    ("injury status", "OFFICIAL_STATE", "INJURY",
     "SPECIALIST", "REPORTING", "SECONDARY", "ASSERTED", True),
]


def test_authority_corpus_closure_targets():
    low_authority_approved = 0
    high_authority_overrides_speculation = 0
    community_rejected = 0
    valid_approved = 0
    for required, need, info, source_class, content_role, basis, strength, allowed in _AUTHORITY_CASES:
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
        if not allowed and approved:
            if strength == "SPECULATIVE":
                high_authority_overrides_speculation += 1
        if need == "FIRSTHAND_ACCOUNT" and source_class == "COMMUNITY" and not approved:
            community_rejected += 1
        if allowed and approved:
            valid_approved += 1
    assert low_authority_approved == 0
    assert high_authority_overrides_speculation == 0
    assert community_rejected == 0
    assert valid_approved >= 4


def test_qualified_evidence_never_becomes_authority():
    proposition = _prop("roster status", info="INJURY")
    text = "Shohei Ohtani roster status: a community post says he was optioned today."
    projection = _end_to_end(
        proposition, text,
        _role_llm(source_class="COMMUNITY", content_role="FIRSTHAND", evidential_basis="UNKNOWN"),
    )
    assert projection.qualified_evidence
    assert not [c for c in projection.claims if proposition.id in c.proposition_ids]
    assert projection.goal_status != GoalStatus.COMPLETE
    updated = next(p for p in projection.propositions if p.id == proposition.id)
    assert updated.coverage != CoverageState.SATISFIED


def test_mutable_knowledge_never_satisfies_current_fact():
    goal = _evidence_goal("Is Shohei Ohtani currently injured?", "current injury status of Shohei Ohtani")
    artifact = _knowledge_artifact([
        {"id": "FACT:x", "type": "FACT", "title": "Shohei Ohtani injury status",
         "summary": "Shohei Ohtani is currently injured."}
    ])
    projection = Judge().evaluate(goal, [artifact], [])
    assert not projection.claims
    assert projection.goal_status != GoalStatus.COMPLETE


def test_valid_partial_result_is_not_hidden():
    proposition = _prop("roster status", info="INJURY")
    text = "Shohei Ohtani roster status: a community post says he was optioned today."
    projection = _end_to_end(
        proposition, text,
        _role_llm(source_class="COMMUNITY", content_role="FIRSTHAND", evidential_basis="UNKNOWN"),
    )
    assert projection.qualified_evidence
    assert projection.qualified_evidence[0].span_text


def test_invalid_evidence_is_not_surfaced():
    proposition = _prop("injury status", info="INJURY")
    goal, requirement_id = _goal_for(proposition)
    document = _document("Totally unrelated content about the weather.", "2026-10-01")
    span = _span(document.body_text, document)
    finding = verify_span(
        proposition, span, document,
        llm=_role_llm(source_class="NEWS", content_role="REPORTING", evidential_basis="SECONDARY"),
        allow_llm=True,
    )
    artifact = _web_artifact(goal, proposition, document, span, finding)
    projection = _judge(goal, artifact, requirement_id)
    assert not projection.qualified_evidence
    assert not projection.claims
