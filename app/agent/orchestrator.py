"""Orchestrator: scheduling, budgets, terminal latch and finalization.

It is a workflow manager, not a domain expert. It does not decompose requirements,
interpret metrics, choose sources, judge evidence or write baseball conclusions.
"""

from collections.abc import Callable

from app.agent.executor import ExecutionOutcome, Executor
from app.agent.planner import Planner, PlannerContext, PlannerTerminalLatch
from app.agent.registry import ArtifactRegistry
from app.agent.response import build_response_package
from app.agent.routing import Router
from app.assessment.service import AssessmentService
from app.models.artifacts import ArtifactAssessment, ArtifactContract
from app.models.contracts import (AnalysisObjective, ArtifactRequirement, ObjectiveState,
                                  RequirementState)
from app.models.planning import PlanningDecision, RoutingDecision
from app.models.reports import (CompletionReport, ExecutionSummary, RequirementCompletion,
                                ResponsePackage)
from app.state.services import (derive_objective_state, derive_requirement_state,
                                unmet_core_requirements)


class RunResult(ArtifactContract):
    """Orchestrator-internal run diagnostics. Not the Response Agent's input."""

    run_id: str
    completion_report: CompletionReport
    response_package: ResponsePackage
    objective_state: ObjectiveState
    requirement_states: tuple[RequirementState, ...] = ()
    assessments: tuple[ArtifactAssessment, ...] = ()
    planning_decisions: tuple[PlanningDecision, ...] = ()
    routing_decisions: tuple[RoutingDecision, ...] = ()
    executions: tuple[ExecutionOutcome, ...] = ()
    retrieved_artifacts: tuple[str, ...] = ()


class Orchestrator:
    def __init__(self, planner: Planner, router: Router, executor: Executor,
                 assessment_service: AssessmentService, registry: ArtifactRegistry,
                 max_rounds: int = 3, budget: int = 10,
                 id_factory: Callable[[str], str] | None = None,
                 permitted_sources: tuple[str, ...] = ()) -> None:
        if max_rounds < 1 or budget < 1:
            raise ValueError("max_rounds and budget must be positive")
        self._planner = planner
        self._router = router
        self._executor = executor
        self._assessment_service = assessment_service
        self._registry = registry
        self._max_rounds = max_rounds
        self._budget = budget
        self._permitted_sources = permitted_sources
        counter = iter(range(1, 10_000))
        self._id_factory = id_factory or (lambda prefix: f"{prefix}-{next(counter)}")
        self._latch = PlannerTerminalLatch()

    def _external_condition(self) -> tuple:
        return (tuple(sorted(item.artifact_id for item in self._registry.artifacts())),
                self._permitted_sources)

    def run(self, objective: AnalysisObjective,
            initial_requirements: tuple[ArtifactRequirement, ...],
            confirmed_intent: str = "") -> RunResult:
        from app.models.requirements import RequirementCatalog
        catalog = RequirementCatalog((objective,), initial_requirements)
        requirements = tuple(catalog.initial_requirements) + tuple(catalog.supporting_requirements)
        states = {state.requirement_ref: state for state in catalog.requirement_states}
        by_id = {item.requirement_id: item for item in requirements}

        run_id = self._id_factory("run")
        self._latch = PlannerTerminalLatch()
        decisions: list[PlanningDecision] = []
        routings: list[RoutingDecision] = []
        executions: list[ExecutionOutcome] = []
        assessments: list[ArtifactAssessment] = []
        round_index = 0
        budget = self._budget
        tasks_planned = 0
        stop_reason: str | None = None

        while True:
            condition = self._external_condition()
            if not self._latch.may_invoke(condition):
                stop_reason = self._latch.reason
                break
            unmet = unmet_core_requirements(requirements, states)
            recoverable = tuple(
                item.requirement_id for item in unmet
                if self._router.eligible_sources(item.descriptor.artifact_type, self._permitted_sources))
            policy_blocked = tuple(
                item.requirement_id for item in unmet
                if item.requirement_id not in recoverable
                and self._router.candidate_sources(item.descriptor.artifact_type))
            context = PlannerContext(
                objective=objective, requirements=requirements,
                requirement_states=tuple(states.values()), artifact_index=self._registry.index(),
                assessment_summaries=self._assessment_service.summaries(), round=round_index,
                max_rounds=self._max_rounds, budget_remaining=budget,
                recoverable_gaps=recoverable, policy_blocked_gaps=policy_blocked,
                prior_plan_count=len(decisions), planner_terminal=self._latch.latched)
            decision = self._planner.decide(context)
            decisions.append(decision)
            if decision.planner_terminal:
                self._latch.latch(decision.terminal_reason, condition)
                stop_reason = decision.terminal_reason
                break

            tasks_planned += len(decision.tasks)
            new_artifact_ids: list[str] = []
            for task in decision.tasks:
                if budget <= 0:
                    break
                requirement = by_id[task.requirement_refs[0]]
                routing = self._router.route(task, requirement.descriptor.artifact_type,
                                             self._permitted_sources)
                routings.append(routing)
                outcome = self._executor.run(task, routing)
                executions.append(outcome)
                budget -= 1
                if outcome.artifact is not None:
                    if outcome.artifact.artifact_id not in self._registry:
                        new_artifact_ids.append(outcome.artifact.artifact_id)
                    self._registry.register(outcome.artifact)
                    assessments.append(self._assessment_service.assess(
                        outcome.artifact.artifact_id, requirement, objective.objective_id))

            for item in requirements:
                states[item.requirement_id] = derive_requirement_state(
                    item.requirement_id,
                    self._assessment_service.assessments_for(item.requirement_id),
                    states[item.requirement_id])
            round_index += 1

            if not new_artifact_ids:
                stop_reason = "NO_PROGRESS"
                self._latch.latch(stop_reason, self._external_condition())
                break

        objective_state = derive_objective_state(
            objective.objective_id, requirements, states, planner_terminal=True)
        execution_summary = ExecutionSummary(
            rounds=round_index, tasks_planned=tasks_planned,
            tasks_succeeded=sum(1 for item in executions if item.execution.status == "SUCCEEDED"),
            tasks_empty=sum(1 for item in executions if item.execution.status == "EMPTY"),
            tasks_failed=sum(1 for item in executions if item.execution.status == "FAILED"),
            attempts=sum(len(item.attempts) for item in executions),
            budget_spent=self._budget - max(budget, 0))
        accepted_ids = tuple(
            dict.fromkeys(item.artifact_ref for item in assessments if item.accepted))
        completion = CompletionReport(
            run_id=run_id, query=objective.raw_query, confirmed_intent=confirmed_intent,
            objective_ref=objective.objective_id, objective_status=objective_state.status,
            requirement_completion=tuple(
                RequirementCompletion(requirement_ref=item.requirement_id,
                                      criticality=item.base_criticality, origin=item.origin,
                                      status=states[item.requirement_id].status)
                for item in requirements),
            final_artifact_refs=accepted_ids,
            limitations=tuple(dict.fromkeys(
                limitation for item in assessments if item.accepted
                for limitation in item.limitations)),
            unresolved_gaps=tuple(item.requirement_id for item in unmet_core_requirements(requirements, states)),
            plan_revisions=sum(1 for item in decisions if item.kind in ("PLAN", "REPLAN")),
            execution_summary=execution_summary,
            stop_reason=stop_reason)
        response = build_response_package(run_id, objective_state, requirements, states,
                                          self._assessment_service, self._registry)
        return RunResult(
            run_id=run_id, completion_report=completion, response_package=response,
            objective_state=objective_state, requirement_states=tuple(states.values()),
            assessments=tuple(assessments), planning_decisions=tuple(decisions),
            routing_decisions=tuple(routings), executions=tuple(executions),
            retrieved_artifacts=tuple(item.artifact_id for item in self._registry.artifacts()))


def run_all_channel_baseball_agent(user_prompt: str, config: object = None) -> str:
    """Legacy entry point. Requires explicitly injected, validated dependencies."""
    raise RuntimeError(
        "The validated planning/response loop is not yet wired to live sources; "
        "legacy execution remains disabled.")
