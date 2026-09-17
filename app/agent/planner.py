"""Semantic planning: PLAN / REPLAN / STOP_PLANNING.

The Planner decides *what* to do next. It never selects a physical source (Router)
and never edits an Initial Requirement; it may only propose new supporting ones.
"""

from typing import Callable, Protocol

from pydantic import Field

from app.context.service import ContextItem
from app.models.artifacts import ArtifactContract, ArtifactIndexEntry, AssessmentSummary
from app.models.contracts import (AnalysisObjective, ArtifactDescriptor, ArtifactRequirement,
                                  RequirementState)
from app.models.planning import AgentTask, PlanningDecision, SupportingNeed
from app.models.reports import ExecutionSummary
from app.models.understanding import SemanticUnderstanding


def supporting_requirement(need: SupportingNeed) -> ArtifactRequirement:
    """Materialize a Planner-created need into a PLANNER_ADDED (never CORE) requirement.

    Supporting requirements cannot raise the original objective's completion threshold;
    they are executed because the same planning decision carries a task for them.
    """
    return ArtifactRequirement(
        requirement_id=f"supporting-{need.need_id}",
        objective_ref=need.objective_ref,
        description=need.description,
        descriptor=ArtifactDescriptor(
            artifact_type=need.artifact_type,
            data_keys=need.data_keys,
            granularity="event" if need.artifact_type == "EVIDENCE" else "record",
            population_scope="web" if need.artifact_type == "EVIDENCE" else "league"),
        origin="PLANNER_ADDED",
        base_criticality="OPTIONAL",
        parent_ref=need.parent_ref,
        evidence_purpose="EXISTENCE" if need.artifact_type == "EVIDENCE" else "DESCRIPTIVE")


def task_type_for(requirement: ArtifactRequirement) -> str:
    """Deterministic task class from the requirement's artifact shape.

    The class tells the Router/Executor which broad tool family is appropriate without
    collapsing the task into an enum of fixed metric values.
    """
    if requirement.descriptor.artifact_type == "EVIDENCE":
        keys = set(requirement.descriptor.data_keys)
        if keys & {"injury_status", "salary", "news_claim", "knowledge_statement"}:
            return "KNOWLEDGE"
        return "WEB_RESEARCH"
    if requirement.descriptor.artifact_type == "FEATURE":
        return "COMPUTATION"
    return "LOCAL_ANALYTICS"


def _task_objective(requirement: ArtifactRequirement,
                    understanding: SemanticUnderstanding | None) -> str:
    keys = ", ".join(requirement.descriptor.data_keys)
    base = f"Obtain {keys} for: {requirement.description}"
    if understanding is not None and understanding.analysis_strategy:
        return f"{base}. Strategy: {understanding.analysis_strategy}"
    return base


class PlannerContext(ArtifactContract):
    """Bounded, payload-free view. Raw payloads expand through Shared Context.

    The Planner now also receives the raw query and the open-world semantic
    understanding so the semantic layer is an interpretation aid, not an information
    bottleneck. Already-confirmed explicit user constraints still outrank free-form
    interpretation.
    """

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
    execution_summary: ExecutionSummary = Field(default_factory=ExecutionSummary)
    context_items: tuple[ContextItem, ...] = ()
    prior_plan_count: int = Field(default=0, ge=0)
    planner_terminal: bool = False
    terminal_reason: str = ""
    raw_query: str = ""
    understanding: SemanticUnderstanding | None = None
    # True when a web research tool is actually wired into this run. The Planner may then
    # dynamically create a web-research supporting requirement.
    web_recovery_available: bool = False
    # Structured recovery signals from failed/empty tool attempts (for example
    # UNSUPPORTED_LOCAL_ANALYTICS). UNKNOWN != FAILED: these guide re-planning.
    recovery_signals: tuple[str, ...] = ()


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
            # Unknown is a routing signal: if the semantic layer could not resolve a
            # concept and web research is wired in, create a supporting requirement.
            if (context.web_recovery_available and context.understanding is not None
                    and context.understanding.has_unresolved):
                need_id = self._id_factory("need")
                need = SupportingNeed(
                    need_id=need_id, objective_ref=context.objective.objective_id,
                    description="Resolve the unresolved mention(s) with web research",
                    artifact_type="EVIDENCE", data_keys=("entity_alias",),
                    task_type="ENTITY_RESOLUTION",
                    objective="Identify the entity/nickname referenced by the user: "
                              + ", ".join(context.understanding.unresolved_concepts
                                           or context.understanding.candidate_entity_mentions),
                    instructions=context.understanding.planner_notes,
                    expected_evidence="A sourced statement identifying the entity.",
                    search_hints=context.understanding.search_hints)
                requirement_id = f"supporting-{need_id}"
                task = AgentTask(
                    task_id=self._id_factory("task"), objective_ref=context.objective.objective_id,
                    requirement_refs=(requirement_id,),
                    description="Web/entity-resolution research",
                    task_type="ENTITY_RESOLUTION", objective=need.objective,
                    instructions=need.instructions, expected_evidence=need.expected_evidence,
                    search_hints=need.search_hints)
                return PlanningDecision(
                    decision_id=self._id_factory("decision"),
                    objective_ref=context.objective.objective_id,
                    kind="PLAN" if context.prior_plan_count == 0 else "REPLAN",
                    tasks=(task,), supporting_needs=(need,),
                    rationale="Create a web-research supporting requirement for an "
                              "unresolved entity/nickname.", round=context.round)
            return stop("NO_RECOVERABLE_PATH", "No unmet core requirement still has a recoverable data path.")

        tasks = tuple(
            AgentTask(
                task_id=self._id_factory("task"), objective_ref=context.objective.objective_id,
                requirement_refs=(item.requirement_id,),
                description=f"Obtain {', '.join(item.descriptor.data_keys)} for {item.requirement_id}",
                source_preference=None,
                task_type=task_type_for(item),
                objective=_task_objective(item, context.understanding),
                instructions=(context.understanding.planner_notes
                              if context.understanding is not None else ""),
                search_hints=(context.understanding.search_hints
                              if context.understanding is not None else ()))
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

    @property
    def condition(self) -> object | None:
        return self._condition

    def latch(self, reason: str, condition: object) -> None:
        self._reason = reason
        self._condition = condition

    def observe(self, condition: object) -> None:
        if self.latched and condition != self._condition:
            self._reason = None
            self._condition = None

    def may_invoke(self, condition: object) -> bool:
        return not self.latched or condition != self._condition
