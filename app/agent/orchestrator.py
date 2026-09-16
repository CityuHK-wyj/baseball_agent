"""Orchestrator: scheduling, budgets, terminal latch and finalization.

It is a workflow manager, not a domain expert. It does not decompose requirements,
interpret metrics, choose sources, judge evidence or write baseball conclusions.

Persistence is optional. When a ``RunRecorder`` is injected, products are recorded in
safe order (payload, then artifact metadata, assessment, states, execution references,
checkpoint) so that no state ever references an artifact that failed to persist.
"""

from collections.abc import Callable
from time import monotonic

from app.agent.executor import ExecutionOutcome, Executor
from app.agent.planner import Planner, PlannerContext, PlannerTerminalLatch
from app.agent.registry import ArtifactRegistry
from app.agent.response import build_response_package
from app.agent.routing import Router
from app.agent.source_mapping import SourceMappingResolver
from app.assessment.service import AssessmentService
from app.context.service import ContextRequest, ContextService
from app.models.artifacts import ArtifactAssessment, ArtifactContract
from app.models.contracts import (AnalysisObjective, ArtifactRequirement, ObjectiveState,
                                  RequirementState)
from app.models.planning import PlanningDecision, RoutingDecision
from app.models.reports import (CompletionReport, ExecutionSummary, RequirementCompletion,
                                ResponsePackage)
from app.observability.metrics import RunEvent, RunMetrics
from app.persistence.recorder import RunRecorder
from app.persistence.resume import RestoredRun
from app.state.services import (derive_objective_state, derive_requirement_state,
                                unmet_core_requirements)

# Knowledge kinds a Planner or Response may receive through Shared Context. History
# (attempts, routing, drafts, judge reasoning, rejected evidence, unused RAG) is never
# a knowledge kind and is structurally excluded by ContextService.
KNOWLEDGE_KINDS: tuple[str, ...] = ("METRIC", "SCHEMA", "SOURCE_MAPPING", "REFERENCE",
                                    "KNOWLEDGE", "COMPLETION_REPORT")
MAX_CONTEXT_ITEMS = 8


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
    metrics: tuple[RunEvent, ...] = ()


class Orchestrator:
    def __init__(self, planner: Planner, router: Router, executor: Executor,
                 assessment_service: AssessmentService, registry: ArtifactRegistry,
                 max_rounds: int = 3, budget: int = 10,
                 id_factory: Callable[[str], str] | None = None,
                 permitted_sources: tuple[str, ...] = (),
                 recorder: RunRecorder | None = None,
                 context_service: ContextService | None = None,
                 source_mapping_resolver: SourceMappingResolver | None = None,
                 metrics_factory: Callable[[str], RunMetrics] | None = None,
                 run_id: str | None = None) -> None:
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
        self._recorder = recorder
        self._context_service = context_service
        self._source_mapping_resolver = source_mapping_resolver
        self._metrics_factory = metrics_factory or (
            lambda run_id: RunMetrics(run_id, sink=recorder.record_metric if recorder else None))
        self._fixed_run_id = run_id
        counter = iter(range(1, 10_000))
        self._id_factory = id_factory or (lambda prefix: f"{prefix}-{next(counter)}")
        self._latch = PlannerTerminalLatch()

    def _external_condition(self) -> tuple:
        return (tuple(sorted(item.artifact_id for item in self._registry.artifacts())),
                self._permitted_sources)

    def _execution_summary(self, executions: list[ExecutionOutcome], round_index: int,
                           tasks_planned: int, budget: int) -> ExecutionSummary:
        return ExecutionSummary(
            rounds=round_index, tasks_planned=tasks_planned,
            tasks_succeeded=sum(1 for item in executions if item.execution.status == "SUCCEEDED"),
            tasks_empty=sum(1 for item in executions if item.execution.status == "EMPTY"),
            tasks_failed=sum(1 for item in executions if item.execution.status == "FAILED"),
            attempts=sum(len(item.attempts) for item in executions),
            budget_spent=self._budget - max(budget, 0))

    def _retrieve_context(self, request: ContextRequest) -> tuple:
        if self._context_service is None:
            return ()
        return self._context_service.retrieve(request).items

    def _apply_restored(self, restored: RestoredRun, states: dict[str, RequirementState]) -> None:
        """Rehydrate persisted domains. Each domain keeps its own representation."""
        for artifact in restored.artifacts:
            if artifact.artifact_id not in self._registry:
                self._registry.register(artifact)
        for assessment in restored.assessments:
            self._assessment_service.restore(assessment)
        for state in restored.requirement_states:
            if state.requirement_ref in states:
                states[state.requirement_ref] = state
        if restored.planner_terminal:
            complete = restored.objective_state is not None and restored.objective_state.status == "COMPLETE"
            reason = "COMPLETE" if complete else "NO_RECOVERABLE_PATH"
            # Old checkpoints have no saved fingerprint; preserving their terminal
            # behavior is safer than automatically re-planning an unknown run.
            condition = restored.terminal_condition or self._external_condition()
            self._latch.latch(reason, condition)

    def run(self, objective: AnalysisObjective,
            initial_requirements: tuple[ArtifactRequirement, ...],
            confirmed_intent: str = "",
            restored: RestoredRun | None = None) -> RunResult:
        from app.models.requirements import RequirementCatalog
        catalog = RequirementCatalog((objective,), initial_requirements)
        requirements = tuple(catalog.initial_requirements) + tuple(catalog.supporting_requirements)
        states = {state.requirement_ref: state for state in catalog.requirement_states}
        by_id = {item.requirement_id: item for item in requirements}

        run_id = self._fixed_run_id or self._id_factory("run")
        metrics = self._metrics_factory(run_id)
        started_at = monotonic()
        metrics.record("OBJECTIVE", subject_ref=objective.objective_id, agent="ORCHESTRATOR",
                       status="STARTED")
        self._latch = PlannerTerminalLatch()
        if restored is not None:
            self._apply_restored(restored, states)
            for artifact in restored.artifacts:
                metrics.record("ARTIFACT", subject_ref=artifact.artifact_id, agent="ORCHESTRATOR",
                               status="REUSED", source=artifact.provenance.source,
                               message="reused persisted artifact")
        decisions: list[PlanningDecision] = []
        routings: list[RoutingDecision] = []
        executions: list[ExecutionOutcome] = []
        round_index = 0
        budget = self._budget
        tasks_planned = 0
        stop_reason: str | None = None

        while True:
            condition = self._external_condition()
            if not self._latch.may_invoke(condition):
                stop_reason = self._latch.reason
                break
            self._latch.observe(condition)
            unmet = unmet_core_requirements(requirements, states)
            recoverable = tuple(
                item.requirement_id for item in unmet
                if self._router.eligible_sources(item.descriptor.artifact_type, self._permitted_sources,
                                                 item.descriptor.data_keys))
            policy_blocked = tuple(
                item.requirement_id for item in unmet
                if item.requirement_id not in recoverable
                and self._router.candidate_sources(item.descriptor.artifact_type, item.descriptor.data_keys))
            scoped_assessments = tuple(item for item in self._assessment_service.all_assessments()
                                       if item.requirement_ref in by_id
                                       and item.objective_ref in (None, objective.objective_id))
            accepted_refs = {item.artifact_ref for item in scoped_assessments if item.accepted}
            assessment_refs = {item.assessment_id for item in scoped_assessments}
            context = PlannerContext(
                objective=objective, requirements=requirements,
                requirement_states=tuple(states.values()), artifact_index=tuple(
                    item for item in self._registry.index() if item.artifact_ref in accepted_refs),
                assessment_summaries=tuple(item for item in self._assessment_service.summaries()
                                           if item.assessment_ref in assessment_refs), round=round_index,
                max_rounds=self._max_rounds, budget_remaining=budget,
                recoverable_gaps=recoverable, policy_blocked_gaps=policy_blocked,
                execution_summary=self._execution_summary(executions, round_index,
                                                         tasks_planned, budget),
                context_items=self._retrieve_context(ContextRequest(
                    request_id=f"planner-{run_id}-{round_index}", purpose="PLANNER",
                    query=objective.raw_query,
                    kinds=KNOWLEDGE_KINDS, max_items=MAX_CONTEXT_ITEMS, run_id=run_id)),
                prior_plan_count=len(decisions), planner_terminal=self._latch.latched)
            decision = self._planner.decide(context)
            decisions.append(decision)
            metrics.record("PLAN", subject_ref=decision.decision_id, agent="PLANNER",
                           status=decision.kind,
                           replan_count=sum(1 for item in decisions if item.kind == "REPLAN"),
                           message=decision.rationale)
            if decision.planner_terminal:
                self._latch.latch(decision.terminal_reason, condition)
                stop_reason = decision.terminal_reason
                if self._recorder is not None:
                    self._recorder.checkpoint(run_id, "PLANNER_TERMINAL",
                                              terminal_condition=condition)
                break

            if self._recorder is not None:
                self._recorder.checkpoint(run_id, "PLAN_ACCEPTED",
                                          active_work_refs=tuple(task.task_id for task in decision.tasks))
            tasks_planned += len(decision.tasks)
            new_artifact_ids: list[str] = []
            round_assessments: list[ArtifactAssessment] = []
            round_executions: list[tuple[ExecutionOutcome]] = []
            for task in decision.tasks:
                if budget <= 0:
                    break
                requirement = by_id[task.requirement_refs[0]]
                execution_route = None
                if self._source_mapping_resolver is not None and requirement.descriptor.artifact_type != "EVIDENCE":
                    execution_route = self._source_mapping_resolver.resolve(
                        task.task_id, requirement.descriptor.data_keys)
                routing = self._router.route(task, requirement.descriptor.artifact_type,
                                             self._permitted_sources,
                                             execution_route=execution_route,
                                             data_keys=requirement.descriptor.data_keys)
                routings.append(routing)
                metrics.record("ROUTE", subject_ref=routing.decision_id, agent="ROUTER",
                               status="SELECTED" if routing.selected_tool else "BLOCKED",
                               tool=routing.selected_tool or "", message=routing.rationale)
                outcome = (self._recorder.execute_once(run_id, task, routing, self._executor)
                           if self._recorder else self._executor.run(task, routing))
                executions.append(outcome)
                round_executions.append((outcome,))
                budget -= 1
                metrics.record("TASK", subject_ref=task.task_id, agent="EXECUTOR",
                               status=outcome.execution.status,
                               retry_count=max(len(outcome.attempts) - 1, 0),
                               tool=routing.selected_tool or "")
                for attempt_index, attempt in enumerate(outcome.attempts):
                    metrics.record("ATTEMPT", subject_ref=attempt.attempt_id, agent="EXECUTOR",
                                   status=attempt.status, retry_count=attempt_index,
                                   tool=attempt.tool, message=attempt.safe_error_summary)
                for supporting_artifact in outcome.supporting_artifacts:
                    self._registry.register(supporting_artifact)
                    if self._recorder is not None:
                        self._recorder.record_artifact(run_id, supporting_artifact)
                if outcome.artifact is not None:
                    if outcome.artifact.artifact_id not in self._registry:
                        new_artifact_ids.append(outcome.artifact.artifact_id)
                    metrics.record("ARTIFACT", subject_ref=outcome.artifact.artifact_id,
                                   agent="EXECUTOR", status="CREATED",
                                   source=outcome.artifact.provenance.source,
                                   tool=routing.selected_tool or "")
                    # Payload and artifact metadata are persisted before any state or
                    # assessment that references them. A failure here aborts the round.
                    if self._recorder is not None:
                        stored = self._recorder.record_artifact(run_id, outcome.artifact, outcome.payload,
                                                                outcome.payload_content_type)
                        artifact = outcome.artifact.model_copy(update={"payload_ref": stored.location}) if stored else outcome.artifact
                    else:
                        artifact = outcome.artifact
                    self._registry.register(artifact)
                    assessment = self._assessment_service.assess(
                        outcome.artifact.artifact_id, requirement, objective.objective_id,
                        context_items=self._retrieve_context(ContextRequest(
                            request_id=f"judge-{run_id}-{requirement.requirement_id}", purpose="JUDGE",
                            query=objective.raw_query, kinds=KNOWLEDGE_KINDS,
                            max_items=MAX_CONTEXT_ITEMS, run_id=run_id)))
                    round_assessments.append(assessment)
                    metrics.record("ASSESSMENT", subject_ref=assessment.assessment_id, agent="JUDGE",
                                   status=assessment.final_level, message=assessment.assessment_summary)

            for item in requirements:
                states[item.requirement_id] = derive_requirement_state(
                    item.requirement_id,
                    self._assessment_service.assessments_for(item.requirement_id),
                    states[item.requirement_id])

            if self._recorder is not None:
                for assessment in round_assessments:
                    self._recorder.record_assessment(run_id, assessment)
                self._recorder.record_requirement_states(run_id, states)
                self._recorder.record_objective_state(run_id, derive_objective_state(
                    objective.objective_id, requirements, states, planner_terminal=False))
                for (outcome,) in round_executions:
                    self._recorder.record_execution(run_id, outcome.execution, outcome.attempts,
                                                    outcome.artifact)
                if round_assessments:
                    self._recorder.checkpoint(run_id, "ARTIFACT_ASSESSED")
            round_index += 1

            if not new_artifact_ids:
                stop_reason = "NO_PROGRESS"
                self._latch.latch(stop_reason, self._external_condition())
                break

        objective_state = derive_objective_state(
            objective.objective_id, requirements, states, planner_terminal=True)
        # Scope to this objective: services may be shared across objectives in a run.
        requirement_ids = {item.requirement_id for item in requirements}
        run_assessments = tuple(
            item for item in self._assessment_service.all_assessments()
            if item.requirement_ref in requirement_ids
            and item.objective_ref in (None, objective.objective_id))
        execution_summary = self._execution_summary(executions, round_index, tasks_planned, budget)
        accepted_ids = tuple(
            dict.fromkeys(item.artifact_ref for item in run_assessments if item.accepted))
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
                limitation for item in run_assessments if item.accepted
                for limitation in item.limitations)),
            unresolved_gaps=tuple(item.requirement_id for item in unmet_core_requirements(requirements, states)),
            plan_revisions=sum(1 for item in decisions if item.kind in ("PLAN", "REPLAN")),
            execution_summary=execution_summary,
            stop_reason=stop_reason)
        response = build_response_package(
            run_id, objective_state, requirements, states,
            self._assessment_service, self._registry,
            context_items=self._retrieve_context(ContextRequest(
                request_id=f"response-{run_id}", purpose="RESPONSE",
                query=objective.raw_query,
                kinds=KNOWLEDGE_KINDS, max_items=MAX_CONTEXT_ITEMS, run_id=run_id)))
        if self._recorder is not None:
            self._recorder.record_objective_state(run_id, objective_state)
            self._recorder.record_completion_report(run_id, completion)
            self._recorder.record_response_package(run_id, response)
            self._recorder.checkpoint(run_id, "FINALIZATION",
                                      terminal_condition=self._latch.condition)
        metrics.record("FINALIZATION", subject_ref=objective.objective_id, agent="ORCHESTRATOR",
                       status=objective_state.status,
                       duration_ms=round((monotonic() - started_at) * 1000),
                       replan_count=completion.plan_revisions,
                       message=completion.stop_reason)
        return RunResult(
            run_id=run_id, completion_report=completion, response_package=response,
            objective_state=objective_state, requirement_states=tuple(states.values()),
            assessments=run_assessments, planning_decisions=tuple(decisions),
            routing_decisions=tuple(routings), executions=tuple(executions),
            retrieved_artifacts=tuple(dict.fromkeys(item.artifact_ref for item in run_assessments)),
            metrics=metrics.events())


def run_all_channel_baseball_agent(user_prompt: str, config: object = None) -> str:
    """Legacy entry point. Requires explicitly injected, validated dependencies."""
    raise RuntimeError(
        "The validated planning/response loop is not yet wired to live sources; "
        "legacy execution remains disabled.")
