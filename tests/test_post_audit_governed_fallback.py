"""Post-audit governed-integrity recovery regression suite.

A Judge projection that is internally invalid (a mandatory communicable proposition is
SATISFIED but no approved Claim states it) must never be exposed as valid and must not
crash the normal user path. Recovery only removes authority: offending propositions are
downgraded, existing claims are retained, and no Claim is invented.
"""
from __future__ import annotations

from app.contracts.models import AnswerProjection, AnswerProposition, Claim, GoalStatus, Reference, RequirementAssessment, ArtifactAssessment
from app.runtime.orchestrator import Orchestrator
from app.verification.governed_state import (
    GovernedStateError,
    governed_limited_fallback,
    validate_governed_projection,
)


def _invalid_projection(*, status=GoalStatus.COMPLETE):
    return AnswerProjection(
        goal_id="g",
        goal_status=status,
        artifact_assessments=[ArtifactAssessment(artifact_id='approved',source_kind='SYNTHETIC_FIXTURE',status='ELIGIBLE')],
        requirement_assessments=[RequirementAssessment(requirement_id='r1',status='SATISFIED',
            coverage='SATISFIED',authority_status='AUTHORITATIVE')],
        claims=[Claim(text="approved supported result", proposition_ids=["p2"],
                      support_refs=[Reference(ref_type='ARTIFACT',ref_id='approved')])],
        propositions=[
            AnswerProposition(id="p1", goal_id="g", requirement_id="r1", statement="unbacked fact",
                              kind="FACT", coverage="SATISFIED", mandatory=True),
            AnswerProposition(id="p2", goal_id="g", requirement_id="r1", statement="backed fact",
                              kind="FACT", coverage="SATISFIED", mandatory=True),
        ],
    )


def test_invalid_projection_is_rejected_by_the_integrity_guard():
    invalid = _invalid_projection().frozen_snapshot()
    try:
        validate_governed_projection(invalid)
    except GovernedStateError:
        pass
    else:  # pragma: no cover - the invalid fixture must fail
        raise AssertionError("invalid projection was accepted")


def test_governed_fallback_downgrades_only_the_unbacked_proposition():
    invalid = _invalid_projection().frozen_snapshot()
    fallback = governed_limited_fallback(invalid)
    validate_governed_projection(fallback)
    assert fallback.goal_status == GoalStatus.LIMITED
    coverage = {p.id: p.coverage.value for p in fallback.propositions}
    assert coverage["p1"] == "PARTIAL"
    assert coverage["p2"] == "SATISFIED"
    # Supported claim is retained; no unsupported claim is created.
    assert [c.text for c in fallback.claims] == ["approved supported result"]
    assert any("integrity recovery" in note for note in fallback.limitations)


def test_governed_fallback_does_not_mutate_the_source_projection():
    invalid = _invalid_projection().frozen_snapshot()
    before = invalid.model_dump_json()
    _ = governed_limited_fallback(invalid)
    assert invalid.model_dump_json() == before
    assert invalid.goal_status == GoalStatus.COMPLETE


def test_governed_fallback_with_only_unbacked_content_has_no_claims():
    invalid = AnswerProjection(
        goal_id="g", goal_status=GoalStatus.COMPLETE, claims=[],
        propositions=[AnswerProposition(id="p1", goal_id="g", requirement_id="r1",
                                        statement="fact", kind="FACT", coverage="SATISFIED", mandatory=True)],
    ).frozen_snapshot()
    fallback = governed_limited_fallback(invalid)
    validate_governed_projection(fallback)
    assert fallback.goal_status == GoalStatus.LIMITED
    assert fallback.claims == []
    assert fallback.propositions[0].coverage.value == "PARTIAL"


def test_orchestrator_recovers_with_a_new_valid_judge_evaluation():
    invalid = _invalid_projection().frozen_snapshot()
    from tests.governed_state_fixture import approved_snapshot
    valid = approved_snapshot(AnswerProjection(goal_id="g", goal_status=GoalStatus.LIMITED,
                             propositions=[AnswerProposition(id="p1", goal_id="g", requirement_id="r1",
                                                             statement="fact", kind="FACT",
                                                             coverage="PARTIAL", mandatory=True)]))
    outputs = iter([invalid, valid])
    lifecycle = Orchestrator()
    projection = lifecycle.evaluate(lambda: next(outputs))
    assert projection is valid
    assert lifecycle.judge_attempts == 2
    assert lifecycle.governed_retries == 1


def test_orchestrator_exhausted_recovery_exposes_invalid_projection_for_fallback():
    invalid = _invalid_projection().frozen_snapshot()
    lifecycle = Orchestrator()
    try:
        lifecycle.evaluate(lambda: invalid)
    except GovernedStateError:
        pass
    else:  # pragma: no cover
        raise AssertionError("exhausted recovery must fail closed")
    assert lifecycle.last_invalid_projection is invalid
