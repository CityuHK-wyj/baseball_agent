"""End-to-end analysis pipeline.

Composes the deterministic flow: raw query -> semantic normalization -> requirement
decomposition -> Orchestrator (planning, routing, execution, assessment, state) ->
CompletionReport -> ResponsePackage -> response text. The pipeline never re-interprets
intent and never bypasses the accepted-product boundary.
"""

from collections.abc import Callable

from app.agent.executor import Executor, Tool
from app.agent.orchestrator import Orchestrator, RunResult
from app.agent.planner import Planner
from app.agent.registry import ArtifactRegistry
from app.agent.routing import Router
from app.assessment.service import AssessmentService
from app.context.service import ContextService
from app.llm.response import DeterministicResponseComposer, ResponseComposer
from app.models.artifacts import ArtifactContract
from app.models.clarification import ClarificationAnswer, ClarificationRequest
from app.models.contracts import (AnalysisObjective, ArtifactRequirement, CategoryConstraint,
                                  Constraint, Entity)
from app.models.interaction import InteractionRecord, PermissionAnswer, PermissionRequest
from app.models.reports import ResponsePackage
from app.persistence.recorder import RunRecorder
from app.semantic.normalizer import SemanticNormalizer
from app.semantic.requirement_decomposer import RequirementDecomposer
from app.tools.synthetic import SyntheticDataTool


class PipelineResult(ArtifactContract):
    raw_query: str
    needs_clarification: bool = False
    clarifications: tuple[ClarificationRequest, ...] = ()
    permissions: tuple[PermissionRequest, ...] = ()
    objectives: tuple[AnalysisObjective, ...] = ()
    run_ids: tuple[str, ...] = ()
    objective_statuses: tuple[str, ...] = ()
    responses: tuple[str, ...] = ()
    response_packages: tuple[ResponsePackage, ...] = ()
    runs: tuple[RunResult, ...] = ()


ToolFactory = Callable[[tuple[ArtifactRequirement, ...]], Tool]


def default_tool_factory(row_count: int = 1200) -> ToolFactory:
    return lambda requirements: SyntheticDataTool(requirements, row_count=row_count)


class AnalysisPipeline:
    def __init__(self, semantic: SemanticNormalizer, decomposer: RequirementDecomposer,
                 planner: Planner, router: Router, assessment_service: AssessmentService,
                 registry: ArtifactRegistry, tool_factory: ToolFactory | None = None,
                 context_service: ContextService | None = None, recorder: RunRecorder | None = None,
                 response_composer: ResponseComposer | None = None,
                 max_rounds: int = 3, budget: int = 10,
                 id_factory: Callable[[str], str] | None = None) -> None:
        self._semantic = semantic
        self._decomposer = decomposer
        self._planner = planner
        self._router = router
        self._assessment_service = assessment_service
        self._registry = registry
        self._tool_factory = tool_factory or default_tool_factory()
        self._context_service = context_service
        self._recorder = recorder
        self._response_composer = response_composer or DeterministicResponseComposer()
        self._max_rounds = max_rounds
        self._budget = budget
        counter = iter(range(1, 100_000))
        self._id_factory = id_factory or (lambda prefix: f"{prefix}-{next(counter)}")

    def analyze(self, raw_query: str, mentions: tuple[str, ...] | None = None,
                constraints: tuple[Constraint, ...] = (), run_id: str | None = None) -> PipelineResult:
        semantic = self._semantic.normalize(raw_query, constraints=constraints, mentions=mentions)
        if semantic.needs_clarification:
            effective_run_id = run_id or "run-1"
            if self._recorder is not None:
                request = semantic.clarifications[0]
                self._recorder.record_interaction(InteractionRecord(
                    run_id=effective_run_id, raw_query=raw_query, objectives=semantic.objectives,
                    clarification=request))
                self._recorder.checkpoint(effective_run_id, "WAITING_FOR_USER",
                                          pending_request_refs=(request.clarification_id,))
            return PipelineResult(raw_query=raw_query, needs_clarification=True,
                                  clarifications=semantic.clarifications,
                                  objectives=semantic.objectives, run_ids=(effective_run_id,))
        if not semantic.objectives:
            return PipelineResult(raw_query=raw_query)

        for objective in semantic.objectives:
            requirements = self._decomposer.decompose(objective)
            if any(not self._router.eligible_sources(item.descriptor.artifact_type)
                   for item in requirements):
                candidates = next((self._router.permission_candidates(item.descriptor.artifact_type)
                                   for item in requirements
                                   if self._router.permission_candidates(item.descriptor.artifact_type)), ())
                if candidates:
                    capability = candidates[0]
                    request = PermissionRequest(permission_id=self._id_factory("permission"),
                                                objective_ref=objective.objective_id,
                                                tool=capability.tool, source_kind=capability.source_kind,
                                                cost=capability.cost, reason="Source requires user consent")
                    effective_run_id = run_id or "run-1"
                    if self._recorder is not None:
                        self._recorder.record_interaction(InteractionRecord(
                            run_id=effective_run_id, raw_query=raw_query,
                            objectives=semantic.objectives, permission=request))
                        self._recorder.checkpoint(effective_run_id, "WAITING_FOR_USER",
                                                  pending_request_refs=(request.permission_id,))
                    return PipelineResult(raw_query=raw_query, objectives=semantic.objectives,
                                          run_ids=(effective_run_id,), permissions=(request,))

        return self._run_objectives(raw_query, semantic.objectives, run_id)

    def resume_permission(self, run_id: str, answer: PermissionAnswer) -> PipelineResult:
        if self._recorder is None:
            raise RuntimeError("Permission resume requires a persistent RunRecorder")
        interaction = self._recorder.load_interaction(run_id)
        if interaction is None or interaction.status != "WAITING_FOR_USER" or interaction.permission is None:
            raise ValueError(f"Run {run_id!r} has no pending permission")
        request = interaction.permission
        if answer.permission_ref != request.permission_id:
            raise ValueError("Permission answer does not match the pending request")
        if not answer.approved:
            self._recorder.record_interaction(interaction.model_copy(update={"status": "REJECTED"}))
            return PipelineResult(raw_query=interaction.raw_query, objectives=interaction.objectives,
                                  run_ids=(run_id,))
        authorized = self._router.authorized_for((request.cost,), (request.tool,))
        self._recorder.record_interaction(interaction.model_copy(update={"status": "APPROVED"}))
        return self._run_objectives(interaction.raw_query, interaction.objectives, run_id,
                                    router=authorized)

    def resume_clarification(self, run_id: str, answer: ClarificationAnswer) -> PipelineResult:
        if self._recorder is None:
            raise RuntimeError("Clarification resume requires a persistent RunRecorder")
        interaction = self._recorder.load_interaction(run_id)
        if interaction is None or interaction.status != "WAITING_FOR_USER":
            raise ValueError(f"Run {run_id!r} has no pending clarification")
        if answer.clarification_ref != interaction.clarification.clarification_id:
            raise ValueError("Clarification answer does not match the pending request")
        option = interaction.clarification.chosen(answer.chosen_option_id)
        if option is None:
            raise ValueError("Clarification answer selected an unknown option")
        canonical = self._semantic.entity_for_key(option.value)
        namespace, _, identifier = canonical.entity_key.partition(":")
        entity = Entity(namespace=namespace or "LOCAL", entity_type=canonical.entity_type,
                        identifier=identifier or canonical.entity_key)
        confirmed = CategoryConstraint(key="entity_key", values=(option.value,),
                                       origin="USER_CONFIRMED", authority="USER_CONSTRAINT")
        objectives = tuple(item.model_copy(update={
            "entities": tuple((*item.entities, entity)),
            "constraints": tuple((*item.constraints, confirmed)),
        }) for item in interaction.objectives)
        self._recorder.record_interaction(interaction.model_copy(update={
            "status": "CONFIRMED", "objectives": objectives,
            "confirmed_constraints": (confirmed,)}))
        return self._run_objectives(interaction.raw_query, objectives, run_id)

    def _run_objectives(self, raw_query: str, objectives: tuple[AnalysisObjective, ...],
                        run_id: str | None, router: Router | None = None) -> PipelineResult:
        requirements: list[ArtifactRequirement] = []
        for objective in objectives:
            requirements.extend(self._decomposer.decompose(objective))
        all_requirements = tuple(requirements)

        tool = self._tool_factory(all_requirements)
        executor = Executor({tool.name: tool}, max_retries=0, id_factory=self._id_factory)

        run_ids: list[str] = []
        statuses: list[str] = []
        responses: list[str] = []
        packages: list[ResponsePackage] = []
        runs: list[RunResult] = []
        for index, objective in enumerate(objectives):
            objective_requirements = tuple(
                item for item in all_requirements if item.objective_ref == objective.objective_id)
            effective_run_id = run_id or f"run-{index + 1}"
            orchestrator = Orchestrator(
                self._planner, router or self._router, executor, self._assessment_service, self._registry,
                max_rounds=self._max_rounds, budget=self._budget, id_factory=self._id_factory,
                context_service=self._context_service, recorder=self._recorder,
                run_id=effective_run_id)
            result = orchestrator.run(objective, objective_requirements, confirmed_intent=raw_query)
            run_ids.append(result.run_id)
            statuses.append(result.objective_state.status)
            packages.append(result.response_package)
            responses.append(self._response_composer.compose(result.response_package))
            runs.append(result)

        return PipelineResult(raw_query=raw_query, objectives=objectives,
                              run_ids=tuple(run_ids), objective_statuses=tuple(statuses),
                              responses=tuple(responses), response_packages=tuple(packages),
                              runs=tuple(runs))
