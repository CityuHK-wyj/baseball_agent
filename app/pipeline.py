"""End-to-end analysis pipeline.

Composes the deterministic flow: raw query -> semantic normalization -> requirement
decomposition -> Orchestrator (planning, routing, execution, assessment, state) ->
CompletionReport -> ResponsePackage -> response text. The pipeline never re-interprets
intent and never bypasses the accepted-product boundary.
"""

from collections.abc import Callable
from datetime import datetime, timezone
from uuid import uuid4

from app.agent.executor import Executor, Tool
from app.agent.orchestrator import Orchestrator, RunResult
from app.agent.planner import Planner
from app.agent.registry import ArtifactRegistry
from app.agent.routing import Router
from app.agent.source_mapping import SourceMappingResolver
from app.assessment.service import AssessmentService
from app.context.service import ContextService
from app.llm.response import DeterministicResponseComposer, ResponseComposer
from app.models.artifacts import ArtifactContract
from app.models.clarification import ClarificationAnswer, ClarificationRequest
from app.models.contracts import (AnalysisObjective, ArtifactRequirement, CategoryConstraint,
                                  Constraint, Entity, LocationConstraint)
from app.models.interaction import (InteractionRecord, PermissionAnswer, PermissionRequest,
                                    ConstraintRevisionRequest, ConstraintRevisionAnswer)
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
    constraint_revisions: tuple[ConstraintRevisionRequest, ...] = ()
    objectives: tuple[AnalysisObjective, ...] = ()
    run_ids: tuple[str, ...] = ()
    objective_statuses: tuple[str, ...] = ()
    responses: tuple[str, ...] = ()
    response_packages: tuple[ResponsePackage, ...] = ()
    runs: tuple[RunResult, ...] = ()
    # Bounded, structured semantic observability: extractor, parser version, fallback
    # reason and the constraint summary. Never raw model text or credentials.
    semantic_trace: tuple[str, ...] = ()


ToolFactory = Callable[[tuple[ArtifactRequirement, ...]], Tool]


def hard_sources(objective: AnalysisObjective) -> tuple[str, ...]:
    groups = [set(item.values) for item in objective.constraints
              if isinstance(item, CategoryConstraint) and item.key in ("source", "source_kind")
              and item.authority in ("SYSTEM_POLICY", "USER_CONSTRAINT")]
    if not groups:
        return ()
    return tuple(sorted(set.intersection(*groups))) or ("__NO_SOURCE__",)


def default_tool_factory(row_count: int = 1200) -> ToolFactory:
    return lambda requirements: SyntheticDataTool(requirements, row_count=row_count)


class AnalysisPipeline:
    @classmethod
    def default(cls, **options) -> "AnalysisPipeline":
        """Compose the local runtime; injectable providers remain optional."""
        from app.runtime import build_pipeline
        return build_pipeline(**options)

    def close(self) -> None:
        for resource in getattr(self, "_owned_resources", ()):
            resource.close()
        self._owned_resources = ()

    def __init__(self, semantic: SemanticNormalizer, decomposer: RequirementDecomposer,
                 planner: Planner, router: Router, assessment_service: AssessmentService,
                 registry: ArtifactRegistry, tool_factory: ToolFactory | None = None,
                 context_service: ContextService | None = None, recorder: RunRecorder | None = None,
                 response_composer: ResponseComposer | None = None,
                 max_rounds: int = 3, budget: int = 10,
                 id_factory: Callable[[str], str] | None = None,
                 source_mapping_resolver: SourceMappingResolver | None = None) -> None:
        self._semantic = semantic
        self._decomposer = decomposer
        self._planner = planner
        self._router = router
        self._assessment_service = assessment_service
        self._registry = registry
        self._tool_factory = tool_factory or default_tool_factory()
        self._context_service = context_service
        self._recorder = recorder
        self._source_mapping_resolver = source_mapping_resolver
        self._response_composer = response_composer or DeterministicResponseComposer()
        self._max_rounds = max_rounds
        self._budget = budget
        self._id_factory = id_factory or (lambda prefix: f"{prefix}-{uuid4().hex}")

    def analyze(self, raw_query: str, mentions: tuple[str, ...] | None = None,
                constraints: tuple[Constraint, ...] = (), run_id: str | None = None) -> PipelineResult:
        if run_id is not None and self._recorder is not None and self._recorder.has_run(run_id):
            raise ValueError("Run already exists; answer its pending request or inspect resume state")
        semantic = self._semantic.normalize(raw_query, constraints=constraints, mentions=mentions)
        trace = semantic.notes
        if semantic.needs_clarification:
            effective_run_id = run_id or self._id_factory("run")
            if self._recorder is not None:
                request = semantic.clarifications[0]
                self._recorder.record_interaction(InteractionRecord(
                    run_id=effective_run_id, raw_query=raw_query, objectives=semantic.objectives,
                    clarification=request))
                self._recorder.checkpoint(effective_run_id, "WAITING_FOR_USER",
                                          pending_request_refs=(request.clarification_id,))
            return PipelineResult(raw_query=raw_query, needs_clarification=True,
                                  clarifications=semantic.clarifications,
                                  objectives=semantic.objectives, run_ids=(effective_run_id,),
                                  semantic_trace=trace)
        if not semantic.objectives:
            return PipelineResult(raw_query=raw_query, semantic_trace=trace)

        return self._prepare_objectives(raw_query, semantic.objectives, run_id).model_copy(
            update={"semantic_trace": trace})

    def _prepare_objectives(self, raw_query: str, objectives: tuple[AnalysisObjective, ...],
                            run_id: str | None) -> PipelineResult:
        for objective in objectives:
            sources = hard_sources(objective)
            for requirement in self._decomposer.decompose(objective):
                if requirement.base_criticality != "CORE" or not sources:
                    continue
                kind = requirement.descriptor.artifact_type
                if self._router.eligible_sources(kind, sources, requirement.descriptor.data_keys,
                                                 time_range=requirement.descriptor.time_range):
                    continue
                alternatives = self._router.eligible_sources(kind, data_keys=requirement.descriptor.data_keys,
                                                             time_range=requirement.descriptor.time_range)
                for original in objective.constraints:
                    if not (isinstance(original, CategoryConstraint) and
                            original.key in ("source", "source_kind") and
                            original.authority == "USER_CONSTRAINT"):
                        continue
                    for candidate in alternatives:
                        proposed = original.model_copy(update={"values": (candidate.source_kind,),
                                                               "origin": "USER_CONFIRMED"})
                        revised = objective.model_copy(update={"constraints": tuple(
                            proposed if item == original else item for item in objective.constraints)})
                        if not self._router.eligible_sources(kind, hard_sources(revised), requirement.descriptor.data_keys,
                                                             time_range=requirement.descriptor.time_range):
                            continue
                        request = ConstraintRevisionRequest(
                            revision_id=self._id_factory("revision"), objective_ref=objective.objective_id,
                            original=original, proposed=proposed,
                            reason="The requested source is unavailable; this source can satisfy the requirement")
                        effective_run_id = run_id or self._id_factory("run")
                        if self._recorder is not None:
                            self._recorder.record_interaction(InteractionRecord(
                                run_id=effective_run_id, raw_query=raw_query, objectives=objectives,
                                constraint_revision=request))
                            self._recorder.checkpoint(effective_run_id, "WAITING_FOR_USER",
                                                      pending_request_refs=(request.revision_id,))
                        return PipelineResult(raw_query=raw_query, objectives=objectives,
                            run_ids=(effective_run_id,), constraint_revisions=(request,))

        for objective in objectives:
            requirements = self._decomposer.decompose(objective)
            if any(not self._router.eligible_sources(item.descriptor.artifact_type, hard_sources(objective),
                                                     item.descriptor.data_keys,
                                                     time_range=item.descriptor.time_range)
                   for item in requirements):
                candidates = next((self._router.permission_candidates(item.descriptor.artifact_type, item.descriptor.data_keys,
                                                                      time_range=item.descriptor.time_range)
                                   for item in requirements
                                   if self._router.permission_candidates(item.descriptor.artifact_type, item.descriptor.data_keys,
                                                                          time_range=item.descriptor.time_range)), ())
                if candidates:
                    candidates = tuple(item for item in candidates if not hard_sources(objective)
                                       or item.source_kind in hard_sources(objective))
                    if not candidates:
                        continue
                    capability = candidates[0]
                    request = PermissionRequest(permission_id=self._id_factory("permission"),
                                                objective_ref=objective.objective_id,
                                                tool=capability.tool, source_kind=capability.source_kind,
                                                cost=capability.cost, reason="Source requires user consent")
                    effective_run_id = run_id or self._id_factory("run")
                    if self._recorder is not None:
                        self._recorder.record_interaction(InteractionRecord(
                            run_id=effective_run_id, raw_query=raw_query,
                            objectives=objectives, permission=request))
                        self._recorder.checkpoint(effective_run_id, "WAITING_FOR_USER",
                                                  pending_request_refs=(request.permission_id,))
                    return PipelineResult(raw_query=raw_query, objectives=objectives,
                                          run_ids=(effective_run_id,), permissions=(request,))

        return self._run_objectives(raw_query, objectives, run_id)

    def resume_constraint_revision(self, run_id: str, answer: ConstraintRevisionAnswer) -> PipelineResult:
        if self._recorder is None:
            raise RuntimeError("Constraint revision resume requires a persistent RunRecorder")
        interaction = self._recorder.load_interaction(run_id)
        if interaction is None or interaction.status != "WAITING_FOR_USER" or interaction.constraint_revision is None:
            raise ValueError("No pending constraint revision")
        request = interaction.constraint_revision
        if answer.revision_ref != request.revision_id:
            raise ValueError("Constraint revision answer does not match the pending request")
        if datetime.now(timezone.utc) >= request.expires_at:
            self._recorder.consume_interaction(interaction, interaction.model_copy(update={"status": "EXPIRED"}))
            raise ValueError("Constraint revision expired")
        targets = [item for item in interaction.objectives if item.objective_id == request.objective_ref]
        if (len(targets) != 1 or request.original not in targets[0].constraints or
                request.original.authority != "USER_CONSTRAINT" or
                request.proposed.authority != "USER_CONSTRAINT" or
                request.proposed.origin != "USER_CONFIRMED" or
                request.proposed.key != request.original.key):
            raise ValueError("Constraint revision scope is stale or invalid")
        objectives = interaction.objectives
        if answer.accepted:
            objectives = tuple(item.model_copy(update={"constraints": tuple(
                request.proposed if constraint == request.original else constraint
                for constraint in item.constraints)}) if item.objective_id == request.objective_ref
                else item for item in objectives)
        self._recorder.consume_interaction(interaction, interaction.model_copy(update={
            "status": "CONFIRMED" if answer.accepted else "REJECTED", "objectives": objectives,
            "confirmed_constraints": (request.proposed,) if answer.accepted else ()}))
        if answer.accepted:
            return self._prepare_objectives(interaction.raw_query, objectives, run_id)
        return self._run_objectives(interaction.raw_query, objectives, run_id)

    def resume_permission(self, run_id: str, answer: PermissionAnswer) -> PipelineResult:
        if self._recorder is None:
            raise RuntimeError("Permission resume requires a persistent RunRecorder")
        interaction = self._recorder.load_interaction(run_id)
        if interaction is None or interaction.status != "WAITING_FOR_USER" or interaction.permission is None:
            raise ValueError(f"Run {run_id!r} has no pending permission")
        request = interaction.permission
        if answer.permission_ref != request.permission_id:
            raise ValueError("Permission answer does not match the pending request")
        if datetime.now(timezone.utc) >= request.expires_at:
            self._recorder.consume_interaction(interaction, interaction.model_copy(update={"status": "EXPIRED"}))
            raise ValueError("Permission request expired")
        if not answer.approved:
            self._recorder.consume_interaction(interaction, interaction.model_copy(update={"status": "REJECTED"}))
            return PipelineResult(raw_query=interaction.raw_query, objectives=interaction.objectives,
                                  run_ids=(run_id,))
        if request.action != "EXECUTE" or not any(
                objective.objective_id == request.objective_ref for objective in interaction.objectives):
            raise ValueError("Permission scope does not match the objective or action")
        candidates = tuple(capability for objective in interaction.objectives
                           if objective.objective_id == request.objective_ref
                           for requirement in self._decomposer.decompose(objective)
                           for capability in self._router.permission_candidates(
                               requirement.descriptor.artifact_type, requirement.descriptor.data_keys,
                               time_range=requirement.descriptor.time_range))
        if not any((item.tool, item.source_kind, item.cost) ==
                   (request.tool, request.source_kind, request.cost) for item in candidates):
            raise ValueError("Permission scope is stale or forbidden by system policy")
        authorized = self._router.authorized_for((request.cost,), (request.tool,))
        self._recorder.consume_interaction(interaction, interaction.model_copy(update={"status": "APPROVED"}))
        return self._run_objectives(interaction.raw_query, interaction.objectives, run_id,
                                    router=authorized, authorized_objective=request.objective_ref)

    def resume_clarification(self, run_id: str, answer: ClarificationAnswer) -> PipelineResult:
        if self._recorder is None:
            raise RuntimeError("Clarification resume requires a persistent RunRecorder")
        interaction = self._recorder.load_interaction(run_id)
        if interaction is None or interaction.status != "WAITING_FOR_USER" or interaction.clarification is None:
            raise ValueError(f"Run {run_id!r} has no pending clarification")
        if answer.clarification_ref != interaction.clarification.clarification_id:
            raise ValueError("Clarification answer does not match the pending request")
        option = interaction.clarification.chosen(answer.chosen_option_id)
        if option is None:
            raise ValueError("Clarification answer selected an unknown option")
        if interaction.clarification.kind == "CONSTRAINT":
            return self._resume_constraint_clarification(interaction, option, run_id)
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
        self._recorder.consume_interaction(interaction, interaction.model_copy(update={
            "status": "CONFIRMED", "objectives": objectives,
            "confirmed_constraints": (confirmed,)}))
        return self._prepare_objectives(interaction.raw_query, objectives, run_id)

    def _resume_constraint_clarification(self, interaction, option, run_id: str) -> PipelineResult:
        """Apply a CONSTRAINT clarification (currently pitch-location definition)."""
        confirmed = LocationConstraint(key="pitch_location", definition=option.value,
                                       origin="USER_CONFIRMED", authority="USER_CONSTRAINT")
        objectives = tuple(item.model_copy(update={
            "constraints": tuple((*item.constraints, confirmed)),
        }) for item in interaction.objectives)
        self._recorder.consume_interaction(interaction, interaction.model_copy(update={
            "status": "CONFIRMED", "objectives": objectives,
            "confirmed_constraints": (confirmed,)}))
        return self._prepare_objectives(interaction.raw_query, objectives, run_id)

    def resume_run(self, run_id: str) -> PipelineResult:
        """Continue durable confirmed intent without consuming an answer again."""
        if self._recorder is None:
            raise RuntimeError("Run recovery requires a persistent RunRecorder")
        interaction = self._recorder.load_interaction(run_id)
        definitions = self._recorder.initial_definitions(run_id)
        if interaction is not None:
            if interaction.status in ("WAITING_FOR_USER", "EXPIRED"):
                raise ValueError("Run requires a valid user answer before recovery")
            if interaction.permission is not None:
                if interaction.status != "APPROVED":
                    return PipelineResult(raw_query=interaction.raw_query, objectives=interaction.objectives,
                                          run_ids=(run_id,))
                request = interaction.permission
                if datetime.now(timezone.utc) >= request.expires_at:
                    raise ValueError("Permission expired before recovery")
                candidates = tuple(item for objective in interaction.objectives
                    if objective.objective_id == request.objective_ref
                    for requirement in self._decomposer.decompose(objective)
                    for item in self._router.permission_candidates(requirement.descriptor.artifact_type,
                                                                    requirement.descriptor.data_keys,
                                                                    time_range=requirement.descriptor.time_range))
                if request.action != "EXECUTE" or not any(
                    (item.tool, item.source_kind, item.cost) == (request.tool, request.source_kind, request.cost)
                    for item in candidates):
                    raise ValueError("Recovered permission scope is stale or forbidden")
                return self._run_objectives(interaction.raw_query, interaction.objectives, run_id,
                    router=self._router.authorized_for((request.cost,), (request.tool,)),
                    authorized_objective=request.objective_ref)
            if definitions or interaction.status == "REJECTED":
                return self._run_objectives(interaction.raw_query, interaction.objectives, run_id)
            return self._prepare_objectives(interaction.raw_query, interaction.objectives, run_id)
        run_definition = self._recorder.run_definition(run_id)
        if run_definition is not None:
            return self._run_objectives(*run_definition, run_id)
        if not definitions:
            raise ValueError("No persisted run definitions")
        objectives = tuple(item[0] for item in definitions)
        return self._run_objectives(objectives[0].raw_query, objectives, run_id)

    def _run_objectives(self, raw_query: str, objectives: tuple[AnalysisObjective, ...],
                        run_id: str | None, router: Router | None = None,
                        authorized_objective: str | None = None) -> PipelineResult:
        requirements: list[ArtifactRequirement] = []
        effective_run_id = run_id or self._id_factory("run")
        if self._recorder:
            self._recorder.record_run_definition(effective_run_id, raw_query, objectives)
        saved = dict((item.objective_id, (item, needs)) for item, needs in
                     self._recorder.initial_definitions(effective_run_id)) if self._recorder else {}
        for objective in objectives:
            if objective.objective_id in saved:
                previous, needs = saved[objective.objective_id]
                if previous != objective:
                    raise ValueError("Persisted initial objective cannot be changed")
            else:
                needs = self._decomposer.decompose(objective)
                if self._recorder:
                    needs = self._recorder.record_initial_definition(effective_run_id, objective, needs)
            requirements.extend(needs)
        all_requirements = tuple(requirements)

        tool = self._tool_factory(all_requirements)
        executor = Executor(tool if isinstance(tool, dict) else {tool.name: tool},
                            max_retries=0, id_factory=self._id_factory)

        run_ids: list[str] = []
        statuses: list[str] = []
        responses: list[str] = []
        packages: list[ResponsePackage] = []
        runs: list[RunResult] = []
        for index, objective in enumerate(objectives):
            objective_requirements = tuple(
                item for item in all_requirements if item.objective_ref == objective.objective_id)
            orchestrator = Orchestrator(
                self._planner, router if router is not None and
                objective.objective_id == authorized_objective else self._router,
                executor, self._assessment_service, self._registry,
                max_rounds=self._max_rounds, budget=self._budget, id_factory=self._id_factory,
                context_service=self._context_service, recorder=self._recorder,
                permitted_sources=hard_sources(objective),
                source_mapping_resolver=self._source_mapping_resolver,
                run_id=effective_run_id)
            restored = self._recorder.restore_objective(effective_run_id, objective.objective_id,
                tuple(item.requirement_id for item in objective_requirements)) if self._recorder else None
            result = orchestrator.run(objective, objective_requirements, confirmed_intent=raw_query,
                                      restored=restored)
            run_ids.append(result.run_id)
            statuses.append(result.objective_state.status)
            packages.append(result.response_package)
            responses.append(self._response_composer.compose(result.response_package))
            runs.append(result)

        return PipelineResult(raw_query=raw_query, objectives=objectives,
                              run_ids=tuple(run_ids), objective_statuses=tuple(statuses),
                              responses=tuple(responses), response_packages=tuple(packages),
                              runs=tuple(runs))
