"""Coverage / sufficiency judgement.

Completion depends on Goal coverage, never on "some evidence exists". This module
combines two stages:

* **Deterministic verification** (``scope_verification``): structural validity, verified
  scope, hard mismatches. A hard mismatch is non-overridable.
* **Independent Judge** (``judge``): contextual relevance, joint support, claim-type
  sufficiency. It is consulted on *every* candidate, including apparently sufficient
  ones, may downgrade, and never upgrades a deterministic rejection.

Terminal semantics:

* COMPLETE  : every CORE user obligation (and every CORE Need) is supported by verified
              evidence and an independent assessment.
* LIMITED   : useful supported claims exist but material obligations/gaps remain.
* WAITING_FOR_USER : user input is materially necessary.
* FAILED    : no useful supported answer remains after recovery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from app.models.artifact_runtime import (CoverageAssessment, CoverageVerdict, Goal, Need,
                                         RuntimeArtifact)
from app.artifact_runtime.judge import (DeterministicContextJudge, IndependentJudge,
                                        JudgeRequest, judge_outage_assessment)
from app.artifact_runtime.scope_verification import ScopeVerdict, verified_verdict

JudgeHook = Callable[[Goal, Need, RuntimeArtifact], tuple[str, float, tuple[str, ...]]]

_ACCEPTED_STATUSES = ("OK", "PARTIAL")
_VERDICT_RANK = {"IRRELEVANT": 0, "UNAVAILABLE": 0, "UNSATISFIED": 1, "PARTIAL": 2,
                 "SATISFIED": 3}


@dataclass(frozen=True)
class GoalCoverage:
    core_goal_supported: bool
    verdict: CoverageVerdict
    quality: float = 0.0
    gaps: tuple[str, ...] = field(default_factory=tuple)
    missing_needs: tuple[str, ...] = field(default_factory=tuple)
    obligation_coverage: dict[str, str] = field(default_factory=dict)
    missing_obligations: tuple[str, ...] = field(default_factory=tuple)
    conflicts: tuple[str, ...] = field(default_factory=tuple)
    judge_available: bool = True
    accepted_artifact_ids: tuple[str, ...] = field(default_factory=tuple)


_INFERENCE_TOKENS = ("explain", "why", "cause", "reason", "导致", "原因", "为什么",
                     "解释")


def _candidate_claim_type(need: Need, goal: Goal) -> str:
    # The claim type is about what *this Need's* evidence must support, not the whole
    # goal: an analytical Need can be satisfied while the explanation Need still needs a
    # sourced explanation.
    text = f"{need.objective} {need.expected_information}".casefold()
    if any(token in text for token in ("cause", "导致", "caused")):
        return "CAUSAL_CLAIM"
    if any(token in text for token in _INFERENCE_TOKENS):
        return "REPORTED_EXPLANATION"
    return "OBSERVED_FACT"


class CoverageJudge:
    def __init__(self, judge_hook: JudgeHook | None = None, *,
                 independent_judge: IndependentJudge | None = None,
                 satisfied_threshold: float = 0.55, quality_floor: float = 0.35) -> None:
        self._hook = judge_hook
        self._judge = independent_judge or DeterministicContextJudge()
        self._satisfied_threshold = satisfied_threshold
        self._quality_floor = quality_floor

    # -- per-Need ----------------------------------------------------------
    def assess_need(self, goal: Goal, need: Need,
                    artifacts: tuple[RuntimeArtifact, ...], *,
                    upstream_artifacts: tuple[RuntimeArtifact, ...] = ()
                    ) -> CoverageAssessment:
        candidates = [item for item in artifacts if item.artifact_id in need.linked_artifacts]
        usable = tuple(item for item in candidates if item.status in _ACCEPTED_STATUSES)
        verdict = verified_verdict(need.required_scope, usable)

        # Joint support: artifacts whose dimensions are not hard mismatches.
        supporting = tuple(item.artifact_id for item in usable
                           if not item.has_hard_mismatch()
                           and not any(v.status == "MISMATCH"
                                       for v in item.scope_verifications))
        if need.required_scope is None:
            supporting = tuple(item.artifact_id for item in usable)

        quality = max((item.confidence for item in usable), default=0.0)
        if verdict.dimensions and any(status == "PARTIAL" for status in
                                      verdict.dimensions.values()):
            quality = min(quality, 0.6)
        if verdict.hard_mismatch:
            quality = min(quality, 0.3)

        invalid = [item for item in candidates if item.status == "INVALID"]
        gaps = list(verdict.gaps)
        reasons = list(verdict.reasons)
        if invalid:
            gaps.append("an analytical attempt was rejected by the safety boundary")

        if not usable:
            verdict_out: str = "UNSATISFIED"
            gaps.append("no accepted artifact advances this need")
        elif verdict.hard_mismatch:
            verdict_out = "PARTIAL"
        elif verdict.unknown:
            verdict_out = "PARTIAL"
        elif verdict.fully_verified and quality >= self._quality_floor:
            verdict_out = "SATISFIED"
        else:
            verdict_out = "PARTIAL"

        # Backwards-compatible hook: may refine downward, never upward.
        if self._hook is not None and usable:
            try:
                hook_verdict, confidence, hook_reasons = self._hook(goal, need, usable[0])
                if (_VERDICT_RANK.get(hook_verdict, 0) < _VERDICT_RANK[verdict_out]
                        and hook_verdict in ("PARTIAL", "UNSATISFIED", "IRRELEVANT")):
                    verdict_out = hook_verdict
                reasons.extend(hook_reasons)
                quality = max(quality, min(confidence, quality))
            except Exception:  # noqa: BLE001 - hook outage must not block the runtime
                reasons.append("judge hook unavailable; deterministic comparison used")
                verdict_out = "PARTIAL" if verdict_out == "SATISFIED" else verdict_out

        # Independent Judge runs on *every* candidate, including apparent success.
        assessment_available = True
        judge_outcome = "SATISFIED"
        judge_reasons: list[str] = []
        missing_information: list[str] = []
        try:
            judgement = self._judge.assess(JudgeRequest(
                goal=goal, need=need, artifacts=usable, deterministic=verdict,
                candidate_claim_type=_candidate_claim_type(need, goal),
                supporting_artifact_ids=supporting))
            judge_outcome = judgement.outcome
            judge_reasons = list(judgement.reasons)
            missing_information = list(judgement.missing_information)
            if judgement.outcome == "UNAVAILABLE":
                assessment_available = False
            elif _VERDICT_RANK.get(judgement.outcome, 0) < _VERDICT_RANK[verdict_out]:
                verdict_out = judgement.outcome
        except Exception as error:  # noqa: BLE001 - outage is explicit, never silent success
            assessment_available = False
            judgement = judge_outage_assessment(goal, need, f"{type(error).__name__}: {error}")
            judge_outcome = judgement.outcome
            judge_reasons = list(judgement.reasons)
        if not assessment_available and verdict_out == "SATISFIED":
            verdict_out = "PARTIAL"
            gaps.append("independent assessment unavailable; sufficiency not certified")

        reasons.extend(judge_reasons)
        gaps.extend(missing_information)

        supported = tuple(supporting) if verdict_out == "SATISFIED" else ()
        return CoverageAssessment(
            assessment_id=f"coverage-{goal.goal_id}-{need.need_id}",
            goal_id=goal.goal_id, need_id=need.need_id,
            entity_coverage=verdict.aggregate if verdict.dimensions else 1.0,
            temporal_coverage=verdict.aggregate if verdict.dimensions else 1.0,
            population_coverage=verdict.aggregate if verdict.dimensions else 1.0,
            measure_coverage=verdict.aggregate if verdict.dimensions else 1.0,
            evidence_quality=quality, supported_claims=supported,
            missing_needs=() if verdict_out == "SATISFIED" else (need.need_id,),
            core_goal_supported=verdict_out == "SATISFIED" and need.criticality == "CORE",
            verdict=verdict_out, reasons=tuple(dict.fromkeys(reasons)),
            gaps=tuple(dict.fromkeys(gaps)),
            dimension_statuses=dict(verdict.dimensions),
            judge_outcome=judge_outcome, assessment_available=assessment_available,
            independent=True, verified_artifact_ids=tuple(
                item.artifact_id for item in usable
                if not item.has_hard_mismatch()),
            supporting_artifact_ids=tuple(supporting))

    @staticmethod
    def _verdict_rank(verdict: str) -> int:
        return _VERDICT_RANK.get(verdict, 0)

    # -- goal --------------------------------------------------------------
    def summarize(self, goal: Goal, needs: tuple[Need, ...],
                  assessments: tuple[CoverageAssessment, ...], *,
                  obligation_coverage: dict[str, str] | None = None,
                  closed_needs: tuple[str, ...] = ()
                  ) -> GoalCoverage:
        core = [need for need in needs if need.criticality == "CORE"]
        by_need = {item.need_id: item for item in assessments}
        closed = set(closed_needs)
        missing = [need.need_id for need in core
                   if (by_need.get(need.need_id) is None
                       or by_need[need.need_id].verdict != "SATISFIED")
                   and need.need_id not in closed]
        gaps: list[str] = []
        for need in needs:
            # A structurally closed Need (its durable outcome says no plan of that shape can
            # produce it) is disclosed through its attempt gap, not as a silent omission.
            if need.need_id in closed and by_need.get(need.need_id) is None:
                continue
            assessment = by_need.get(need.need_id)
            if assessment is not None:
                gaps.extend(assessment.gaps)
                if assessment.verdict != "SATISFIED":
                    gaps.append(f"{need.need_id} is {assessment.verdict.lower()}")
            elif need.criticality == "CORE":
                gaps.append(f"{need.need_id} was never assessed")
        coverage = dict(obligation_coverage or {})
        missing_obligations: list[str] = []
        for obligation in goal.obligations:
            state = coverage.get(obligation.obligation_id, "MISSING")
            if state != "VERIFIED":
                missing_obligations.append(obligation.obligation_id)
                gaps.append(f"user obligation not covered: {obligation.description}")
        # Typed user conflicts are semantic facts, not generic gaps: a contradictory or
        # future/impossible requirement can never be certified COMPLETE even if unrelated
        # evidence exists.
        conflict_notes: list[str] = []
        hard_conflict = False
        for conflict in goal.conflicts:
            conflict_notes.append(f"{conflict.severity} {conflict.kind}: {conflict.description}")
            if conflict.severity in ("IMPOSSIBLE", "UNKNOWN"):
                hard_conflict = True
        if conflict_notes:
            gaps.extend(f"user requirement is not satisfiable: {note}" for note in conflict_notes)
        obligations_complete = (not goal.obligations) or not missing_obligations
        satisfied = [item for item in assessments if item.verdict == "SATISFIED"]
        # A closed core Need does not by itself block COMPLETE when the frozen obligations
        # are independently verified by other accepted work; otherwise it does. This is
        # deterministic (durable outcome + obligation coverage), not planner self-approval.
        recoverable = bool(goal.obligations) and obligations_complete and bool(satisfied)
        if recoverable:
            missing = [item for item in missing if item not in closed]
        core_supported = (bool(core) and not missing and obligations_complete
                          and not hard_conflict)
        quality = (sum(item.evidence_quality for item in satisfied) / len(satisfied)
                   if satisfied else 0.0)
        judge_available = all(item.assessment_available for item in assessments)
        if core_supported:
            verdict: CoverageVerdict = "SATISFIED"
        elif satisfied or any(item.verdict == "PARTIAL" for item in assessments):
            verdict = "PARTIAL"
        else:
            verdict = "UNSATISFIED"
        accepted = tuple(dict.fromkeys(
            artifact_id for item in assessments if item.verdict == "SATISFIED"
            for artifact_id in item.supported_claims))
        return GoalCoverage(core_goal_supported=core_supported, verdict=verdict,
                            quality=round(quality, 4),
                            gaps=tuple(dict.fromkeys(gaps)),
                            missing_needs=tuple(missing),
                            obligation_coverage=coverage,
                            missing_obligations=tuple(missing_obligations),
                            conflicts=tuple(conflict_notes),
                            judge_available=judge_available,
                            accepted_artifact_ids=accepted)
