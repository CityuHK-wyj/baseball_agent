"""State projection: the single owner of Need SATISFIED/PARTIAL and Goal terminal truth.

Planner and Judge do not mutate terminal truth directly. The projector combines the frozen
user obligations, the Need graph, the Artifacts, deterministic verification, the
independent Judge assessment and the durable ToolOutcome, then decides:

* Need: SATISFIED / PARTIAL / BLOCKED / FAILED
* Goal: COMPLETE / LIMITED / FAILED / WAITING_FOR_USER

``COMPLETE`` requires all core user obligations adequately covered. ``LIMITED`` requires
at least one useful accepted claim *plus* explicit material gaps; an arbitrary OK Artifact
is not enough.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.agent_runtime import RunStatus
from app.models.artifact_runtime import Claim, CoverageAssessment, Goal, Need
from app.artifact_runtime.sufficiency import GoalCoverage

_NEED_BY_VERDICT = {
    "SATISFIED": "SATISFIED",
    "PARTIAL": "PARTIAL",
    "IRRELEVANT": "PARTIAL",
    "UNSATISFIED": "FAILED",
    "UNAVAILABLE": "PARTIAL",
}


@dataclass(frozen=True)
class GoalState:
    status: RunStatus
    coverage: GoalCoverage
    reason: str = ""


class StateProjector:
    def project_need(self, need: Need, assessment: CoverageAssessment | None,
                     *, has_attempt: bool) -> str:
        if assessment is None:
            return "IN_PROGRESS" if has_attempt else "OPEN"
        return _NEED_BY_VERDICT.get(assessment.verdict, "PARTIAL")

    def project_goal(self, coverage: GoalCoverage, claims: tuple[Claim, ...],
                     accepted_artifact_ids: tuple[str, ...]) -> GoalState:
        useful = bool(claims) or bool(accepted_artifact_ids)
        if coverage.core_goal_supported and claims:
            return GoalState("COMPLETE", coverage,
                             "all core obligations covered by verified, judged evidence")
        if useful:
            return GoalState("LIMITED", coverage,
                             "a useful bounded answer exists but material gaps remain")
        return GoalState("FAILED", coverage, "no supported answer remains after recovery")

    def project_waiting(self, coverage: GoalCoverage | None = None) -> GoalState:
        return GoalState("WAITING_FOR_USER",
                         coverage or GoalCoverage(False, "UNSATISFIED"),
                         "user input is materially necessary")
