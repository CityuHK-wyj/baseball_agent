"""The artifact runtime: conversation -> goal -> need graph -> planner -> artifacts.

This is the center of the v0.4 runtime. Tools are evidence-producing operators; the
planner is an artifact/dataflow planner; completion depends on frozen user obligations and
independently judged verified evidence. The engine owns conversation state, the reference
graph, the artifact store, the durable event journal and persistence.

v0.4 invariants enforced here:

* explicit instruction bindings (no ambient export injection),
* deterministic scope verification for every produced artifact,
* a first-class ToolOutcome/ToolAttempt for every attempted action,
* independent Judge participation in every evidence loop,
* state projection (not Planner/Judge) owns terminal truth,
* an append-only redacted event journal that makes failures diagnosable.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from uuid import uuid4

from app.models.agent_runtime import AgentMessage, PendingClarification, RunStatus
from app.models.artifact_runtime import (Claim, CoverageAssessment, Goal, Need, PlannerDecision,
                                         RuntimeArtifact, RuntimeEvent, RuntimeTrace, Scope,
                                         SemanticBrief, ToolAttempt, ToolRequest, utcnow)
from app.artifact_runtime.artifacts import ArtifactStore
from app.artifact_runtime.bindings import (apply_bindings, artifact_index,
                                           resolve_bindings_with_gaps)
from app.artifact_runtime.claims import build_claims, validate_claims
from app.artifact_runtime.convergence import (build_feedback, capability_views,
                                              export_views, schema_views,
                                              unavailable_capabilities)
from app.artifact_runtime.events import EventJournal
from app.artifact_runtime.obligations import (detect_conflicts, extract_obligations,
                                              obligation_coverage, obligation_gaps)
from app.artifact_runtime.planner import Planner, PlannerContext, PlannerAction, SemanticInterpreter
from app.artifact_runtime.recovery import (attempt_for, gap_for_attempt, normalize_outcome,
                                           requires_replan)
from app.artifact_runtime.references import ReferenceStore
from app.artifact_runtime.response import DeterministicResponseComposer, ResponseComposer
from app.artifact_runtime.scope_verification import verify_artifact_scope
from app.artifact_runtime.state import StateProjector
from app.artifact_runtime.sufficiency import CoverageJudge, GoalCoverage
from app.artifact_runtime.tool_base import RuntimeTool, ToolContext, ToolOutcome, ToolRegistry

_MAX_ITERATIONS = 6
_MAX_HISTORY = 8
# Maximum full semantic replans per turn. Recovery is preserved; runaway replan
# amplification is not. Repeated identical gaps are skipped independently.
_MAX_REPLANS = 2


@dataclass
class RuntimeTurnResult:
    conversation_id: str
    status: RunStatus
    answer: str
    pending_clarification: PendingClarification | None = None
    claims: tuple[Claim, ...] = ()
    artifacts: tuple[RuntimeArtifact, ...] = ()
    coverage: GoalCoverage | None = None
    trace: RuntimeTrace | None = None


@dataclass
class RuntimeConversation:
    conversation_id: str
    status: RunStatus = "PENDING"
    messages: list[AgentMessage] = field(default_factory=list)
    refs: ReferenceStore = field(default_factory=ReferenceStore)
    artifacts: ArtifactStore = field(default_factory=ArtifactStore)
    goal: Goal | None = None
    previous_goals: list[Goal] = field(default_factory=list)
    needs: list[Need] = field(default_factory=list)
    assessments: list[CoverageAssessment] = field(default_factory=list)
    decisions: list[PlannerDecision] = field(default_factory=list)
    attempts: list[ToolAttempt] = field(default_factory=list)
    events: EventJournal = field(default_factory=EventJournal)
    pending_clarification: PendingClarification | None = None
    accepted_context: list[str] = field(default_factory=list)
    clarification_refs: list[str] = field(default_factory=list)
    recent_entities: list[str] = field(default_factory=list)
    export_refs: dict[str, str] = field(default_factory=dict)
    steps: list[str] = field(default_factory=list)
    turns: int = 0
    last_coverage: GoalCoverage | None = None
    # The semantic brief that produced a pending clarification. Persisted so a reply that
    # selects a machine-generated option can resume deterministically without re-calling
    # the semantic model. Free-text replies never use this path.
    last_brief: SemanticBrief | None = None
    persistence_error: str = ""

    # -- persistence -------------------------------------------------------
    def snapshot(self) -> dict:
        return {
            "schema_version": 2,
            "conversation_id": self.conversation_id, "status": self.status,
            "messages": [item.model_dump(mode="json") for item in self.messages],
            "references": [item.model_dump(mode="json") for item in self.refs.all()],
            "artifacts": [item.model_dump(mode="json") for item in self.artifacts.all()],
            "goal": self.goal.model_dump(mode="json") if self.goal else None,
            "previous_goals": [item.model_dump(mode="json") for item in self.previous_goals],
            "needs": [item.model_dump(mode="json") for item in self.needs],
            "assessments": [item.model_dump(mode="json") for item in self.assessments],
            "decisions": [item.model_dump(mode="json") for item in self.decisions],
            "attempts": [item.model_dump(mode="json") for item in self.attempts],
            "events": [item.model_dump(mode="json") for item in self.events.all()],
            "pending_clarification": (self.pending_clarification.model_dump(mode="json")
                                      if self.pending_clarification else None),
            "accepted_context": list(self.accepted_context),
            "clarification_refs": list(self.clarification_refs),
            "recent_entities": list(self.recent_entities),
            "export_refs": dict(self.export_refs), "steps": list(self.steps),
            "turns": self.turns,
            "last_brief": (self.last_brief.model_dump(mode="json")
                           if self.last_brief else None),
        }

    @classmethod
    def restore(cls, payload: dict) -> "RuntimeConversation":
        conversation = cls(conversation_id=payload["conversation_id"])
        conversation.status = payload.get("status", "PENDING")
        conversation.messages = [AgentMessage.model_validate(item)
                                 for item in payload.get("messages", ())]
        for item in payload.get("references", ()):
            conversation.refs.put(_ReferenceModel.model_validate(item))
        conversation.artifacts.seed_from(tuple(
            RuntimeArtifact.model_validate(item) for item in payload.get("artifacts", ())))
        if payload.get("goal"):
            conversation.goal = Goal.model_validate(payload["goal"])
        conversation.previous_goals = [Goal.model_validate(item)
                                       for item in payload.get("previous_goals", ())]
        conversation.needs = [Need.model_validate(item) for item in payload.get("needs", ())]
        conversation.assessments = [CoverageAssessment.model_validate(item)
                                    for item in payload.get("assessments", ())]
        conversation.decisions = [PlannerDecision.model_validate(item)
                                  for item in payload.get("decisions", ())]
        conversation.attempts = [ToolAttempt.model_validate(item)
                                 for item in payload.get("attempts", ())]
        conversation.events.extend(RuntimeEvent.model_validate(item)
                                   for item in payload.get("events", ()))
        if payload.get("pending_clarification"):
            conversation.pending_clarification = PendingClarification.model_validate(
                payload["pending_clarification"])
        conversation.accepted_context = list(payload.get("accepted_context", ()))
        conversation.clarification_refs = list(payload.get("clarification_refs", ()))
        conversation.recent_entities = list(payload.get("recent_entities", ()))
        conversation.export_refs = dict(payload.get("export_refs", {}))
        conversation.steps = list(payload.get("steps", ()))
        conversation.turns = int(payload.get("turns", 0))
        if payload.get("last_brief"):
            conversation.last_brief = SemanticBrief.model_validate(payload["last_brief"])
        return conversation


# Imported lazily to avoid a pydantic forward-reference cycle at module import.
from app.models.artifact_runtime import Reference as _ReferenceModel  # noqa: E402


class ArtifactRuntime:
    def __init__(self, *, interpreter: SemanticInterpreter, planner: Planner,
                 registry: ToolRegistry, composer: ResponseComposer | None = None,
                 judge: CoverageJudge | None = None, knowledge=None, entity_lookup=None,
                 web=None, batting=None, pitching=None, roster_provider=None,
                 postgres_executor=None, parquet_executor=None,
                 parquet_glob: str = "mlb_statcast_*.parquet", player_names=None,
                 field_mapping=None, candidate_sink=None, store=None,
                 run_id: str = "runtime", today=date.today, id_factory=None,
                 projector: StateProjector | None = None) -> None:
        self._interpreter = interpreter
        self._planner = planner
        self._registry = registry
        self._composer = composer or DeterministicResponseComposer()
        self._judge = judge or CoverageJudge()
        self._knowledge = knowledge
        self._entity_lookup = entity_lookup
        self._web = web
        self._batting = batting
        self._pitching = pitching
        self._roster_provider = roster_provider
        self._postgres_executor = postgres_executor
        self._parquet_executor = parquet_executor
        self._parquet_glob = parquet_glob
        self._player_names = dict(player_names or {})
        self._field_mapping = field_mapping
        self._candidate_sink = candidate_sink
        self._store = store
        self._run_id = run_id
        self._today = today
        self._projector = projector or StateProjector()
        self._id = id_factory or (lambda prefix: f"{prefix}-{uuid4().hex}")
        self._conversations: dict[str, RuntimeConversation] = {}
        self._catalog_cache = None
        self._schema_views_cache: tuple = ()
        self._capability_views_cache: tuple = ()

    # -- public API --------------------------------------------------------
    def start_conversation(self) -> str:
        conversation_id = self._id("conv")
        conversation = RuntimeConversation(conversation_id=conversation_id)
        self._conversations[conversation_id] = conversation
        self._persist(conversation)
        return conversation_id

    def resume_conversation(self, conversation_id: str) -> RuntimeConversation:
        if conversation_id in self._conversations:
            return self._conversations[conversation_id]
        if self._store is not None:
            stored = self._store.get_object("runtime_conversation", conversation_id)
            if stored is not None:
                self._check_ownership(stored)
                conversation = RuntimeConversation.restore(stored.payload)
                self._conversations[conversation_id] = conversation
                conversation.events.emit(
                    "RESUMED", conversation_id=conversation_id, run_id=self._run_id,
                    detail="conversation restored from durable store")
                return conversation
        raise KeyError(f"Unknown conversation {conversation_id!r}")

    def _check_ownership(self, stored) -> None:
        owner = getattr(stored, "run_id", None)
        if owner and owner != self._run_id and self._run_id != "runtime":
            raise KeyError(f"conversation belongs to run {owner!r}, not {self._run_id!r}")

    def get_conversation(self, conversation_id: str) -> RuntimeConversation:
        return self._conversations.get(conversation_id) or self.resume_conversation(
            conversation_id)

    def get_trace(self, conversation_id: str) -> RuntimeTrace | None:
        return getattr(self.get_conversation(conversation_id), "_last_trace", None)

    def send_message(self, conversation_id: str, text: str) -> RuntimeTurnResult:
        return self._turn(conversation_id, text, is_clarification=False)

    def respond_to_clarification(self, conversation_id: str, text: str) -> RuntimeTurnResult:
        return self._turn(conversation_id, text, is_clarification=True)

    def close(self) -> None:
        for resource in getattr(self, "_owned_resources", ()):
            close = getattr(resource, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001 - best-effort cleanup
                    pass
        self._owned_resources = ()
        for name in ("_store",):
            resource = getattr(self, name, None)
            close = getattr(resource, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001 - best-effort cleanup
                    pass

    # -- turn loop ---------------------------------------------------------
    def _turn(self, conversation_id: str, text: str, *, is_clarification: bool
              ) -> RuntimeTurnResult:
        conversation = self.get_conversation(conversation_id)
        clarification_ref = ""
        resume_brief: SemanticBrief | None = None
        if conversation.pending_clarification is not None:
            resolved = conversation.pending_clarification
            conversation.accepted_context.append(
                f"Clarification asked: {resolved.question} | user answered: {text}")
            clarification_ref = conversation.refs.add(
                "CLARIFICATION_ANSWER", resolved.clarification_id,
                selector=text[:200], label=resolved.question[:80]).ref_id
            conversation.clarification_refs.append(clarification_ref)
            # Deterministic resume: a reply that selects a machine-generated option binds
            # directly to the pending clarification. Only an explicit option match uses
            # this path; any other reply falls through to full semantic interpretation.
            if is_clarification:
                chosen = _match_clarification_option(resolved, text)
                if chosen is not None and conversation.last_brief is not None:
                    base = conversation.last_brief
                    resume_brief = base.model_copy(update={
                        "clarification_question": "",
                        "clarification_options": (),
                        "assumptions": tuple(dict.fromkeys((
                            *base.assumptions, f"clarification selection: {chosen}"))),
                    })
            conversation.pending_clarification = None
        conversation.messages.append(AgentMessage(role="user", text=text))
        conversation.turns += 1
        conversation.status = "RUNNING"
        user_ref = conversation.refs.add("USER_MESSAGE",
                                         f"{conversation_id}:{conversation.turns}",
                                         label=text[:80])
        conversation.events.emit("USER_MESSAGE", conversation_id=conversation_id,
                                 run_id=self._run_id, turn=conversation.turns,
                                 parent_refs=(user_ref.ref_id,), detail=text[:200])
        resolved_entities = self._resolve_entities(text)
        if resume_brief is not None:
            brief = resume_brief
            conversation.events.emit(
                "CLARIFICATION_RESUMED", conversation_id=conversation_id,
                run_id=self._run_id, turn=conversation.turns,
                detail="clarification option bound without a semantic model call")
        else:
            with self._latency(conversation, "semantic"):
                brief = self._interpreter.brief(
                    message=text, history=self._history(conversation),
                    resolved_entities=tuple(resolved_entities),
                    unknowns=tuple(conversation.accepted_context[-3:]),
                    today=self._today().isoformat())

        if brief.clarification_question and not self._already_clarified(
                conversation, brief.clarification_question):
            pending = PendingClarification(
                clarification_id=self._id("clarify"), question=brief.clarification_question,
                options=brief.clarification_options, reason="material ambiguity",
                kind="GENERAL")
            conversation.pending_clarification = pending
            # Retain the brief that produced the question so a machine-generated option
            # selection can resume deterministically on the next turn.
            conversation.last_brief = brief
            conversation.status = "WAITING_FOR_USER"
            conversation.messages.append(AgentMessage(role="agent", text=pending.question))
            conversation.events.emit("CLARIFICATION", conversation_id=conversation_id,
                                     run_id=self._run_id, turn=conversation.turns,
                                     detail=pending.question[:200])
            trace = self._build_trace(text, conversation, (), None, pending.question)
            self._remember_trace(conversation, trace)
            self._persist(conversation)
            return RuntimeTurnResult(conversation_id, "WAITING_FOR_USER", pending.question,
                                     pending, (), (), None, trace)

        with self._latency(conversation, "goal_construction"):
            goal = self._new_goal(conversation, brief, text, user_ref, clarification_ref)
        with self._latency(conversation, "planner_total"):
            self._run_planner(conversation, goal, brief)

        assessments = tuple(item for item in conversation.assessments
                            if any(need.need_id == item.need_id
                                   for need in conversation.needs))
        self._project_need_statuses(conversation)
        with self._latency(conversation, "verification"):
            coverage_map = obligation_coverage(goal, tuple(conversation.needs), assessments,
                                               conversation.artifacts.all())
            coverage = self._judge.summarize(goal, tuple(conversation.needs), assessments,
                                             obligation_coverage=coverage_map,
                                             closed_needs=self._closed_needs(conversation))
            coverage = self._with_attempt_gaps(conversation, coverage)
            conversation.last_coverage = coverage
        with self._latency(conversation, "claim_grounding"):
            claims = build_claims(goal, tuple(conversation.needs),
                                  conversation.artifacts.all(), assessments)
            claims = validate_claims(claims, conversation.artifacts.all(), conversation.refs)
        for claim in claims:
            conversation.events.emit("CLAIM_ACCEPTED", conversation_id=conversation_id,
                                     run_id=self._run_id, turn=conversation.turns,
                                     goal_id=goal.goal_id, need_id="",
                                     detail=f"{claim.claim_id} [{claim.claim_type}]")
        with self._latency(conversation, "state_projection"):
            state = self._projector.project_goal(coverage, claims,
                                                 coverage.accepted_artifact_ids)
        conversation.events.emit("STATE_TRANSITION", conversation_id=conversation_id,
                                 run_id=self._run_id, turn=conversation.turns,
                                 goal_id=goal.goal_id, detail=state.status)
        with self._latency(conversation, "response_composition"):
            answer = self._composer.compose(
                message=text, goal=goal, claims=claims,
                artifacts=conversation.artifacts.all(), coverage=coverage,
                assumptions=brief.assumptions)
        conversation.messages.append(AgentMessage(role="agent", text=answer))
        conversation.status = state.status
        self._record_entities(conversation, resolved_entities)
        self._record_candidates(conversation, brief, text)
        with self._latency(conversation, "trace_build"):
            trace = self._build_trace(text, conversation, claims, coverage, answer)
        self._remember_trace(conversation, trace)
        with self._latency(conversation, "persistence"):
            self._persist(conversation)
        return RuntimeTurnResult(conversation_id, state.status, answer, None, claims,
                                 conversation.artifacts.all(), coverage, trace)

    # -- planner loop ------------------------------------------------------
    def _run_planner(self, conversation: RuntimeConversation, goal: Goal,
                     brief: SemanticBrief) -> None:
        with self._latency(conversation, "planner_context"):
            context = self._planner_context(conversation)
        # Durable attempt identity: an action already attempted for a Need is not repeated
        # blindly after restart. Uncertain external work in particular is never assumed
        # safe to retry.
        attempted: set[tuple[str, str]] = {
            (item.need_id, item.capability) for item in conversation.attempts
            if item.need_id and item.capability}
        seen_gap_signatures: set[frozenset] = set()
        replans_used = 0
        with self._latency(conversation, "planner_initial"):
            new_needs = self._planner.initial_needs(goal=goal, brief=brief, context=context)
        for need in new_needs:
            self._upsert_need(conversation, need)
            conversation.events.emit("NEED_CREATED", conversation_id=conversation.conversation_id,
                                     run_id=self._run_id, turn=conversation.turns,
                                     goal_id=goal.goal_id, need_id=need.need_id,
                                     need_revision=need.revision,
                                     detail=need.proposed_capability or need.objective[:80])
        for iteration in range(_MAX_ITERATIONS):
            with self._latency(conversation, "planner_context",
                               detail=f"iteration={iteration}"):                context = self._planner_context(
                    conversation, budget_remaining=_MAX_ITERATIONS - iteration)
            context.attempted = attempted
            with self._latency(conversation, "planner_scheduler"):
                action = self._planner.next_action(goal=goal, needs=tuple(conversation.needs),
                                                   artifacts=conversation.artifacts.all(),
                                                   context=context)
            if action is None:
                coverage = self._summarize(conversation, goal)
                if coverage.core_goal_supported:
                    break
                gaps = tuple(dict.fromkeys((*coverage.gaps, *self._attempt_gaps(conversation))))
                # Bounded replanning: recovery is preserved, but a turn may not chain an
                # unbounded number of full (expensive) semantic replans. A repeated
                # identical gap signature cannot produce new information, so it is a
                # deterministic continuation rather than another model call.
                gap_signature = frozenset(gaps)
                if replans_used >= _MAX_REPLANS:
                    conversation.events.emit(
                        "REPLAN_SKIPPED", conversation_id=conversation.conversation_id,
                        run_id=self._run_id, turn=conversation.turns,
                        goal_id=goal.goal_id,
                        detail=f"replan budget exhausted ({_MAX_REPLANS})")
                    break
                if gap_signature in seen_gap_signatures:
                    conversation.events.emit(
                        "REPLAN_SKIPPED", conversation_id=conversation.conversation_id,
                        run_id=self._run_id, turn=conversation.turns,
                        goal_id=goal.goal_id,
                        detail="deterministic continuation: identical gap signature")
                    break
                seen_gap_signatures.add(gap_signature)
                replans_used += 1
                with self._latency(conversation, "planner_replan", detail=f"gaps={len(gaps)}"):
                    added = self._planner.add_needs(
                        goal=goal, brief=brief, existing=tuple(conversation.needs),
                        artifacts=conversation.artifacts.all(), gaps=gaps,
                        context=context)
                if not added:
                    break
                for need in added:
                    self._upsert_need(conversation, need)
                    conversation.events.emit(
                        "NEED_CREATED", conversation_id=conversation.conversation_id,
                        run_id=self._run_id, turn=conversation.turns,
                        goal_id=goal.goal_id, need_id=need.need_id,
                        need_revision=need.revision,
                        detail=f"replan: {need.proposed_capability or need.objective[:60]}")
                continue

            need = self._need(conversation, action.need_id)
            attempted.add((action.need_id, action.request.capability))
            self._execute(conversation, goal, need, action)

        self._refresh_assessments(conversation, goal)

    def _execute(self, conversation: RuntimeConversation, goal: Goal, need: Need | None,
                 action: PlannerAction) -> None:
        attempts = conversation.steps
        tool = self._registry.get(action.request.capability)
        decision = PlannerDecision(
            decision_id=self._id("decision"), iteration=len(conversation.decisions) + 1,
            action="REQUEST_TOOL", need_id=action.need_id, request=action.request,
            rationale=action.rationale)
        conversation.decisions.append(decision)
        attempt_id = self._id("attempt")
        if tool is None:
            outcome = ToolOutcome(recovery_code="UNSUPPORTED_CAPABILITY",
                                  detail=f"no tool named {action.request.capability!r}")
        else:
            bindings = ()
            if need is not None:
                with self._latency(conversation, "binding_resolution", need_id=need.need_id):
                    bindings, binding_gaps = resolve_bindings_with_gaps(
                        need, artifacts=conversation.artifacts.all(),
                        export_refs=conversation.export_refs,
                        by_need=artifact_index(tuple(conversation.needs)),
                        accepted_types=tool.contract.accepts)
                for gap in binding_gaps:
                    conversation.events.emit(
                        "BINDING_REJECTED", conversation_id=conversation.conversation_id,
                        run_id=self._run_id, turn=conversation.turns,
                        goal_id=goal.goal_id, need_id=need.need_id, detail=gap)
                    need.unsatisfied_inputs = tuple(dict.fromkeys(
                        (*need.unsatisfied_inputs, "INCOMPATIBLE_BINDING")))
                if bindings:
                    need = apply_bindings(need, bindings)
                    self._upsert_need(conversation, need)
                    for binding in bindings:
                        conversation.events.emit(
                            "BINDING_SELECTED", conversation_id=conversation.conversation_id,
                            run_id=self._run_id, turn=conversation.turns,
                            goal_id=goal.goal_id, need_id=need.need_id,
                            detail=f"{binding.name} <- {binding.source_export_id} "
                                   f"from {binding.source_need_id or binding.source_artifact_id}")
            request = action.request
            if bindings:
                from app.artifact_runtime.bindings import binding_refs
                request = request.model_copy(update={
                    "input_refs": tuple(dict.fromkeys(
                        (*request.input_refs, *binding_refs(bindings))))})
            conversation.events.emit(
                "ACTION_ADMITTED", conversation_id=conversation.conversation_id,
                run_id=self._run_id, turn=conversation.turns, goal_id=goal.goal_id,
                need_id=need.need_id if need else "", request_id=request.request_id,
                attempt_id=attempt_id,
                detail=f"{tool.name} admitted (cost={tool.contract.cost})")
            # Durable execution intent is persisted before the side effect.
            pending = ToolAttempt(
                attempt_id=attempt_id, request_id=request.request_id,
                need_id=need.need_id if need else "", capability=tool.name,
                status="INTERRUPTED", outcome_code="INTERRUPTED",
                detail="started", external_effect_possible=bool(tool.contract.cost > 1))
            conversation.attempts.append(pending)
            with self._latency(conversation, "persistence", detail="pre_tool"):
                self._persist(conversation)
            context = self._tool_context(conversation)
            try:
                with self._latency(conversation, "tool_execute", detail=tool.name,
                                   need_id=need.need_id if need else "",
                                   request_id=request.request_id, attempt_id=attempt_id):
                    raw_outcome = tool.run(request, context)
                outcome = normalize_outcome(tool.name, raw_outcome)
            except Exception as error:  # noqa: BLE001 - central exception -> safe outcome
                outcome = normalize_outcome(tool.name, None, error)

        # Verify scope before accepting evidence; a Tool's declared scope is never trusted.
        upstream = tuple(
            artifact for artifact in conversation.artifacts.all()
            if artifact.artifact_id in {
                item for item in (need.linked_artifacts if need else ())})
        verified: list[RuntimeArtifact] = []
        for artifact in outcome.artifacts:
            requested = (need.required_scope if need else None) or artifact.requested_scope
            lineage_upstream = tuple(
                conversation.artifacts.maybe(parent) for parent in artifact.lineage)
            parents = tuple(item for item in lineage_upstream if item is not None) or upstream
            with self._latency(conversation, "scope_verification",
                               detail=f"{artifact.artifact_id}"):
                verifications = verify_artifact_scope(
                    requested, artifact,
                    receipt=artifact.metadata.get("execution_receipt"),
                    upstream=parents)
            updated = artifact.model_copy(update={"scope_verifications": verifications})
            conversation.artifacts.update(updated)
            verified.append(updated)
            for verification in verifications:
                conversation.events.emit(
                    "SCOPE_VERIFIED", conversation_id=conversation.conversation_id,
                    run_id=self._run_id, turn=conversation.turns,
                    goal_id=goal.goal_id, need_id=need.need_id if need else "",
                    detail=f"{updated.artifact_id}:{verification.dimension}="
                           f"{verification.status}")

        self._add_artifacts(conversation, tuple(verified))
        # Surface bounded sub-phase timings reported by a tool's execution receipt
        # (for example Safe-IR compilation and database execution) as native events.
        for artifact in outcome.artifacts:
            receipt = artifact.metadata.get("execution_receipt") or {}
            for phase, key in (("ir_compile", "ir_compile_ms"),
                               ("db_execute", "db_execute_ms")):
                if key in receipt:
                    try:
                        conversation.events.emit(
                            "LATENCY", conversation_id=conversation.conversation_id,
                            run_id=self._run_id, turn=conversation.turns,
                            goal_id=goal.goal_id,
                            need_id=need.need_id if need else "",
                            detail=f"{phase}: {float(receipt[key]):.1f}ms ({artifact.artifact_id})",
                            phase=phase, duration_ms=round(float(receipt[key]), 3))
                    except (TypeError, ValueError):
                        continue
        if need is not None:
            need.linked_artifacts = tuple(dict.fromkeys(
                (*need.linked_artifacts, *(item.artifact_id for item in verified))))
        attempt = attempt_for(
            attempt_id=attempt_id, request_id=action.request.request_id,
            need_id=need.need_id if need else "", capability=action.request.capability,
            outcome=outcome, artifacts=tuple(verified),
            binding_ids=tuple(binding.binding_id for binding in (
                need.input_bindings if need else ())))
        conversation.attempts = [item for item in conversation.attempts
                                 if item.attempt_id != attempt_id]
        conversation.attempts.append(attempt)
        if need is not None:
            need.attempts = (*need.attempts, attempt)
            need.status = "IN_PROGRESS"
            if outcome.recovery_code:
                need.unsatisfied_inputs = tuple(dict.fromkeys(
                    (*need.unsatisfied_inputs, outcome.recovery_code)))
        conversation.events.emit(
            "EXECUTION_OUTCOME", conversation_id=conversation.conversation_id,
            run_id=self._run_id, turn=conversation.turns, goal_id=goal.goal_id,
            need_id=need.need_id if need else "", request_id=action.request.request_id,
            attempt_id=attempt_id,
            detail=f"{action.request.capability}: {attempt.outcome_code}"
                   + (f" — {outcome.detail}" if outcome.detail else ""),
            data={"artifact_ids": list(attempt.artifact_ids),
                  "retryable": attempt.retryable})
        if outcome.recovery_code:
            attempts.append(f"{action.request.capability}: {outcome.recovery_code} — "
                            f"{outcome.detail}")
        for artifact in outcome.artifacts:
            if artifact.metadata.get("recovery_code"):
                attempts.append(f"{action.request.capability}: "
                                f"{artifact.metadata['recovery_code']}")
        if need is not None:
            self._assess_need(conversation, goal, need)
        for other in conversation.needs:
            if other.need_id != (need.need_id if need else None):
                self._assess_need(conversation, goal, other)
        self._project_need_statuses(conversation)
        with self._latency(conversation, "persistence", detail="post_tool"):
            self._persist(conversation)

    # -- assessment --------------------------------------------------------
    def _assess_need(self, conversation: RuntimeConversation, goal: Goal, need: Need) -> None:
        if not need.linked_artifacts:
            return
        with self._latency(conversation, "judge", need_id=need.need_id):
            assessment = self._judge.assess_need(goal, need, conversation.artifacts.all())
        conversation.assessments = [item for item in conversation.assessments
                                    if item.need_id != need.need_id]
        conversation.assessments.append(assessment)
        conversation.events.emit("JUDGE_ASSESSMENT",
                                 conversation_id=conversation.conversation_id,
                                 run_id=self._run_id, turn=conversation.turns,
                                 goal_id=goal.goal_id, need_id=need.need_id,
                                 detail=f"verdict={assessment.verdict} "
                                        f"judge={assessment.judge_outcome} "
                                        f"available={assessment.assessment_available}")

    def _refresh_assessments(self, conversation: RuntimeConversation, goal: Goal) -> None:
        for need in conversation.needs:
            if need.linked_artifacts:
                self._assess_need(conversation, goal, need)
        self._project_need_statuses(conversation)

    def _project_need_statuses(self, conversation: RuntimeConversation) -> None:
        by_need = {item.need_id: item for item in conversation.assessments}
        for need in conversation.needs:
            assessment = by_need.get(need.need_id)
            has_attempt = any(item.need_id == need.need_id for item in conversation.attempts)
            need.status = self._projector.project_need(need, assessment,
                                                       has_attempt=has_attempt)

    def _summarize(self, conversation: RuntimeConversation, goal: Goal) -> GoalCoverage:
        assessments = tuple(item for item in conversation.assessments
                            if any(need.need_id == item.need_id
                                   for need in conversation.needs))
        coverage_map = obligation_coverage(goal, tuple(conversation.needs), assessments,
                                           conversation.artifacts.all())
        return self._judge.summarize(goal, tuple(conversation.needs), assessments,
                                     obligation_coverage=coverage_map,
                                     closed_needs=self._closed_needs(conversation))

    @staticmethod
    def _closed_needs(conversation: RuntimeConversation) -> tuple[str, ...]:
        """Needs whose durable outcome says no plan of that shape can produce evidence.

        A closed Need is not a silent omission: it is disclosed through its attempt gap.
        It only stops blocking COMPLETE when the frozen obligations are verified by other
        accepted work (see ``CoverageJudge.summarize``).
        """
        closed: set[str] = set()
        produced: set[str] = set()
        status = {artifact.artifact_id: artifact.status
                  for artifact in conversation.artifacts.all()}
        for attempt in conversation.attempts:
            if attempt.need_id and any(status.get(artifact_id) in ("OK", "PARTIAL")
                                       for artifact_id in attempt.artifact_ids):
                produced.add(attempt.need_id)
        for attempt in conversation.attempts:
            if (attempt.need_id and attempt.need_id not in produced
                    and requires_replan(attempt.outcome_code)):
                closed.add(attempt.need_id)
        return tuple(sorted(closed))

    def _attempt_gaps(self, conversation: RuntimeConversation) -> tuple[str, ...]:
        gaps: list[str] = []
        seen_attempts: set[str] = set()
        for attempt in conversation.attempts:
            if attempt.attempt_id in seen_attempts:
                continue
            seen_attempts.add(attempt.attempt_id)
            if attempt.status in ("FAILED", "INTERRUPTED", "UNCERTAIN"):
                gaps.append(gap_for_attempt(attempt.need_id, attempt))
        return tuple(dict.fromkeys(gaps))

    def _with_attempt_gaps(self, conversation: RuntimeConversation,
                           coverage: GoalCoverage) -> GoalCoverage:
        extra = self._attempt_gaps(conversation)
        if not extra:
            return coverage
        return GoalCoverage(
            core_goal_supported=coverage.core_goal_supported, verdict=coverage.verdict,
            quality=coverage.quality, gaps=tuple(dict.fromkeys((*coverage.gaps, *extra))),
            missing_needs=coverage.missing_needs,
            obligation_coverage=coverage.obligation_coverage,
            missing_obligations=coverage.missing_obligations,
            conflicts=coverage.conflicts,
            judge_available=coverage.judge_available,
            accepted_artifact_ids=coverage.accepted_artifact_ids)

    # -- helpers -----------------------------------------------------------
    def _new_goal(self, conversation: RuntimeConversation, brief: SemanticBrief, text: str,
                  user_ref, clarification_ref: str = "") -> Goal:
        previous = conversation.goal
        constraint_refs: list[str] = []
        for constraint in brief.constraints:
            reference = conversation.refs.add("USER_SPAN", user_ref.target_id,
                                              selector=constraint, label=constraint)
            constraint_refs.append(reference.ref_id)
        fresh = extract_obligations(
            message=text, brief=brief, source_ref=user_ref.ref_id,
            clarification_refs=tuple(filter(None, (clarification_ref,))))
        inherited: list[str] = []
        changed: list[str] = []
        obligations = list(fresh)
        if previous is not None:
            conversation.previous_goals.append(previous)
            new_keys = {(item.kind, item.value) for item in fresh}
            for obligation in previous.obligations:
                if (obligation.kind, obligation.value) in new_keys:
                    continue
                # A follow-up inherits obligations it did not explicitly change.
                inherited.append(obligation.obligation_id)
                obligations.append(obligation.model_copy(update={"status": "OPEN"}))
            changed = [item.obligation_id for item in fresh]
        # Typed conflicts are structural facts about the frozen requirements, not a
        # Planner conclusion: they cannot be silently dropped or interpreted away.
        conflicts = detect_conflicts(scope=brief.proposed_scope,
                                     constraints=tuple(brief.constraints),
                                     obligations=tuple(obligations),
                                     today=self._today())
        # Fresh need graph: prior Artifacts remain available only through explicit
        # bindings and are re-assessed under the new Goal revision.
        conversation.needs = []
        conversation.assessments = []
        goal = Goal(
            goal_id=self._id("goal"),
            revision=(previous.revision + 1) if previous is not None else 1,
            parent_goal_id=(previous.goal_id if previous is not None else ""),
            statement=brief.goal_statement or brief.understanding or text,
            scope=brief.proposed_scope,
            obligations=tuple(obligations),
            inherited_obligations=tuple(inherited), changed_obligations=tuple(changed),
            conflicts=conflicts,
            constraint_refs=tuple(constraint_refs), constraints=tuple(brief.constraints),
            ambiguity_notes=tuple(brief.ambiguities), source_refs=(user_ref.ref_id,),
            clarification_refs=tuple(filter(None, conversation.clarification_refs)))
        conversation.goal = goal
        conversation.events.emit(
            "GOAL_REVISED" if previous is not None else "GOAL_INTERPRETED",
            conversation_id=conversation.conversation_id, run_id=self._run_id,
            turn=conversation.turns, goal_id=goal.goal_id, goal_revision=goal.revision,
            detail=goal.statement[:200],
            data={"obligations": [item.kind for item in goal.obligations],
                  "inherited": list(inherited), "changed": list(changed)})
        for conflict in conflicts:
            conversation.events.emit(
                "USER_CONFLICT", conversation_id=conversation.conversation_id,
                run_id=self._run_id, turn=conversation.turns, goal_id=goal.goal_id,
                goal_revision=goal.revision,
                detail=f"{conflict.severity}/{conflict.kind}: {conflict.description}")
        return goal

    def _upsert_need(self, conversation: RuntimeConversation, need: Need) -> None:
        for index, existing in enumerate(conversation.needs):
            if existing.need_id == need.need_id:
                conversation.needs[index] = need
                return
        conversation.needs.append(need)

    @staticmethod
    def _need(conversation: RuntimeConversation, need_id: str) -> Need | None:
        for need in conversation.needs:
            if need.need_id == need_id:
                return need
        return None

    def _add_artifacts(self, conversation: RuntimeConversation,
                       artifacts: tuple[RuntimeArtifact, ...]) -> None:
        for artifact in artifacts:
            if conversation.artifacts.maybe(artifact.artifact_id) is None:
                conversation.artifacts.add(artifact)
            for export in artifact.exports:
                if export.export_id in conversation.export_refs:
                    continue
                reference = conversation.refs.add(
                    "ARTIFACT_EXPORT", artifact.artifact_id, selector=export.export_id,
                    label=export.export_type, provenance=export.provenance)
                conversation.export_refs[export.export_id] = reference.ref_id
            conversation.events.emit(
                "ARTIFACT_REGISTERED", conversation_id=conversation.conversation_id,
                run_id=self._run_id, turn=conversation.turns,
                goal_id=conversation.goal.goal_id if conversation.goal else "",
                detail=f"{artifact.artifact_id} [{artifact.kind}/{artifact.status}]",
                data={"exports": [item.export_type for item in artifact.exports]})

    def _planner_context(self, conversation: RuntimeConversation,
                         budget_remaining: int = _MAX_ITERATIONS) -> PlannerContext:
        registry = self._registry
        capabilities = {tool.name: (tool.contract.accepts, tool.contract.produces)
                        for tool in registry.all()}
        available = {"SEARCH_QUERY", "ENTITY_MENTION", "TEAM_NAME"}
        hints: dict[str, list[str]] = defaultdict(list)
        for artifact in conversation.artifacts.all():
            for export in artifact.exports:
                available.add(export.export_type)
                ref_id = conversation.export_refs.get(export.export_id)
                if ref_id:
                    hints[export.export_type].append(ref_id)
        budget_remaining = max(0, budget_remaining)
        goal = conversation.goal
        coverage_map: dict[str, str] = {}
        gaps: tuple[str, ...] = ()
        feedback = None
        if goal is not None:
            coverage_map = obligation_coverage(
                goal, tuple(conversation.needs), tuple(conversation.assessments),
                conversation.artifacts.all())
            gaps = obligation_gaps(goal, coverage_map)
            feedback = build_feedback(
                goal=goal, needs=tuple(conversation.needs),
                artifacts=conversation.artifacts.all(),
                attempts=tuple(conversation.attempts),
                obligation_coverage=coverage_map, gaps=gaps,
                budget_remaining=budget_remaining, registry=registry)
        return PlannerContext(
            capability_names=tuple(tool.name for tool in registry.all()),
            available_input_types=tuple(sorted(available)),
            tool_capabilities=capabilities, catalog_summary=self._catalog_summary(),
            export_ref_hints={key: tuple(value) for key, value in hints.items()},
            budget_remaining=budget_remaining,
            capabilities=self._capability_views(),
            schema_tables=self._schema_views(),
            available_exports=export_views(
                conversation.artifacts.all(), artifact_index(tuple(conversation.needs))),
            feedback=feedback,
            unavailable_capabilities=unavailable_capabilities(
                tuple(conversation.attempts), registry=registry))

    def _tool_context(self, conversation: RuntimeConversation) -> ToolContext:
        return ToolContext(
            refs=conversation.refs, artifacts=conversation.artifacts, today=self._today(),
            knowledge=self._knowledge, entity_lookup=self._entity_lookup, web=self._web,
            batting=self._batting, pitching=self._pitching,
            postgres_executor=self._postgres_executor,
            parquet_executor=self._parquet_executor, parquet_glob=self._parquet_glob,
            player_names=self._player_names, field_mapping=self._field_mapping,
            roster_provider=self._roster_provider, candidate_sink=self._candidate_sink)

    def _catalog(self):
        if self._catalog_cache is None:
            from app.artifact_runtime.schema_catalog import catalog_from_registry
            self._catalog_cache = catalog_from_registry()
        return self._catalog_cache

    def _schema_views(self) -> tuple:
        if not self._schema_views_cache:
            self._schema_views_cache = schema_views(self._catalog())
        return self._schema_views_cache

    def _capability_views(self) -> tuple:
        if not self._capability_views_cache:
            self._capability_views_cache = capability_views(self._registry)
        return self._capability_views_cache

    def _catalog_summary(self) -> str:
        lines: list[str] = []
        for table in self._catalog().tables():
            field_text = ", ".join(
                f"{field.name}({field.role},{field.data_type})" for field in table.fields)
            lines.append(f"- {table.source_kind}:{table.name} grain={table.grain} "
                         f"coverage={','.join(table.coverage)} fields=[{field_text}]")
        return "\n".join(lines)

    def _resolve_entities(self, text: str) -> list[str]:
        if self._entity_lookup is None:
            return []
        found: list[str] = []
        seen: set[str] = set()
        for entity in self._entity_lookup.dictionary.entities():
            for surface in (entity.display_name, *entity.aliases):
                if len(surface) >= 2 and surface.casefold() in text.casefold():
                    if entity.entity_key not in seen:
                        seen.add(entity.entity_key)
                        found.append(entity.display_name)
                    break
        return found

    def _record_entities(self, conversation: RuntimeConversation, names: list[str]) -> None:
        for name in names:
            if name and name not in conversation.recent_entities:
                conversation.recent_entities.append(name)
        del conversation.recent_entities[:-8]

    def _record_candidates(self, conversation: RuntimeConversation, brief: SemanticBrief,
                           message: str) -> None:
        if self._candidate_sink is None or not brief.unresolved:
            return
        evidence_refs = [artifact.artifact_id for artifact in conversation.artifacts.all()
                         if artifact.artifact_id in {
                             need.linked_artifacts[-1]
                             for need in conversation.needs if need.linked_artifacts}]
        for mention in brief.unresolved:
            if self._entity_lookup is not None and self._entity_lookup.resolve(mention).resolved:
                continue
            self._candidate_sink({
                "surface": mention, "context": brief.understanding[:500],
                "proposed_type": "CONTEXT_REFERENCE", "language": "und",
                "discovered_from_query": message,
                "reason": "unresolved runtime mention",
                "originating_run": self._run_id,
                "evidence_spans": tuple(conversation.artifacts.all()[-1].references
                                        if conversation.artifacts.all() else ()),
                "source_artifact_refs": tuple(evidence_refs),
                "provenance": list(brief.research_queries[:3])})

    def _history(self, conversation: RuntimeConversation) -> str:
        parts = [f"{item.role}: {item.text}"
                 for item in conversation.messages[-_MAX_HISTORY:]]
        if conversation.goal is not None and conversation.goal.statement:
            parts.append(f"active goal: {conversation.goal.statement}")
        if conversation.goal is not None and conversation.goal.obligations:
            parts.append("frozen obligations: " + "; ".join(
                obligation.description for obligation in conversation.goal.obligations))
        if conversation.accepted_context:
            parts.extend(conversation.accepted_context[-3:])
        return "\n".join(parts)

    @staticmethod
    def _already_clarified(conversation: RuntimeConversation, question: str) -> bool:
        needle = question.strip().casefold()[:40]
        return any(needle and needle in item.casefold()
                   for item in conversation.accepted_context)

    def _build_trace(self, message: str, conversation: RuntimeConversation,
                     claims: tuple[Claim, ...], coverage: GoalCoverage | None,
                     answer: str) -> RuntimeTrace:
        sql = [artifact.structured_data["sql"] for artifact in conversation.artifacts.all()
               if isinstance(artifact.structured_data, dict)
               and artifact.structured_data.get("sql")]
        return RuntimeTrace(
            raw_query=message, goal=conversation.goal,
            needs=tuple(conversation.needs), decisions=tuple(conversation.decisions),
            artifact_summaries=tuple(
                f"{artifact.artifact_id} [{artifact.kind}/{artifact.status}] "
                f"exports={[item.export_type for item in artifact.exports]} "
                f"scope={_scope_summary(artifact)}"
                for artifact in conversation.artifacts.all()),
            assessments=tuple(conversation.assessments),
            coverage=(coverage.__dict__ if coverage else {}), claims=claims,
            events=conversation.events.all(), attempts=tuple(conversation.attempts),
            obligation_coverage=(coverage.obligation_coverage if coverage else {}),
            steps=tuple(conversation.steps) + tuple(
                decision.rationale for decision in conversation.decisions
                if decision.rationale) + tuple(
                f"{item.capability}: {item.outcome_code} [{item.status}]"
                for item in conversation.attempts),
            tool_calls=tuple(f"{decision.request.capability}({decision.need_id})"
                             for decision in conversation.decisions
                             if decision.request is not None),
            sql_statements=tuple(sql), status=conversation.status, answer=answer,
            latency=_latency_summary(conversation))

    @staticmethod
    def _remember_trace(conversation: RuntimeConversation, trace: RuntimeTrace) -> None:
        setattr(conversation, "_last_trace", trace)

    @contextmanager
    def _latency(self, conversation: RuntimeConversation, phase: str,
                 detail: str = "", **context):
        """Emit a bounded native latency event for one runtime phase.

        Only a phase name and a measured duration are recorded; never prompts, payloads
        or hidden reasoning. A raising block still records its duration and re-raises.
        """
        started = time.perf_counter()
        try:
            yield
        finally:
            duration_ms = (time.perf_counter() - started) * 1000.0
            text = f"{phase}: {duration_ms:.1f}ms"
            if detail:
                text += f" ({detail})"
            conversation.events.emit(
                "LATENCY", conversation_id=conversation.conversation_id,
                run_id=self._run_id, turn=conversation.turns,
                detail=text, phase=phase, duration_ms=round(duration_ms, 3), **context)

    def _persist(self, conversation: RuntimeConversation) -> None:
        if self._store is None:
            return
        try:
            self._store.save_object("runtime_conversation", conversation.conversation_id,
                                    self._run_id, conversation.snapshot())
            conversation.persistence_error = ""
        except Exception as error:  # noqa: BLE001 - surfaced, never silently swallowed
            conversation.persistence_error = f"{type(error).__name__}: {error}"
            conversation.steps.append(f"persistence error: {conversation.persistence_error}")
            conversation.events.emit(
                "PERSISTENCE_ERROR", conversation_id=conversation.conversation_id,
                run_id=self._run_id, turn=conversation.turns,
                detail=conversation.persistence_error)


def _scope_summary(artifact: RuntimeArtifact) -> str:
    if not artifact.scope_verifications:
        return "unverified"
    return ",".join(f"{item.dimension}:{item.status}"
                    for item in artifact.scope_verifications)


def _match_clarification_option(pending, text: str):
    """Return the machine-generated option selected by ``text``, or ``None``.

    Only an exact (case-insensitive) option match or a 1-based option index counts. Any
    other reply is free text and must fall through to semantic interpretation.
    """
    options = tuple(getattr(pending, "options", ()) or ())
    if not options:
        return None
    needle = str(text or "").strip()
    if not needle:
        return None
    for option in options:
        if needle.casefold() == str(option).strip().casefold():
            return str(option)
    if needle.isdigit():
        index = int(needle)
        if 1 <= index <= len(options):
            return str(options[index - 1])
    return None


def _latency_summary(conversation: RuntimeConversation) -> dict[str, float]:
    """Aggregate this turn's native LATENCY events into a phase -> total-ms map."""
    summary: dict[str, float] = {}
    current_turn = conversation.turns
    for event in conversation.events.all():
        if event.event_type != "LATENCY" or not event.phase:
            continue
        if event.turn != current_turn:
            continue
        summary[event.phase] = round(
            summary.get(event.phase, 0.0) + float(event.duration_ms or 0.0), 3)
    return summary
