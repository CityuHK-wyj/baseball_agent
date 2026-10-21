"""Small, bounded lifecycle policy. No evidence truth or planning semantics live here."""
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from app.contracts.models import CoverageState, GoalStatus, ConsistencyStatus, StatusConsistency
from app.response.answer_contract import AnswerPackage, render_governed_package
from app.verification.composition_conformance import conform_composition
from app.verification.consistency import check_status_consistency, repair_answer
from app.verification.governed_state import GovernedStateError, ExpectedObligations, validate_governed_projection


class OrchestratorAction(str, Enum):
    FINAL = "FINAL"
    REGENERATE_COMPOSER = "REGENERATE_COMPOSER"
    REPLAN = "REPLAN"
    FALLBACK = "FALLBACK"


@dataclass(frozen=True)
class OrchestratorDecision:
    action: OrchestratorAction
    reason: str
    attempt: int
    violations: tuple[str, ...] = ()
    package_id: str = ""


@dataclass
class Orchestrator:
    # Hard bounds, independently accounted rather than collapsed into one scalar.
    max_composer_attempts: int = 2
    max_post_judge_replans: int = 1
    decisions: list[OrchestratorDecision] = field(default_factory=list)
    composer_attempts: int = 0
    conformance_attempts: int = 0
    post_judge_replans: int = 0
    judge_attempts: int = 0
    governed_retries: int = 0
    # The last integrity-violating projection, kept only for the bounded governed fallback.
    last_invalid_projection: object | None = None
    expected_obligations: ExpectedObligations | None = None

    def __post_init__(self):
        self.max_composer_attempts = min(2, max(1, self.max_composer_attempts))
        self.max_post_judge_replans = min(1, max(0, self.max_post_judge_replans))

    def evaluate(self, evaluate: Callable, *, goal=None):
        """A structural upstream inconsistency permits one NEW Judge pass, never mutation."""
        if goal is not None and self.expected_obligations is None:
            self.expected_obligations = ExpectedObligations.from_goal(goal)
        while True:
            self.judge_attempts += 1
            projection = None
            try:
                projection = evaluate()
                validate_governed_projection(projection,expected=self.expected_obligations)
            except GovernedStateError as exc:
                self.last_invalid_projection = projection if projection is not None else exc.projection
                if self.governed_retries >= 1 or self.last_invalid_projection is None:
                    raise
                self.governed_retries += 1
                continue
            return projection if projection.is_governed_frozen else projection.frozen_snapshot()

    def post_judge_decision(self, projection, *, mandatory_requirement_ids,
                            reasonable_path: bool, budget_remaining: bool):
        unresolved = [a for a in projection.requirement_assessments
                      if a.requirement_id in mandatory_requirement_ids
                      and a.coverage != CoverageState.SATISFIED]
        if projection.goal_status == GoalStatus.WAITING_FOR_USER:
            reason = "backend clarification required"
        elif not unresolved:
            reason = "no unresolved mandatory Requirement"
        elif not budget_remaining:
            reason = "acquisition budget exhausted"
        elif not reasonable_path:
            reason = "no materially new acquisition path"
        elif self.post_judge_replans >= self.max_post_judge_replans:
            reason = "post-Judge replan limit exhausted"
        else:
            self.post_judge_replans += 1
            decision = OrchestratorDecision(OrchestratorAction.REPLAN,
                "unresolved mandatory Requirement with available acquisition path",
                self.post_judge_replans)
            self.decisions.append(decision)
            return decision
        decision = OrchestratorDecision(OrchestratorAction.FINAL, reason, self.post_judge_replans)
        self.decisions.append(decision)
        return decision

    def enforce_consistency(self, goal, projection, package, answer, *, language="en"):
        """Presentation-only repair; never attach telemetry to the frozen projection."""
        consistency = check_status_consistency(goal, projection, answer)
        if consistency.status == ConsistencyStatus.CONSISTENT:
            return answer, consistency
        repaired = repair_answer(projection, answer, consistency.retracted_proposition_ids, language=language)
        if repaired != answer:
            recheck = check_status_consistency(goal, projection, repaired)
            if recheck.status == ConsistencyStatus.CONSISTENT:
                return repaired, StatusConsistency(
                    status=ConsistencyStatus.CONSISTENT,
                    original_goal_status=projection.goal_status, final_goal_status=projection.goal_status,
                    retracted_proposition_ids=list(consistency.retracted_proposition_ids),
                    method="deterministic_repair",
                    rationale="unconfirmed expression was replaced with bounded accepted mandatory results",
                )
            consistency = recheck
        self.decisions.append(OrchestratorDecision(OrchestratorAction.FALLBACK,
            "presentation consistency could not be established; governed state unchanged",
            self.composer_attempts, package_id=package.package_id))
        return render_governed_package(package), consistency

    def compose(self, package: AnswerPackage, generate_draft: Callable | None,
                assess_draft: Callable, *, language="en") -> str:
        """Composer and Conformance independently receive the identical frozen package.

        Violation feedback is bounded typed metadata, not a failed draft promoted to input
        authority. Conformance never gets a Planner callback and cannot acquire anything.
        """
        if generate_draft is None:
            self.decisions.append(OrchestratorDecision(OrchestratorAction.FALLBACK,
                "Composer unavailable or no composable governed content", 0, package_id=package.package_id))
            return render_governed_package(package)
        feedback = ()
        for attempt in range(1, self.max_composer_attempts + 1):
            self.composer_attempts += 1
            try:
                draft = generate_draft(package, feedback)
            except Exception:  # provider failure is an attempted call, not success
                draft = None
            violations = ("COMPOSER_UNAVAILABLE",)
            if draft is not None:
                self.conformance_attempts += 1
                try:
                    assessment = assess_draft(package, draft)
                except Exception:
                    assessment = None
                conformance = conform_composition(draft, package, assessment, language=language)
                violations = tuple(dict.fromkeys(v.kind for v in conformance.violations))
                if not violations and conformance.text:
                    self.decisions.append(OrchestratorDecision(OrchestratorAction.FINAL,
                        "draft faithfully represents governed package", attempt, package_id=package.package_id))
                    return conformance.text
            action = (OrchestratorAction.REGENERATE_COMPOSER
                      if attempt < self.max_composer_attempts else OrchestratorAction.FALLBACK)
            reason = "presentation violations" if action == OrchestratorAction.REGENERATE_COMPOSER else "Composer attempt limit exhausted"
            self.decisions.append(OrchestratorDecision(action, reason, attempt,
                                                       violations[:16], package.package_id))
            feedback = violations[:16]
        return render_governed_package(package)
