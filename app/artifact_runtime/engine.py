"""The artifact runtime: conversation -> goal -> need graph -> planner -> artifacts.

This is the center of the v0.3 runtime. Tools are evidence-producing operators; the
planner is an artifact/dataflow planner; completion depends on goal coverage. The engine
owns conversation state, the reference graph, the artifact store and persistence.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from uuid import uuid4

from app.models.agent_runtime import AgentMessage, PendingClarification, RunStatus
from app.models.artifact_runtime import (Claim, CoverageAssessment, Goal, Need, PlannerDecision,
                                         RuntimeArtifact, RuntimeTrace, Scope, SemanticBrief,
                                         ToolRequest, utcnow)
from app.artifact_runtime.artifacts import ArtifactStore
from app.artifact_runtime.claims import build_claims
from app.artifact_runtime.planner import Planner, PlannerContext, PlannerAction, SemanticInterpreter
from app.artifact_runtime.references import ReferenceStore
from app.artifact_runtime.response import DeterministicResponseComposer, ResponseComposer
from app.artifact_runtime.roster import RosterUnavailable
from app.artifact_runtime.scope import compare_scope
from app.artifact_runtime.sufficiency import CoverageJudge, GoalCoverage
from app.artifact_runtime.tool_base import RuntimeTool, ToolContext, ToolRegistry

_MAX_ITERATIONS = 6
_MAX_HISTORY = 8


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
    pending_clarification: PendingClarification | None = None
    accepted_context: list[str] = field(default_factory=list)
    recent_entities: list[str] = field(default_factory=list)
    export_refs: dict[str, str] = field(default_factory=dict)
    steps: list[str] = field(default_factory=list)
    turns: int = 0
    last_coverage: GoalCoverage | None = None

    # -- persistence -------------------------------------------------------
    def snapshot(self) -> dict:
        return {
            "conversation_id": self.conversation_id, "status": self.status,
            "messages": [item.model_dump(mode="json") for item in self.messages],
            "references": [item.model_dump(mode="json") for item in self.refs.all()],
            "artifacts": [item.model_dump(mode="json") for item in self.artifacts.all()],
            "goal": self.goal.model_dump(mode="json") if self.goal else None,
            "previous_goals": [item.model_dump(mode="json") for item in self.previous_goals],
            "needs": [item.model_dump(mode="json") for item in self.needs],
            "assessments": [item.model_dump(mode="json") for item in self.assessments],
            "decisions": [item.model_dump(mode="json") for item in self.decisions],
            "pending_clarification": (self.pending_clarification.model_dump(mode="json")
                                      if self.pending_clarification else None),
            "accepted_context": list(self.accepted_context),
            "recent_entities": list(self.recent_entities),
            "export_refs": dict(self.export_refs), "steps": list(self.steps),
            "turns": self.turns,
        }

    @classmethod
    def restore(cls, payload: dict) -> "RuntimeConversation":
        conversation = cls(conversation_id=payload["conversation_id"])
        conversation.status = payload.get("status", "PENDING")
        conversation.messages = [AgentMessage.model_validate(item)
                                 for item in payload.get("messages", ())]
        for item in payload.get("references", ()):
            conversation.refs.put(_ReferenceModel.model_validate(item))
        for item in payload.get("artifacts", ()):
            conversation.artifacts.add(RuntimeArtifact.model_validate(item))
        if payload.get("goal"):
            conversation.goal = Goal.model_validate(payload["goal"])
        conversation.previous_goals = [Goal.model_validate(item)
                                       for item in payload.get("previous_goals", ())]
        conversation.needs = [Need.model_validate(item) for item in payload.get("needs", ())]
        conversation.assessments = [CoverageAssessment.model_validate(item)
                                    for item in payload.get("assessments", ())]
        conversation.decisions = [PlannerDecision.model_validate(item)
                                  for item in payload.get("decisions", ())]
        if payload.get("pending_clarification"):
            conversation.pending_clarification = PendingClarification.model_validate(
                payload["pending_clarification"])
        conversation.accepted_context = list(payload.get("accepted_context", ()))
        conversation.recent_entities = list(payload.get("recent_entities", ()))
        conversation.export_refs = dict(payload.get("export_refs", {}))
        conversation.steps = list(payload.get("steps", ()))
        conversation.turns = int(payload.get("turns", 0))
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
                 run_id: str = "runtime", today=date.today, id_factory=None) -> None:
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
        self._id = id_factory or (lambda prefix: f"{prefix}-{uuid4().hex}")
        self._conversations: dict[str, RuntimeConversation] = {}

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
                conversation = RuntimeConversation.restore(stored.payload)
                self._conversations[conversation_id] = conversation
                return conversation
        raise KeyError(f"Unknown conversation {conversation_id!r}")

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
        if conversation.pending_clarification is not None:
            resolved = conversation.pending_clarification
            conversation.accepted_context.append(
                f"Clarification asked: {resolved.question} | user answered: {text}")
            conversation.pending_clarification = None
        conversation.messages.append(AgentMessage(role="user", text=text))
        conversation.turns += 1
        conversation.status = "RUNNING"
        user_ref = conversation.refs.add("USER_MESSAGE",
                                         f"{conversation_id}:{conversation.turns}",
                                         label=text[:80])
        resolved_entities = self._resolve_entities(text)
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
            conversation.status = "WAITING_FOR_USER"
            conversation.messages.append(AgentMessage(role="agent", text=pending.question))
            trace = self._build_trace(text, conversation, (), None, pending.question)
            self._remember_trace(conversation, trace)
            self._persist(conversation)
            return RuntimeTurnResult(conversation_id, "WAITING_FOR_USER", pending.question,
                                     pending, (), (), None, trace)

        goal = self._new_goal(conversation, brief, user_ref)
        self._run_planner(conversation, goal, brief)

        assessments = tuple(item for item in conversation.assessments
                            if any(need.need_id == item.need_id
                                   for need in conversation.needs))
        claims = build_claims(goal, tuple(conversation.needs),
                              conversation.artifacts.all(), assessments)
        coverage = self._judge.summarize(goal, tuple(conversation.needs), assessments)
        conversation.last_coverage = coverage
        status = self._status_for(conversation, coverage)
        answer = self._composer.compose(
            message=text, goal=goal, claims=claims,
            artifacts=conversation.artifacts.all(), coverage=coverage,
            assumptions=brief.assumptions)
        conversation.messages.append(AgentMessage(role="agent", text=answer))
        conversation.status = status
        self._record_entities(conversation, resolved_entities)
        self._record_candidates(conversation, brief, text)
        trace = self._build_trace(text, conversation, claims, coverage, answer)
        self._remember_trace(conversation, trace)
        self._persist(conversation)
        return RuntimeTurnResult(conversation_id, status, answer, None, claims,
                                 conversation.artifacts.all(), coverage, trace)

    # -- planner loop ------------------------------------------------------
    def _run_planner(self, conversation: RuntimeConversation, goal: Goal,
                     brief: SemanticBrief) -> None:
        context = self._planner_context(conversation)
        attempted: set[tuple[str, str]] = set()
        new_needs = self._planner.initial_needs(goal=goal, brief=brief, context=context)
        for need in new_needs:
            self._upsert_need(conversation, need)
        for iteration in range(_MAX_ITERATIONS):
            context = self._planner_context(conversation)
            context.attempted = attempted
            action = self._planner.next_action(goal=goal, needs=tuple(conversation.needs),
                                               artifacts=conversation.artifacts.all(),
                                               context=context)
            if action is None:
                coverage = self._summarize(conversation, goal)
                if coverage.core_goal_supported:
                    break
                added = self._planner.add_needs(
                    goal=goal, brief=brief, existing=tuple(conversation.needs),
                    artifacts=conversation.artifacts.all(), gaps=coverage.gaps,
                    context=context)
                if not added:
                    break
                for need in added:
                    self._upsert_need(conversation, need)
                continue

            need = self._need(conversation, action.need_id)
            attempted.add((action.need_id, action.request.capability))
            self._execute(conversation, goal, need, action, conversation.steps)

        self._refresh_assessments(conversation, goal)

    def _execute(self, conversation: RuntimeConversation, goal: Goal, need: Need | None,
                 action: PlannerAction, steps: list[str]) -> None:
        tool = self._registry.get(action.request.capability)
        decision = PlannerDecision(
            decision_id=self._id("decision"), iteration=len(conversation.decisions) + 1,
            action="REQUEST_TOOL", need_id=action.need_id, request=action.request,
            rationale=action.rationale)
        conversation.decisions.append(decision)
        if tool is None:
            steps.append(f"no tool named {action.request.capability!r}")
            return
        context = self._tool_context(conversation)
        outcome = tool.run(action.request, context)
        self._add_artifacts(conversation, outcome.artifacts)
        if need is not None:
            need.linked_artifacts = tuple(dict.fromkeys(
                (*need.linked_artifacts, *(item.artifact_id for item in outcome.artifacts))))
            need.status = "IN_PROGRESS"
        if outcome.recovery_code:
            steps.append(f"{tool.name}: {outcome.recovery_code} — {outcome.detail}")
            if need is not None:
                need.unsatisfied_inputs = tuple(dict.fromkeys(
                    (*need.unsatisfied_inputs, outcome.recovery_code)))
        for artifact in outcome.artifacts:
            if artifact.metadata.get("recovery_code"):
                steps.append(f"{tool.name}: {artifact.metadata['recovery_code']}")
        if need is not None:
            self._assess_need(conversation, goal, need)
        # New exports may advance dependent needs; re-assess everything cheaply.
        for other in conversation.needs:
            if other.need_id != (need.need_id if need else None):
                self._assess_need(conversation, goal, other)

    # -- assessment --------------------------------------------------------
    def _assess_need(self, conversation: RuntimeConversation, goal: Goal, need: Need) -> None:
        if not need.linked_artifacts:
            return
        assessment = self._judge.assess_need(goal, need, conversation.artifacts.all())
        conversation.assessments = [item for item in conversation.assessments
                                    if item.need_id != need.need_id]
        conversation.assessments.append(assessment)
        need.status = {"SATISFIED": "SATISFIED", "PARTIAL": "PARTIAL",
                       "IRRELEVANT": "PARTIAL", "UNSATISFIED": "FAILED"}[assessment.verdict]

    def _refresh_assessments(self, conversation: RuntimeConversation, goal: Goal) -> None:
        for need in conversation.needs:
            if need.linked_artifacts:
                self._assess_need(conversation, goal, need)

    def _summarize(self, conversation: RuntimeConversation, goal: Goal) -> GoalCoverage:
        return self._judge.summarize(goal, tuple(conversation.needs),
                                     tuple(conversation.assessments))

    def _status_for(self, conversation: RuntimeConversation, coverage: GoalCoverage
                    ) -> RunStatus:
        if coverage.core_goal_supported:
            return "COMPLETE"
        linked = {artifact_id for need in conversation.needs
                  for artifact_id in need.linked_artifacts}
        useful = [artifact for artifact in conversation.artifacts.all()
                  if artifact.artifact_id in linked and artifact.status in ("OK", "PARTIAL")]
        if useful:
            return "LIMITED"
        return "FAILED"

    # -- helpers -----------------------------------------------------------
    def _new_goal(self, conversation: RuntimeConversation, brief: SemanticBrief,
                  user_ref) -> Goal:
        constraint_refs: list[str] = []
        for constraint in brief.constraints:
            reference = conversation.refs.add("USER_SPAN", user_ref.target_id,
                                              selector=constraint, label=constraint)
            constraint_refs.append(reference.ref_id)
        if conversation.goal is not None:
            conversation.previous_goals.append(conversation.goal)
        # A new goal starts a fresh need graph; prior Artifacts remain available for
        # explicit reuse by reference, but they cannot silently satisfy the new goal.
        conversation.needs = []
        conversation.assessments = []
        goal = Goal(goal_id=self._id("goal"),
                    statement=brief.goal_statement or brief.understanding or "",
                    scope=brief.proposed_scope, constraint_refs=tuple(constraint_refs),
                    constraints=tuple(brief.constraints),
                    ambiguity_notes=tuple(brief.ambiguities), source_refs=(user_ref.ref_id,))
        conversation.goal = goal
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

    def _planner_context(self, conversation: RuntimeConversation) -> PlannerContext:
        capabilities = {tool.name: (tool.contract.accepts, tool.contract.produces)
                        for tool in self._registry.all()}
        available = {"SEARCH_QUERY", "ENTITY_MENTION", "TEAM_NAME"}
        hints: dict[str, list[str]] = defaultdict(list)
        for artifact in conversation.artifacts.all():
            for export in artifact.exports:
                available.add(export.export_type)
                ref_id = conversation.export_refs.get(export.export_id)
                if ref_id:
                    hints[export.export_type].append(ref_id)
        return PlannerContext(
            capability_names=tuple(tool.name for tool in self._registry.all()),
            available_input_types=tuple(sorted(available)),
            tool_capabilities=capabilities, catalog_summary=self._catalog_summary(),
            export_ref_hints={key: tuple(value) for key, value in hints.items()},
            budget_remaining=_MAX_ITERATIONS)

    def _tool_context(self, conversation: RuntimeConversation) -> ToolContext:
        context = ToolContext(
            refs=conversation.refs, artifacts=conversation.artifacts, today=self._today(),
            knowledge=self._knowledge, entity_lookup=self._entity_lookup, web=self._web,
            batting=self._batting, pitching=self._pitching,
            postgres_executor=self._postgres_executor,
            parquet_executor=self._parquet_executor, parquet_glob=self._parquet_glob,
            player_names=self._player_names, field_mapping=self._field_mapping,
            roster_provider=self._roster_provider, candidate_sink=self._candidate_sink)
        return context

    def _catalog_summary(self) -> str:
        from app.artifact_runtime.schema_catalog import catalog_from_registry
        catalog = catalog_from_registry()
        lines: list[str] = []
        for table in catalog.tables():
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
        for mention in brief.unresolved:
            if self._entity_lookup is not None and self._entity_lookup.resolve(mention).resolved:
                continue
            self._candidate_sink({
                "surface": mention, "context": brief.understanding[:500],
                "proposed_type": "CONTEXT_REFERENCE", "language": "und",
                "discovered_from_query": message,
                "reason": "unresolved runtime mention",
                "provenance": list(brief.research_queries[:3])})

    def _history(self, conversation: RuntimeConversation) -> str:
        parts = [f"{item.role}: {item.text}"
                 for item in conversation.messages[-_MAX_HISTORY:]]
        if conversation.goal is not None and conversation.goal.statement:
            parts.append(f"active goal: {conversation.goal.statement}")
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
                f"exports={[item.export_type for item in artifact.exports]}"
                for artifact in conversation.artifacts.all()),
            assessments=tuple(conversation.assessments),
            coverage=(coverage.__dict__ if coverage else {}), claims=claims,
            steps=tuple(conversation.steps) + tuple(
                decision.rationale for decision in conversation.decisions
                if decision.rationale),
            tool_calls=tuple(f"{decision.request.capability}({decision.need_id})"
                             for decision in conversation.decisions
                             if decision.request is not None),
            sql_statements=tuple(sql), status=conversation.status, answer=answer)

    @staticmethod
    def _remember_trace(conversation: RuntimeConversation, trace: RuntimeTrace) -> None:
        setattr(conversation, "_last_trace", trace)

    def _persist(self, conversation: RuntimeConversation) -> None:
        if self._store is None:
            return
        try:
            self._store.save_object("runtime_conversation", conversation.conversation_id,
                                    self._run_id, conversation.snapshot())
        except Exception:  # noqa: BLE001 - persistence must not break a live turn
            pass
