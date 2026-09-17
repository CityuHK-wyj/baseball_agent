"""Independent contextual Judge.

Two distinct stages:

1. **Deterministic verification** (``scope_verification`` module) answers structural
   questions: is the Artifact valid, was scope established, were the requested filters
   applied, are identifiers compatible, is there a hard mismatch.

2. **Independent Judge** (this module) answers contextual questions: does this evidence
   actually help answer *this* Need, does the *combination* of Artifacts satisfy it, is
   the interpretation justified, does the evidence support the requested claim type, is an
   explanation merely correlation, what is still missing.

The Judge participates in *every* evidence loop and is invoked on apparently sufficient
evidence, not only on weak candidates. It may downgrade a semantic SATISFIED candidate,
but it may never override a deterministic hard mismatch. A Judge outage is an explicit
``UNAVAILABLE`` condition, never automatic success.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.models.artifact_runtime import (Goal, JudgeAssessment, Need, RuntimeArtifact)
from app.artifact_runtime.scope_verification import ScopeVerdict

# Claim categories whose evidential bar is higher than an observation.
_INFERENCE_TYPES = ("REPORTED_EXPLANATION", "HYPOTHESIS", "CAUSAL_CLAIM")


@dataclass(frozen=True)
class JudgeRequest:
    goal: Goal
    need: Need
    artifacts: tuple[RuntimeArtifact, ...]
    deterministic: ScopeVerdict
    candidate_claim_type: str = "OBSERVED_FACT"
    supporting_artifact_ids: tuple[str, ...] = ()


class IndependentJudge(Protocol):
    name: str

    def assess(self, request: JudgeRequest) -> JudgeAssessment: ...


class DeterministicContextJudge:
    """Default independent judge: deterministic, but structurally separate from scope
    verification. It is *always* consulted; it can veto; it cannot upgrade."""

    name = "deterministic-context-judge"

    def assess(self, request: JudgeRequest) -> JudgeAssessment:
        need = request.need
        reasons: list[str] = []
        missing: list[str] = []
        outcome = "SATISFIED"
        deterministic = request.deterministic

        # An unverified core dimension cannot be certified as sufficient.
        if deterministic.hard_mismatch:
            return JudgeAssessment(
                assessment_id=f"judge-{need.need_id}",
                goal_id=request.goal.goal_id, need_id=need.need_id,
                outcome="UNAVAILABLE", helpful=False,
                reasons=("deterministic hard mismatch; the judge may not override it",))

        if deterministic.unknown:
            outcome = "PARTIAL"
            missing.append("deterministic scope verification is incomplete")
            reasons.append("some requested dimensions remain UNKNOWN")
        if any(status == "PARTIAL" for status in deterministic.dimensions.values()):
            if need.criticality == "CORE":
                outcome = "PARTIAL"
            reasons.append("some requested dimensions are only partially verified")

        # Relevance: an artifact must contribute the evidence this Need expects.
        if not request.supporting_artifact_ids:
            outcome = "UNSATISFIED"
            missing.append("no accepted artifact supports this Need")
            reasons.append("no evidence was found that advances this Need")
        elif need.preferred_capabilities:
            produced = {export.export_type for artifact in request.artifacts
                        for export in artifact.exports}
            if not (produced & set(need.preferred_capabilities)):
                outcome = "PARTIAL"
                missing.append("expected information type was not produced")
                reasons.append("evidence does not carry the expected information type")

        # Claim-type sufficiency: statistics alone cannot support causal explanation.
        if request.candidate_claim_type in _INFERENCE_TYPES:
            kinds = {artifact.kind for artifact in request.artifacts}
            has_narrative = bool(kinds & {"web_evidence", "knowledge"})
            has_measurement = bool(kinds & {"analytics", "batting_stats", "derived",
                                            "ranked"})
            if request.candidate_claim_type == "CAUSAL_CLAIM" and not (
                    has_narrative and has_measurement):
                outcome = "UNSATISFIED" if outcome == "SATISFIED" else outcome
                missing.append("causal claim needs sourced explanation plus measurement")
                reasons.append("a statistical change alone does not establish causation")
            elif request.candidate_claim_type == "REPORTED_EXPLANATION" and not has_narrative:
                outcome = "PARTIAL" if outcome != "UNSATISFIED" else outcome
                missing.append("no sourced explanation was retrieved")
                reasons.append("measurement alone cannot support a reported explanation")

        # Joint support may rescue a dimension that no single artifact fully covers.
        joint = request.supporting_artifact_ids
        return JudgeAssessment(
            assessment_id=f"judge-{need.need_id}", goal_id=request.goal.goal_id,
            need_id=need.need_id, outcome=outcome, helpful=outcome in ("SATISFIED", "PARTIAL"),
            interpretation_justified=outcome == "SATISFIED",
            claim_type_supported=outcome != "UNSATISFIED",
            joint_support=tuple(joint), missing_information=tuple(dict.fromkeys(missing)),
            reasons=tuple(dict.fromkeys(reasons)), judge=self.name, independent=True)


class JudgeUnavailable(Exception):
    """Raised by an injected judge when it cannot produce an assessment."""


def judge_outage_assessment(goal: Goal, need: Need, detail: str = "") -> JudgeAssessment:
    return JudgeAssessment(
        assessment_id=f"judge-{need.need_id}", goal_id=goal.goal_id, need_id=need.need_id,
        outcome="UNAVAILABLE", helpful=False,
        reasons=(f"assessment unavailable: {detail or 'judge outage'}",),
        judge="unavailable", independent=True)
