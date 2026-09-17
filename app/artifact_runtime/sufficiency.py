"""Coverage / sufficiency judgement.

Completion depends on Goal coverage, never on "some evidence exists". Deterministic
scope comparison supplies channel scores and blocking gaps; an optional judge hook may
refine a verdict downward but can never upgrade evidence past a deterministic scope gap.

Terminal semantics:

* COMPLETE  : core Goal supported by sufficiently relevant accepted evidence.
* LIMITED   : a useful bounded answer exists but important scope/data gaps remain.
* WAITING_FOR_USER : user input is materially necessary.
* FAILED    : no useful supported answer remains after reasonable recovery.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable

from app.models.artifact_runtime import (CoverageAssessment, CoverageVerdict, Goal, Need,
                                         RuntimeArtifact)
from app.artifact_runtime.scope import ScopeComparison, compare_scope

JudgeHook = Callable[[Goal, Need, RuntimeArtifact], tuple[str, float, tuple[str, ...]]]


@dataclass(frozen=True)
class GoalCoverage:
    core_goal_supported: bool
    verdict: CoverageVerdict
    quality: float = 0.0
    gaps: tuple[str, ...] = field(default_factory=tuple)
    missing_needs: tuple[str, ...] = field(default_factory=tuple)


_ACCEPTED_STATUSES = ("OK", "PARTIAL")


class CoverageJudge:
    def __init__(self, judge_hook: JudgeHook | None = None, *,
                 satisfied_threshold: float = 0.55, quality_floor: float = 0.35) -> None:
        self._hook = judge_hook
        self._satisfied_threshold = satisfied_threshold
        self._quality_floor = quality_floor

    def assess_need(self, goal: Goal, need: Need,
                    artifacts: tuple[RuntimeArtifact, ...]) -> CoverageAssessment:
        candidates = [item for item in artifacts if item.artifact_id in need.linked_artifacts]
        usable = [item for item in candidates if item.status in _ACCEPTED_STATUSES]
        comparisons: list[tuple[RuntimeArtifact, ScopeComparison]] = [
            (item, compare_scope(need.required_scope, item.actual_scope, artifact=item))
            for item in usable]
        if comparisons:
            artifact, best = max(comparisons, key=lambda pair: pair[1].aggregate)
        else:
            artifact, best = None, ScopeComparison(entity=0.0, temporal=0.0,
                                                    population=0.0, measure=0.0,
                                                    quality=0.0,
                                                    gaps=("no accepted artifact advances "
                                                          "this need",))
        invalid = [item for item in candidates if item.status == "INVALID"]
        gaps = list(best.gaps)
        reasons = list(best.reasons)
        if invalid:
            gaps.append("an analytical attempt was rejected by the safety boundary")

        verdict: CoverageVerdict
        if artifact is None:
            verdict = "UNSATISFIED"
        elif best.blocking or best.quality < self._quality_floor:
            verdict = "IRRELEVANT" if best.aggregate < 0.25 else "PARTIAL"
        elif best.aggregate >= self._satisfied_threshold and artifact.status == "OK":
            verdict = "SATISFIED"
        else:
            verdict = "PARTIAL"

        # A judge hook may refine downward; it can never erase a deterministic gap.
        if self._hook is not None and artifact is not None and verdict in ("PARTIAL",):
            try:
                hook_verdict, confidence, hook_reasons = self._hook(goal, need, artifact)
                if hook_verdict in ("PARTIAL", "UNSATISFIED", "IRRELEVANT") and \
                        self._verdict_rank(hook_verdict) < self._verdict_rank(verdict):
                    verdict = hook_verdict  # type: ignore[assignment]
                reasons.extend(hook_reasons)
                if confidence > best.quality:
                    best = replace(best, quality=confidence)
            except Exception:  # noqa: BLE001 - a judge outage must not block the runtime
                reasons.append("judge hook unavailable; deterministic scope comparison used")

        supported = (artifact.artifact_id,) if verdict == "SATISFIED" and artifact else ()
        return CoverageAssessment(
            assessment_id=f"coverage-{goal.goal_id}-{need.need_id}",
            goal_id=goal.goal_id, need_id=need.need_id,
            entity_coverage=best.entity, temporal_coverage=best.temporal,
            population_coverage=best.population, measure_coverage=best.measure,
            evidence_quality=best.quality, supported_claims=supported,
            missing_needs=() if verdict == "SATISFIED" else (need.need_id,),
            core_goal_supported=verdict == "SATISFIED" and need.criticality == "CORE",
            verdict=verdict, reasons=tuple(reasons), gaps=tuple(dict.fromkeys(gaps)))

    @staticmethod
    def _verdict_rank(verdict: str) -> int:
        return {"IRRELEVANT": 0, "UNSATISFIED": 1, "PARTIAL": 2, "SATISFIED": 3}.get(verdict, 0)

    def summarize(self, goal: Goal, needs: tuple[Need, ...],
                  assessments: tuple[CoverageAssessment, ...]) -> GoalCoverage:
        core = [need for need in needs if need.criticality == "CORE"]
        by_need = {item.need_id: item for item in assessments}
        missing = [need.need_id for need in core
                   if by_need.get(need.need_id, None) is None
                   or by_need[need.need_id].verdict != "SATISFIED"]
        gaps: list[str] = []
        for need in needs:
            assessment = by_need.get(need.need_id)
            if assessment is not None:
                gaps.extend(assessment.gaps)
                if assessment.verdict != "SATISFIED":
                    gaps.append(f"{need.need_id} is {assessment.verdict.lower()}")
        core_supported = bool(core) and not missing
        satisfied = [item for item in assessments if item.verdict == "SATISFIED"]
        quality = (sum(item.evidence_quality for item in satisfied) / len(satisfied)
                   if satisfied else 0.0)
        if core_supported:
            verdict: CoverageVerdict = "SATISFIED"
        elif satisfied or any(item.verdict == "PARTIAL" for item in assessments):
            verdict = "PARTIAL"
        else:
            verdict = "UNSATISFIED"
        return GoalCoverage(core_goal_supported=core_supported, verdict=verdict,
                            quality=round(quality, 4),
                            gaps=tuple(dict.fromkeys(gaps)),
                            missing_needs=tuple(missing))
