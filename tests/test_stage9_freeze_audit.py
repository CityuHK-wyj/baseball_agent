"""Stage 9 fresh, unseen audit probes.

These are not the retained repair probes. They are new counterexamples written after the
repair to check the freeze invariants from a different angle: the authority-write boundary,
reverse provenance of factual Claims, and unseen paraphrase/adversarial forms. They do not
prove universal correctness and are not a substitute for the human freeze decision.
"""
from __future__ import annotations

from pathlib import Path
import re

import pytest

from app.contracts.models import (
    AnswerProposition, AuthorityNeed, CoverageState, EvidenceIntent, RelationSemantics,
    RequirementKind, UserRequirement,
)
from app.response.answer_contract import build_answer_package
from app.semantic.goals import RequirementBuilder
from app.verification.evidence_semantics import derive_assertion_expectation, derive_authority_expectation
from app.verification.judge import Judge
from tests.test_native_record_admission import _analysis_proposition
from tests.test_derived_operation_lineage import _derived, _injury_projection
from tests.test_dynamic_fact_safety_wsa08 import _goal_for_subject, _dynamic_artifact, _needs
from tests.test_web_evidence_semantics_wsa01_02 import _goal, _prose_proposition

APP = Path(__file__).resolve().parent.parent / "app"


# --------------------------------------------------------------- authority-write audit


def test_governed_answer_authority_is_written_only_by_the_judge():
    """No presentation/planner/tool module may assign governed answer authority."""
    write_patterns = {
        "goal_status": re.compile(r"\.goal_status\s*=[^=]"),
        "requirement_assessments": re.compile(r"\.requirement_assessments\s*=[^=]"),
        "artifact_assessments": re.compile(r"\.artifact_assessments\s*=[^=]"),
        "claims": re.compile(r"\.claims\s*=[^=]"),
        "coverage": re.compile(r"\.coverage\s*=[^=]"),
    }
    allowed = {
        "goal_status": set(),
        "requirement_assessments": {'app/verification/judge.py','app/verification/governed_state.py'},
        "artifact_assessments": set(),
        "claims": set(),
        # Canonical aggregation/fresh demotion is governance, never presentation.
        "coverage": {'app/verification/judge.py','app/verification/governed_state.py'},
    }
    offenders: list[str] = []
    for path in APP.rglob("*.py"):
        rel = path.relative_to(APP.parent).as_posix()
        for number, line in enumerate(path.read_text().splitlines(), 1):
            for field, pattern in write_patterns.items():
                if pattern.search(line) and "==" not in line and "def " not in line:
                    if rel not in allowed[field]:
                        offenders.append(f"{field}: {rel}:{number}: {line.strip()}")
    assert not offenders, offenders


def test_composer_and_presentation_never_receive_a_mutable_goal_or_projection():
    """The downstream package owns detached state; later edits cannot change it."""
    goal = _goal_for_subject("Aaron Judge")
    artifact = _dynamic_artifact(player_name="Aaron Judge", entity_id="592450")
    projection = Judge(max_inspections=0).evaluate(goal, [artifact], _needs())
    package = build_answer_package(goal, projection, [artifact])
    before = (package.goal_status, tuple(package.limitations),
              tuple(a.status for a in package.requirement_assessments))
    goal.requirements.clear()
    goal.propositions.clear()
    artifact.status = "REJECTED"
    artifact.structured_data.clear()
    assert (package.goal_status, tuple(package.limitations),
            tuple(a.status for a in package.requirement_assessments)) == before


# --------------------------------------------------------------- reverse provenance


def _collect_artifact_ids(artifacts):
    return {artifact.id for artifact in artifacts}


def test_every_factual_claim_reverse_traces_to_support_and_authority():
    goal = _goal_for_subject("Aaron Judge")
    artifact = _dynamic_artifact(player_name="Aaron Judge", entity_id="592450")
    projection = Judge(max_inspections=0).evaluate(goal, [artifact], _needs())
    authoritative = {r.id for r in goal.requirements}
    artifact_ids = {artifact.id}
    assert projection.claims
    for claim in projection.claims:
        # Forward: a claim names only authoritative Requirements
        assert claim.proposition_ids
        for proposition_id in claim.proposition_ids:
            proposition = next(p for p in projection.propositions if p.id == proposition_id)
            assert proposition.requirement_id in authoritative
        # Backward: at least one support reference resolves to a collected artifact
        assert any(ref.ref_id in artifact_ids for ref in claim.support_refs)
    # Any proposition shown SATISFIED must carry an inspectable support link.
    for proposition in projection.propositions:
        if proposition.coverage == CoverageState.SATISFIED:
            assert (proposition.supporting_claim_ids or proposition.supporting_artifact_ids
                    or proposition.evidence_finding_ids), proposition.id


def test_same_subject_team_record_never_satisfies_an_injury_obligation():
    artifact = _dynamic_artifact(player_name="Shohei Ohtani", entity_id="660271")
    projection = _injury_projection(artifact, ())
    assert not projection.claims
    assert projection.goal_status.value != "COMPLETE"
    for proposition in projection.propositions:
        if proposition.coverage == CoverageState.SATISFIED:
            assert (proposition.supporting_claim_ids or proposition.supporting_artifact_ids
                    or proposition.evidence_finding_ids), proposition.id


def test_no_claim_is_minted_for_an_unproven_requirement():
    goal = _goal_for_subject("Aaron Judge")
    artifact = _dynamic_artifact(player_name="Aaron Judge", entity_id="592450")
    for requirement in goal.requirements:
        requirement.provenance = None
    projection = Judge(max_inspections=0).evaluate(goal, [artifact], _needs())
    assert not projection.claims
    assert projection.goal_status.value != "COMPLETE"


# --------------------------------------------------------------- unseen semantic probes


@pytest.mark.parametrize("wording", [
    "current injury status, definitely not supporter sentiment",
    "官方伤情，而非球迷情绪",
    "the injury report, not the fan chatter",
])
def test_unseen_negated_audience_wording_keeps_official_authority(wording):
    proposition = _prose_proposition(wording, "injury status")
    assert derive_authority_expectation(proposition).need == AuthorityNeed.OFFICIAL_STATE
    assert derive_assertion_expectation(proposition).minimum_strength.value == "ASSERTED"


@pytest.mark.parametrize("objective", [
    "per-handedness batting average table",
    "breakdown of average by pitcher side",
    "average grouped by which arm the pitcher uses",
])
def test_unseen_split_paraphrases_resolve_to_a_computable_value(objective):
    # No typed comparison contract -> a split/average obligation is VALUE/AGGREGATION.
    assert _analysis_proposition(objective, "COMPARISON").kind.value == "VALUE"


@pytest.mark.parametrize("program", [
    "output = [{'finding': 'the player is out for the season'}]",
    "output = [{'note': 'traded to a new club'}]",
])
def test_unseen_derived_constant_never_mints_a_fact(program):
    artifact, sources = _derived(program)
    projection = _injury_projection(artifact, sources)
    assert not projection.claims
    assert projection.artifact_assessments[0].status == "REJECTED"


def test_unseen_native_record_field_cannot_satisfy_a_different_predicate():
    goal = _goal_for_subject("Aaron Judge")
    owner = next(r for r in goal.requirements if r.id == "r_ev")
    owner.relation_semantics = RelationSemantics(relation_kind="STATUS", predicate="INJURY_STATUS")
    artifact = _dynamic_artifact(player_name="Aaron Judge", entity_id="592450")
    projection = Judge(max_inspections=0).evaluate(goal, [artifact], _needs())
    assert not projection.claims


def test_unseen_assistant_synthesis_with_valid_quote_stays_non_authoritative():
    from tests.test_requirement_provenance_boundary import InterpreterReply
    from app.semantic.interpreter import SemanticInterpreter
    raw = "What about the next campaign?"
    copied = "Player Z underwent season-ending surgery"
    reply = InterpreterReply({"turn_type": "FOLLOW_UP", "analysis_requests": [
        {"text": copied, "objective": copied, "user_span": raw, "meaning_origin": "ASSISTANT_CONTEXT"}]})
    semantic = SemanticInterpreter(reply).interpret(raw)
    assert not semantic.analysis_requests
    goal = RequirementBuilder().build(semantic)
    assert copied not in str([r.model_dump(mode="json") for r in goal.requirements])


def test_unseen_clarification_delta_must_match_its_backend_target():
    from app.contracts.models import RequirementProvenance
    from app.semantic.provenance import message_digest, provenance_valid
    raw = "the split I asked about"
    provenance = RequirementProvenance(
        origin="VALIDATED_CLARIFICATION", message_id="goal", bound_message=raw,
        message_digest=message_digest(raw), user_span=raw,
        clarification_target_digest="target-abc", clarification_answer="left only")
    requirement = UserRequirement(id="c1", kind=RequirementKind.CONSTRAINT, source="clarification",
                                  statement="left only", value={"analytical_role": "BATTER"},
                                  provenance=provenance)
    good = [dict(candidate_id="c1", target_digest="target-abc", user_answer="left only")]
    assert provenance_valid(requirement.model_dump(mode="json"), bound_message=raw,
                            clarification_deltas=good)
    for bad in (
        [dict(candidate_id="c1", target_digest="other", user_answer="left only")],
        [dict(candidate_id="c1", target_digest="target-abc", user_answer="right only")],
        [dict(candidate_id="c9", target_digest="target-abc", user_answer="left only")],
        [],
    ):
        assert not provenance_valid(requirement.model_dump(mode="json"), bound_message=raw,
                                    clarification_deltas=bad)
