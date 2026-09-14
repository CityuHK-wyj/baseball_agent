"""Semantic planning: PLAN / REPLAN / STOP_PLANNING.

The Planner decides *what* to do next. It never selects a physical source (Router)
and never edits an Initial Requirement; it may only propose new supporting ones.
"""

from typing import Callable, Protocol

from pydantic import Field

from app.models.artifacts import ArtifactContract, ArtifactIndexEntry, AssessmentSummary
from app.models.contracts import AnalysisObjective, ArtifactRequirement, RequirementState
from app.models.planning import AgentTask, PlanningDecision


class PlannerContext(ArtifactContract):
    """Bounded, payload-free view. Raw payloads expand through Shared Context."""

    objective: AnalysisObjective
    requirements: tuple[ArtifactRequirement, ...] = Field(min_length=1)
    requirement_states: tuple[RequirementState, ...] = ()
    artifact_index: tuple[ArtifactIndexEntry, ...] = ()
    assessment_summaries: tuple[AssessmentSummary, ...] = ()
    round: int = Field(default=0, ge=0)
    max_rounds: int = Field(default=3, ge=1)
    budget_remaining: int = Field(default=10, ge=0)
    recoverable_gaps: tuple[str, ...] = ()
    policy_blocked_gaps: tuple[str, ...] = ()
    prior_plan_count: int = Field(default=0, ge=0)
    planner_terminal: bool = False
    terminal_reason: str = ""


class Planner(Protocol):
    def decide(self, context: PlannerContext) -> PlanningDecision: ...


class RuleBasedPlanner:
    """Deterministic Planner used until an LLM Planner is justified and testable."""

    def __init__(self, id_factory: Callable[[str], str] | None = None, max_rounds: int = 3) -> None:
        self._id_factory = id_factory or (lambda prefix: f"{prefix}-{id(object())}")
        self._max_rounds = max_rounds

    def decide(self, context: PlannerContext) -> PlanningDecision:
        if context.planner_terminal:
            raise RuntimeError("Planner was invoked after a terminal decision without new external conditions")

        states = {state.requirement_ref: state for state in context.requirement_states}
        unmet = tuple(
            item for item in context.requirements
            if item.base_criticality == "CORE" and states[item.requirement_id].status != "SATISFIED"
        )

        def stop(reason: str, rationale: str) -> PlanningDecision:
            return PlanningDecision(
                decision_id=self._id_factory("decision"), objective_ref=context.objective.objective_id,
                kind="STOP_PLANNING", planner_terminal=True, terminal_reason=reason,
                rationale=rationale, round=context.round)

        if not unmet:
            return stop("COMPLETE", "Every core Initial Requirement is satisfied.")
        if context.round >= max(context.max_rounds, self._max_rounds):
            return stop("MAX_ROUNDS", "The maximum planning round count is reached.")
        if context.budget_remaining <= 0:
            return stop("BUDGET_EXHAUSTED", "No execution budget remains.")
        recoverable = tuple(item for item in unmet if item.requirement_id in context.recoverable_gaps)
        if not recoverable:
            if context.policy_blocked_gaps:
                return stop("POLICY_BLOCKED", "Every remaining data path is blocked by policy or permissions.")
            return stop("NO_RECOVERABLE_PATH", "No unmet core requirement still has a recoverable data path.")

        tasks = tuple(
            AgentTask(
                task_id=self._id_factory("task"), objective_ref=context.objective.objective_id,
                requirement_refs=(item.requirement_id,),
                description=f"Obtain {', '.join(item.descriptor.data_keys)} for {item.requirement_id}",
                source_preference=None)
            for item in recoverable
        )
        return PlanningDecision(
            decision_id=self._id_factory("decision"), objective_ref=context.objective.objective_id,
            kind="PLAN" if context.prior_plan_count == 0 else "REPLAN", tasks=tasks,
            rationale=f"{len(tasks)} task(s) target the unmet core requirements.", round=context.round)


class PlannerTerminalLatch:
    """Guards against re-invoking a terminal Planner without a new external condition.

    The external condition is an opaque hashable fingerprint of things outside the
    Planner's own reasoning: new artifacts, new sources, new permissions, changed
    user constraints. Internal round counters do not clear the latch.
    """

    def __init__(self) -> None:
        self._reason: str | None = None
        self._condition: object | None = None

    @property
    def latched(self) -> bool:
        return self._reason is not None

    @property
    def reason(self) -> str | None:
        return self._reason

    def latch(self, reason: str, condition: object) -> None:
        self._reason = reason
        self._condition = condition

    def observe(self, condition: object) -> None:
        if self.latched and condition != self._condition:
            self._reason = None
            self._condition = None

    def may_invoke(self, condition: object) -> bool:
        return not self.latched or condition != self._condition
