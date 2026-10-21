"""The production runtime engine: one loop for chat and developer mode.

User Message -> Semantic -> Goal/Requirements -> Planner -> Need Graph -> Binder ->
Capability Execution -> Artifact -> Judge -> AnswerProjection -> Response.
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.capabilities.analytics.catalog import CATALOG, SOURCE_PARQUET, SOURCE_POSTGRES
from app.capabilities.analytics.executor import AnalyticalExecutor
from app.capabilities.dynamic_fact import DynamicFactTool
from app.capabilities.entity_resolution import EntityResolutionTool
from app.capabilities.knowledge import SharedKnowledgeTool
from app.capabilities.analytics.tool import LocalAnalyticsTool
from app.capabilities.artifact_projection import ArtifactProjectionTool
from app.capabilities.python_compute import PythonComputeTool
from app.capabilities.registry import CapabilityRegistry
from app.capabilities.web_research import WebResearchTool
from app.config import RuntimeConfig, get_config
from app.contracts.models import (
    AnswerProjection,
    Artifact,
    Clarification,
    GlobalOperation,
    Goal,
    GoalStatus,
    NeedCriticality,
    NeedStatus,
    PlannerNeed,
    PropositionKind,
    RequirementKind,
    SemanticInterpretation,
    Scope,
    StatusConsistency,
    ToolOutcome,
    ToolRequest,
    TurnType,
    new_id,
)
from app.knowledge.directory import PlayerDirectory
from app.knowledge.store import KnowledgeStore
from app.llm.client import LLMClient
from app.observe.diagnostics import DiagnosticRecorder, LatencyTracker, diagnostic_path
from app.observe.trace import Trace
from app.persistence.store import PersistenceStore
from app.planner.planner import Planner, derive_criticality
from app.response.composer import ResponseComposer
from app.response.answer_contract import build_answer_package
from app.runtime.binder import RuntimeBinder
from app.runtime.context import ExecutionContext, SystemClock
from app.runtime.conversation import ConversationManager
from app.runtime.orchestrator import Orchestrator, OrchestratorAction, OrchestratorDecision
from app.semantic.entities import (
    build_entity_references,
    is_contextual_surface,
    mentions_from_requirements,
    strip_context_contamination,
)
from app.semantic.goals import RequirementBuilder
from app.semantic.interpreter import SemanticInterpreter
from app.verification.governed_state import GovernedStateError, governed_limited_fallback
from app.verification.judge import Judge
from app.verification.propositions import build_propositions


class IngressError(ValueError):
    """Raised for input that must not enter the runtime state."""


def validate_ingress(text: str) -> str:
    if not isinstance(text, str):
        raise IngressError("input must be text")
    if not text.strip():
        raise IngressError("empty input")
    for ch in text:
        code = ord(ch)
        if code == 0:
            raise IngressError("NUL byte is not allowed")
        if 0xD800 <= code <= 0xDFFF:
            raise IngressError("illegal Unicode surrogate is not allowed")
    if unicodedata.normalize("NFC", text) != text:
        text = unicodedata.normalize("NFC", text)
    return text


@dataclass
class TurnResult:
    session_id: str
    goal: Goal
    semantic: SemanticInterpretation
    projection: AnswerProjection
    answer: str
    needs: list[PlannerNeed] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    outcomes: list[ToolOutcome] = field(default_factory=list)
    rejected_needs: list[dict[str, Any]] = field(default_factory=list)
    planner_notes: list[str] = field(default_factory=list)
    trace: Trace | None = None
    # Presentation telemetry is not part of immutable Judge authority.
    consistency: StatusConsistency | None = None
    orchestrator_decisions: list[OrchestratorDecision] = field(default_factory=list)
    lifecycle_usage: dict[str, int] = field(default_factory=dict)


class Engine:
    def __init__(self, config: RuntimeConfig | None = None):
        self.config = config or get_config()
        self.config.ensure_dirs()
        self.clock = SystemClock()
        self.llm = LLMClient(self.config.llm)
        self.catalog = CATALOG
        self.executor = AnalyticalExecutor(self.config.postgres, self.config.parquet.archive_path)
        self.directory = PlayerDirectory(self.executor)
        self.knowledge = KnowledgeStore(
            candidate_path=self.config.runtime_dir / "candidate_knowledge.jsonl",
            active_path=self.config.runtime_dir / "knowledge_active.json",
        )
        self.registry = CapabilityRegistry()
        self.registry.register(EntityResolutionTool(self.knowledge, self.directory, self.llm))
        self.registry.register(LocalAnalyticsTool(self.executor, self.llm, parquet_archive=self.config.parquet.archive_path))
        self.registry.register(WebResearchTool(self.config.web, self.llm))
        self.registry.register(DynamicFactTool(self.config, self.llm))
        self.registry.register(SharedKnowledgeTool(self.knowledge))
        self.registry.register(PythonComputeTool(self.llm))
        self.registry.register(ArtifactProjectionTool())
        self.planner = Planner(self.llm, self.registry, self.catalog)
        self.interpreter = SemanticInterpreter(self.llm, knowledge=self.knowledge)
        self.requirements = RequirementBuilder()
        self.binder = RuntimeBinder(self.registry)
        self.judge = Judge(llm=self.llm)
        self.composer = ResponseComposer(self.llm)
        store = PersistenceStore(self.config.runtime_dir / "conversations.db")
        self.conversation = ConversationManager(store)
        self.availability = self._availability()

    # ------------------------------------------------------------------ doctor
    def _availability(self) -> dict[str, bool]:
        postgres_ok = False
        try:
            result = self.executor.run("SELECT 1 AS ok", SOURCE_POSTGRES)
            postgres_ok = not result.error
        except Exception:  # noqa: BLE001
            postgres_ok = False
        parquet_ok = False
        try:
            from pathlib import Path

            parquet_ok = any(Path(self.config.parquet.archive_path).glob("*.parquet"))
        except Exception:  # noqa: BLE001
            parquet_ok = False
        self.source_status = {"postgres": postgres_ok, "parquet": parquet_ok}
        return {
            "entity_resolution": True,
            "local_analytics": postgres_ok or parquet_ok,
            "web_research": True,
            "dynamic_fact": True,
            "shared_knowledge": True,
            "python_compute": True,
        }

    def doctor(self) -> dict[str, Any]:
        report: dict[str, Any] = {
            "llm_configured": self.llm.configured,
            "model": self.config.llm.runtime_model,
            "postgres": self.source_status["postgres"],
            "parquet": self.source_status["parquet"],
            "knowledge_items": len(self.knowledge.items),
            "capabilities": self.availability,
        }
        if self.source_status["postgres"]:
            coverage = self.executor.run(
                "SELECT min(game_date) AS first, max(game_date) AS last, count(*) AS pitches FROM public.statcast_pitches",
                SOURCE_POSTGRES,
            )
            if coverage.rows:
                report["statcast_pitches"] = coverage.rows[0]
        return report

    # ------------------------------------------------------------------ main
    def ask(self, session_id: str, text: str, *, trace_level: str = "summary") -> TurnResult:
        self.llm.usage_records.clear()
        self.llm.usage_sink = None
        text = validate_ingress(text)
        trace = Trace(trace_level)
        latency = LatencyTracker()
        trace.record("ingress", "validated user message", query=text, length=len(text))

        started = time.monotonic()
        prepared = self.conversation.prepare(session_id, text)
        latency.record("conversation", _ms(started))
        trace.record("conversation", f"turn classified as {prepared.turn_type.value}",
                     parent_goal=prepared.parent_goal.id if prepared.parent_goal else None,
                     clarification=bool(prepared.clarification))

        started = time.monotonic()
        from app.semantic.clarification import apply_semantic_delta, make_semantic, target_digest
        pending = prepared.clarification
        parent_goal = prepared.parent_goal
        resolved_goal = None
        forced_clarification = None
        if pending is not None and pending.candidate_id and parent_goal is not None:
            if (not pending.requirement_id or not pending.source_span or
                    pending.target_digest != target_digest(parent_goal, pending.requirement_id,
                                                          pending.candidate_id, pending.source_span)):
                raise IngressError('stale analytical clarification')
            if text in pending.options:
                index = pending.options.index(text)
                if index >= len(pending.option_actions):
                    raise IngressError('clarification has no backend semantic action')
                resolved_goal = apply_semantic_delta(parent_goal, pending, pending.option_actions[index], text)
                semantic = make_semantic(resolved_goal, text)
            else:
                # Arbitrary free text is never a semantic action by itself. The model
                # may propose a typed operation; only the next backend option confirms it.
                resolved_goal = parent_goal.model_copy(deep=True)
                resolved_goal.id = new_id('goal')
                for proposition in resolved_goal.propositions:
                    proposition.goal_id = resolved_goal.id
                resolved_goal.parent_goal_id = parent_goal.id
                resolved_goal.turn_type = TurnType.CLARIFICATION_ANSWER.value
                semantic = make_semantic(resolved_goal, text)
                if not pending.confirmation_requested:
                    proposal = self.interpreter.propose_clarification_action(text, pending)
                    prefix = f'I understood that as {proposal}. ' if proposal else ''
                    forced_clarification = pending.model_copy(update={
                        'goal_id': resolved_goal.id,
                        'target_digest': target_digest(resolved_goal, pending.requirement_id,
                                                       pending.candidate_id, pending.source_span),
                        'question': prefix + 'Please confirm the semantic effect by selecting an option.',
                        'confirmation_requested': True,
                    })
                else:
                    semantic.structured_hints['ga04_no_reclarify'] = True
        else:
            semantic = self.interpreter.interpret(
                prepared.effective_query,
                conversation_context=prepared.interpreter_context,
                clarification=(
                    {"question": pending.question, "options": pending.options}
                    if pending else None
                ),
            )
        if pending is not None and parent_goal is not None and resolved_goal is None:
            semantic.raw_query = parent_goal.raw_query
            semantic.structured_hints['clarification_answer'] = text
            semantic.turn_type = TurnType.CLARIFICATION_ANSWER.value
        elif resolved_goal is None:
            # F1 (ADR 0018): merge the interpreter's mentions with accepted conversation
            # context *before* requirements are built, so a verified contextual identity
            # can never regress to an unresolved pronoun at the goal/request boundary.
            # An INDEPENDENT turn only becomes a follow-up on a deterministic contextual
            # signal; otherwise context is withheld so stale entities cannot leak in.
            state = self.conversation.get(session_id)
            contextual_surface = any(
                mention.carried or _is_contextual_mention(mention.mention)
                for mention in semantic.entities
            )
            prepared_follow = prepared.turn_type == TurnType.FOLLOW_UP
            signal = (
                contextual_surface
                or any(mention.carried for mention in semantic.entities)
            )
            if parent_goal is None and (prepared_follow or signal):
                active = state.active_goal()
                if active is not None:
                    parent_goal = active
                    semantic.turn_type = TurnType.FOLLOW_UP.value
                else:
                    semantic.turn_type = TurnType.INDEPENDENT_GOAL.value
            else:
                semantic.turn_type = prepared.turn_type.value
            if semantic.turn_type == TurnType.INDEPENDENT_GOAL.value:
                semantic = strip_context_contamination(semantic, state=state, parent_goal=state.active_goal())
                parent_goal = None
            semantic.entity_references = build_entity_references(
                semantic, state=state, parent_goal=parent_goal
            )
        latency.record("semantic", _ms(started))
        trace.record("semantic", "interpreted", understanding=semantic.understanding,
                     entities=[e.mention for e in semantic.entities],
                     entity_references=[ref.model_dump(exclude={"reference"}) for ref in semantic.entity_references],
                     constraints=[{c.kind: c.value} for c in semantic.constraints],
                     analysis=[a.objective for a in semantic.analysis_requests],
                     evidence=[e.objective for e in semantic.evidence_requests])

        started = time.monotonic()
        goal = resolved_goal or self.requirements.build(semantic, parent_goal=parent_goal, turn_type=semantic.turn_type, anchor_date=self.clock.today())
        # ADR 0021: propositions refine the frozen requirement graph into explicit answer
        # obligations. They are derived once per Goal and carried to Judges/Web requests.
        # The LLM is only consulted for compound natural-language decomposition; it never
        # chooses tools or asserts truth, and its output is structurally validated.
        if resolved_goal is None:
            goal.propositions = build_propositions(goal, semantic, llm=self.llm)
        latency.record("requirements", _ms(started))
        trace.record("goal", "created goal", goal_id=goal.id, statement=goal.statement,
                     core_requirements=[r.statement for r in goal.requirements if r.required],
                     propositions=[p.statement for p in goal.propositions if p.mandatory])

        diagnostics = DiagnosticRecorder(diagnostic_path(self.config.runtime_dir, session_id, goal.id))
        diagnostics.record(
            "ingress", query=text, turn_type=semantic.turn_type, language=semantic.language,
            parent_goal_id=parent_goal.id if parent_goal else None,
        )
        diagnostics.record(
            "semantic", understanding=semantic.understanding,
            entities=[e.mention for e in semantic.entities],
            constraints=[{"kind": c.kind, "value": c.value} for c in semantic.constraints],
            analysis=[a.objective for a in semantic.analysis_requests],
            evidence=[e.objective for e in semantic.evidence_requests],
        )
        diagnostics.record(
            "goal", goal_id=goal.id, statement=goal.statement, raw_query=goal.raw_query,
            requirements=[{"id": r.id, "kind": r.kind.value, "required": r.required, "statement": r.statement} for r in goal.requirements],
            propositions=[p.model_dump(exclude={"coverage"}) for p in goal.propositions],
        )

        ctx = ExecutionContext(goal=goal, conversation=self.conversation.get(session_id), config=self.config, llm=self.llm, trace=trace, clock=self.clock, diagnostics=diagnostics, latency=latency)
        started = time.monotonic()
        needs, outcomes, artifacts, rejected, planner_notes = self._run_plan(ctx, semantic, trace)
        latency.record("planning_and_tools", _ms(started))

        clarification = forced_clarification or self._clarification(goal, needs, outcomes, artifacts, semantic)
        started = time.monotonic()
        orchestrator = Orchestrator()
        while True:
            try:
                projection = orchestrator.evaluate(lambda: self.judge.evaluate(
                    goal, artifacts, needs, clarification=clarification, hypotheses=semantic.hypotheses,
                    continuation_exhausted=bool(orchestrator.post_judge_replans),
                ),goal=goal)
            except GovernedStateError:
                # Last-resort integrity recovery: a bounded new Judge evaluation already
                # failed to produce a valid projection. Return a governed LIMITED result
                # instead of exposing an internal exception on the normal user path. Only
                # authority is removed; no Claim is invented and the invalid snapshot is
                # never mutated in place.
                projection = governed_limited_fallback(orchestrator.last_invalid_projection,expected=orchestrator.expected_obligations)
                orchestrator.decisions.append(OrchestratorDecision(
                    OrchestratorAction.FALLBACK,
                    "upstream governed projection failed integrity after bounded Judge recovery",
                    orchestrator.judge_attempts,
                ))
                break
            decision = orchestrator.post_judge_decision(
                projection, mandatory_requirement_ids={r.id for r in goal.requirements if r.required},
                reasonable_path=self._post_judge_path(projection, needs, outcomes),
                budget_remaining=(len(needs) < self.config.max_needs
                                  and latency.end_to_end_ms() < 45_000),
            )
            if decision.action != OrchestratorAction.REPLAN:
                break
            feedback = "Judge-governed unresolved Requirements (not new user authority):\n" + str([
                {"requirement_id": a.requirement_id, "coverage": a.coverage.value,
                 "gaps": a.coverage_gaps, "reason": a.note}
                for a in projection.requirement_assessments if a.coverage.value != "SATISFIED"
            ])
            needs, outcomes, artifacts, rejected, planner_notes = self._run_plan(
                ctx, semantic, trace, initial_state=(needs, outcomes, artifacts, rejected, planner_notes),
                judge_feedback=feedback,
            )
            clarification = forced_clarification or self._clarification(goal, needs, outcomes, artifacts, semantic)
        latency.record("judge", _ms(started))
        for entity_artifact in [a for a in artifacts if a.kind == "ENTITY_RESOLUTION"]:
            resolved = [e for e in entity_artifact.structured_data.get("resolved", []) if e.get("id")]
            if resolved:
                self.conversation.record_entities(session_id, resolved, goal_id=goal.id)
        trace.record("judge", f"goal status = {projection.goal_status.value}",
                     claims=len(projection.claims), limitations=projection.limitations,
                     unresolved=projection.unresolved_need_ids,
                     coverage={p.id: p.coverage.value for p in projection.propositions})

        started = time.monotonic()
        package = build_answer_package(goal, projection, artifacts, language=semantic.language)
        answer = self.composer.compose(goal, semantic, projection, artifacts=artifacts, trace=trace,
                                       package=package, orchestrator=orchestrator)
        latency.record("composer", _ms(started))

        # Presentation may be repaired, never used to rewrite Judge authority. Consistency
        # is lifecycle telemetry, kept outside the already-final AnswerProjection.
        started = time.monotonic()
        answer, consistency = orchestrator.enforce_consistency(
            goal, projection, package, answer, language=semantic.language,
        )
        latency.record("consistency", _ms(started))
        lifecycle_usage = {
            "composer_attempts": orchestrator.composer_attempts,
            "semantic_conformance_attempts": orchestrator.conformance_attempts,
            "post_judge_replans": orchestrator.post_judge_replans,
            "judge_evaluations": orchestrator.judge_attempts,
            "governed_retries": orchestrator.governed_retries,
        }
        diagnostics.record("orchestrator", attempts=lifecycle_usage, decisions=[
            {"action": d.action.value, "reason": d.reason, "attempt": d.attempt,
             "violations": list(d.violations), "package_id": d.package_id}
            for d in orchestrator.decisions
        ])
        for decision in orchestrator.decisions:
            trace.record("orchestrator", decision.action.value, attempt=decision.attempt,
                         reason=decision.reason, violations=list(decision.violations))

        self._record_diagnostics(diagnostics, projection, needs, outcomes, artifacts, answer, latency, planner_notes,
                                 consistency=consistency)
        diagnostics.write()
        self.conversation.commit(session_id, user_text=text, goal=goal, answer=answer, projection=projection)
        trace.record("answer", "composed response", length=len(answer))

        return TurnResult(
            session_id=session_id,
            goal=goal,
            semantic=semantic,
            projection=projection,
            answer=answer,
            needs=needs,
            artifacts=artifacts,
            outcomes=outcomes,
            rejected_needs=rejected,
            planner_notes=planner_notes,
            trace=trace,
            consistency=consistency,
            orchestrator_decisions=list(orchestrator.decisions),
            lifecycle_usage=lifecycle_usage,
        )

    def _record_diagnostics(self, diagnostics, projection, needs, outcomes, artifacts, answer, latency, planner_notes=None, *, consistency=None) -> None:
        """Persist minimized metadata; the recorder recursively excludes private content."""
        diagnostics.record(
            "needs",
            items=[
                {
                    "id": n.id, "capability": n.capability, "objective": n.objective,
                    "criticality": n.criticality.value, "status": n.status.value,
                    "depends_on": n.depends_on, "requires": n.requires,
                }
                for n in needs
            ],
        )
        diagnostics.record(
            "tool_outcomes",
            items=[
                {"need_id": o.need_id, "status": o.status, "code": o.code, "retryable": o.retryable, "message": o.message}
                for o in outcomes
            ],
        )
        diagnostics.record(
            "artifacts",
            items=[
                {
                    "id": a.id, "kind": a.kind, "need_id": a.need_id, "status": a.status,
                    "proposition_verification": (a.structured_data or {}).get("proposition_verification"),
                    "source": a.actual_scope.source if a.actual_scope else None,
                    "exports": [e.type for e in a.exports],
                }
                for a in artifacts
            ],
        )
        for artifact in artifacts:
            if artifact.kind != "WEB_EVIDENCE":
                continue
            data = artifact.structured_data or {}
            diagnostics.record(
                "web_research",
                need_id=artifact.need_id,
                objective=data.get("objective"),
                queries=data.get("queries"),
                provider=data.get("provider"),
                retrieval_status=data.get("retrieval_status"),
                documents=data.get("documents"),
                findings=[
                    {
                        "url": f.get("url"), "title": f.get("title"),
                        "retrieved_at": f.get("retrieved_at"), "content_hash": f.get("content_hash"),
                        "relevant": f.get("relevant"), "grounded": f.get("grounded"),
                        "evidence_spans": f.get("evidence_spans"),
                        "proposition_ids": f.get("proposition_ids"),
                    }
                    for f in data.get("findings", [])
                ],
                evidence_findings=data.get("evidence_findings"),
                errors=data.get("errors"),
            )
        diagnostics.record(
            "proposition_coverage",
            items=[
                {
                    "id": p.id, "statement": p.statement, "kind": p.kind.value,
                    "mandatory": p.mandatory, "coverage": p.coverage.value,
                    "supporting_artifact_ids": p.supporting_artifact_ids,
                    "supporting_claim_ids": p.supporting_claim_ids,
                    "evidence_finding_ids": p.evidence_finding_ids, "gaps": p.gaps, "note": p.note,
                }
                for p in projection.propositions
            ],
        )
        diagnostics.record(
            "judge_decision",
            goal_status=projection.goal_status.value,
            limitations=projection.limitations,
            unresolved_need_ids=projection.unresolved_need_ids,
            consistency=consistency.model_dump() if consistency else None,
            subject_tokens=sorted(getattr(self.judge, "_subject_tokens", set())),
        )
        diagnostics.record(
            "claims",
            items=[
                {
                    "id": c.id, "epistemic": c.epistemic.value, "text": c.text,
                    "proposition_ids": c.proposition_ids,
                    "citations": [{"ref_type": r.ref_type.value, "ref_id": r.ref_id, "source": r.source} for r in c.support_refs],
                }
                for c in projection.claims
            ],
        )
        diagnostics.record("final_answer", answer=answer)
        for usage in self.llm.usage_records:
            diagnostics.record("llm_usage", **usage)
        web_verify_calls = sum(
            int((artifact.structured_data or {}).get("llm_verification_calls") or 0)
            for artifact in artifacts
            if artifact.kind == "WEB_EVIDENCE"
        )
        web_query_calls = sum(
            int((artifact.structured_data or {}).get("llm_query_calls") or 0)
            for artifact in artifacts
            if artifact.kind == "WEB_EVIDENCE"
        )
        diagnostics.record(
            "llm_call_stats",
            judge_llm_calls=0,
            composer_consistency_calls=0,
            web_verification_calls=web_verify_calls,
            web_query_generation_calls=web_query_calls,
            planner_rounds=len(planner_notes or []),
        )
        diagnostics.record("latency", spans=latency.to_list(), summary=latency.summary())

    # ------------------------------------------------------------------ planning loop
    def _post_judge_path(self, projection, needs, outcomes):
        if not getattr(self.planner, "available", True):
            return False
        unresolved = {a.requirement_id for a in projection.requirement_assessments
                      if a.coverage.value != "SATISFIED"}
        families = {"local_analytics": "local", "web_research": "web", "dynamic_fact": "web"}
        tried = {families[n.capability] for n in needs
                 if n.capability in families and unresolved.intersection(n.requires)}
        available = {family for capability, family in families.items()
                     if self.availability.get(capability, False)}
        retryable = {o.need_id for o in outcomes if o.retryable}
        return bool(available - tried or any(n.id in retryable and unresolved.intersection(n.requires)
                                             for n in needs))

    def _run_plan(self, ctx: ExecutionContext, semantic: SemanticInterpretation, trace: Trace,
                  *, initial_state=None, judge_feedback=""):
        needs, outcomes, artifacts, rejected, notes = initial_state or ([], [], [], [], [])
        feedback = judge_feedback
        planner_calls = 0
        base_round = len(notes)

        max_calls = 1 if initial_state is not None else max(2, min(int(getattr(self.config, "max_rounds", 4) or 4), 5))
        while planner_calls < max_calls:
            if planner_calls > 0 and self._core_satisfied(ctx.goal, needs, artifacts):
                break
            if planner_calls > 0 and not self._needs_recovery(needs):
                break
            proposed, note, rej = self.planner.plan(
                goal=ctx.goal,
                semantic=semantic,
                artifacts=artifacts,
                needs=needs,
                outcomes=outcomes,
                feedback=feedback,
                availability=self.availability,
                round_index=base_round + planner_calls,
                max_needs=self.config.max_needs,
            )
            notes.append(note)
            rejected.extend(rej)
            for item in rej:
                trace.record("planner_rejected", f"need {item.get('id')} rejected", reason=item.get("reason"), detail=item.get("detail"))
            added = []
            existing_by_id = {n.id: n for n in needs}
            for need in proposed:
                existing = existing_by_id.get(need.id)
                if existing is None:
                    if initial_state is not None and len(needs) >= self.config.max_needs:
                        rejected.append({"id": need.id, "reason": "POST_JUDGE_NEED_BUDGET_EXHAUSTED"})
                        continue
                    needs.append(need)
                    added.append(need)
                    continue
                if existing.status in {NeedStatus.UNSATISFIED, NeedStatus.BLOCKED, NeedStatus.CANCELLED}:
                    # Graph repair: replace a terminal Need in place instead of duplicating it.
                    existing.objective = need.objective
                    existing.capability = need.capability
                    existing.constraints = need.constraints
                    existing.desired_output = need.desired_output
                    existing.input_requirements = need.input_requirements
                    existing.depends_on = need.depends_on
                    existing.requires = need.requires
                    existing.origin = "repair"
                    existing.status = NeedStatus.PENDING
                    added.append(existing)
                    trace.record("need_repaired", f"repaired terminal need {existing.id}", capability=existing.capability)
                else:
                    rejected.append({"id": need.id, "reason": "DUPLICATE_NEED_ID", "detail": need.capability})
            trace.record("planner", f"round {planner_calls}: proposed {len(proposed)}, accepted {len(added)}", note=note,
                         needs=[{"id": n.id, "capability": n.capability, "objective": n.objective, "depends_on": n.depends_on} for n in added])
            derive_criticality(needs, ctx.goal)
            self._ensure_entity_prerequisite(needs, ctx, trace)
            self._execute_pending(ctx, semantic, needs, artifacts, outcomes, trace)
            planner_calls += 1
            if not added and planner_calls > 1:
                break
            feedback = self._feedback(ctx.goal, needs, outcomes)

        return needs, outcomes, artifacts, rejected, notes

    def _needs_recovery(self, needs: list[PlannerNeed]) -> bool:
        """True when a terminal failure still leaves core work unresolved (ADR 0017)."""
        if not needs:
            return False
        return any(
            need.criticality == NeedCriticality.CORE
            and need.status in {NeedStatus.UNSATISFIED, NeedStatus.BLOCKED, NeedStatus.CANCELLED}
            for need in needs
        )

    def _ensure_entity_prerequisite(self, needs: list[PlannerNeed], ctx: ExecutionContext, trace: Trace) -> None:
        entity_requirements = [r for r in ctx.goal.requirements if r.kind == RequirementKind.ENTITY and r.required]
        if not entity_requirements:
            return
        has_resolution = any(n.capability == "entity_resolution" for n in needs)
        if has_resolution:
            return
        prerequisite = PlannerNeed(
            id=f"n_entity_{len(needs) + 1}",
            objective="resolve mentioned entities to canonical ids",
            capability="entity_resolution",
            desired_output="PLAYER_ID_SET",
            criticality_hint="core",
            requires=[r.id for r in entity_requirements],
            origin="repair",
        )
        needs.insert(0, prerequisite)
        for need in needs:
            if need.capability in {"local_analytics", "dynamic_fact"} and not need.depends_on:
                need.depends_on = [prerequisite.id]
                if "PLAYER_ID_SET" not in need.input_requirements:
                    need.input_requirements.append("PLAYER_ID_SET")
        trace.record("runtime_repair", "added deterministic entity_resolution prerequisite", need_id=prerequisite.id)

    def _execute_pending(self, ctx, semantic: SemanticInterpretation, needs, artifacts, outcomes, trace) -> None:
        ctx.artifacts = artifacts  # share the live list so the tool context can resolve exports
        pending = [n for n in needs if n.status == NeedStatus.PENDING]
        repair_attempts: dict[str, int] = {}
        guard = 0
        while pending and guard < 20:
            guard += 1
            progressed = False
            for need in list(pending):
                if need.status != NeedStatus.PENDING:
                    pending.remove(need)
                    continue
                missing_deps = []
                for dep_id in need.depends_on:
                    dep_artifacts = [a for a in artifacts if a.need_id == dep_id and a.status in {"ACCEPTED", "PARTIAL"}]
                    if not dep_artifacts:
                        missing_deps.append(dep_id)
                if missing_deps:
                    dep_needs = [n for n in needs if n.id in missing_deps]
                    terminal = all(n.status in {NeedStatus.CANCELLED, NeedStatus.UNSATISFIED, NeedStatus.BLOCKED} for n in dep_needs) and dep_needs
                    if terminal:
                        if need.criticality == NeedCriticality.CORE:
                            need.status = NeedStatus.BLOCKED
                            outcome = ToolOutcome(status="BLOCKED", code="INPUT_UNRESOLVED", message=f"dependency {missing_deps} produced no artifact", need_id=need.id, classification="WRONG_BINDING")
                            outcomes.append(outcome)
                            trace.record("need_blocked", f"{need.id} blocked by failed dependency", deps=missing_deps)
                        else:
                            need.status = NeedStatus.CANCELLED
                            trace.record("need_cancelled", f"optional {need.id} cancelled (dependency failed)", deps=missing_deps)
                        pending.remove(need)
                        progressed = True
                    continue
                binding = self.binder.bind(
                    need, needs, artifacts, requested_scope=self._binding_scope(need, ctx.goal)
                )
                if not binding.ok:
                    if (
                        need.criticality == NeedCriticality.CORE
                        and repair_attempts.get(need.id, 0) < 1
                        and any(failure.code == "EXPORT_TYPE_INCOMPATIBLE" for failure in binding.failures)
                    ):
                        converter = self._repair_conversion(need, needs, artifacts, binding, ctx, trace)
                        if converter is not None:
                            repair_attempts[need.id] = repair_attempts.get(need.id, 0) + 1
                            pending.append(converter)
                            progressed = True
                            continue
                    failure_code = _binding_failure_code(binding)
                    if need.criticality == NeedCriticality.CORE:
                        need.status = NeedStatus.BLOCKED
                        outcomes.append(ToolOutcome(status="BLOCKED", code=failure_code, message="; ".join(binding.unresolved)[:600], need_id=need.id, classification="BINDING_CONTRACT"))
                        trace.record("need_blocked", f"{need.id} failed the input binding contract",
                                     failures=[failure.code for failure in binding.failures])
                    else:
                        need.status = NeedStatus.CANCELLED
                        trace.record("need_cancelled", f"optional {need.id} cancelled (unbound inputs)", unresolved=binding.unresolved)
                    pending.remove(need)
                    progressed = True
                    continue
                for rejection in binding.rejected:
                    trace.record("binding_rejected", rejection.get("detail", ""), need_id=need.id)
                for bound in binding.bindings:
                    trace.record("binding", f"{need.id} <- {bound.export_type}", export_id=bound.export_id, artifact_id=bound.artifact_id)

                request = self._build_request(need, ctx, semantic, artifacts, binding, needs)
                trace.record("tool_request", f"{need.capability} for {need.id}", inputs=_redact_request(request))
                if ctx.diagnostics is not None:
                    ctx.diagnostics.record(
                        "tool_request",
                        need_id=need.id,
                        capability=need.capability,
                        objective=need.objective,
                        inputs=_redact_request(request),
                        bindings=[{"export_type": b.export_type, "artifact_id": b.artifact_id} for b in binding.bindings],
                    )
                started = time.monotonic()
                result = self.registry.execute(request, ctx)
                if ctx.latency is not None:
                    ctx.latency.record(f"tool:{need.capability}", _ms(started), need.id)
                outcomes.append(result.outcome)
                trace.record("tool_outcome", f"{need.capability}: {result.outcome.status}/{result.outcome.code}", message=result.outcome.message)
                if ctx.diagnostics is not None:
                    ctx.diagnostics.record(
                        "tool_outcome",
                        need_id=need.id,
                        capability=need.capability,
                        status=result.outcome.status,
                        code=result.outcome.code,
                        message=result.outcome.message,
                        retryable=result.outcome.retryable,
                    )
                if result.artifact is not None:
                    artifacts.append(result.artifact)
                    trace.record("artifact", f"{result.artifact.kind} {result.artifact.id}", rows=result.artifact.structured_data.get("row_count"),
                                 exports=[e.type for e in result.artifact.exports],
                                 actual_scope=result.artifact.actual_scope.model_dump() if result.artifact.actual_scope else {})
                for extra in result.extra_artifacts:
                    artifacts.append(extra)
                if result.outcome.status == "SUCCESS" and result.artifact is not None and result.artifact.status in {"ACCEPTED", "PARTIAL"}:
                    if result.outcome.code == "VALID_EMPTY" or result.artifact.structured_data.get("row_count") == 0:
                        need.status = NeedStatus.UNSATISFIED
                    else:
                        need.status = NeedStatus.SATISFIED
                elif result.outcome.status == "BLOCKED":
                    need.status = NeedStatus.BLOCKED
                elif result.outcome.status == "NO_DATA":
                    need.status = NeedStatus.UNSATISFIED
                else:
                    need.status = NeedStatus.UNSATISFIED
                pending.remove(need)
                progressed = True
            if not progressed:
                for need in pending:
                    if need.criticality == NeedCriticality.CORE:
                        need.status = NeedStatus.BLOCKED
                    else:
                        need.status = NeedStatus.CANCELLED
                pending = []

    def _repair_conversion(self, need, needs, artifacts, binding, ctx, trace):
        """Deterministic typed graph repair (F2, ADR 0019).

        When a consumer declares a type its dependency cannot supply, insert a registered
        converter capability between them instead of relaxing the Binder. The Planner is
        not required to discover the converter.
        """
        available: set[str] = set()
        dep_ids: list[str] = []
        for failure in binding.failures:
            if failure.code != "EXPORT_TYPE_INCOMPATIBLE" or not failure.dependency_id:
                continue
            for artifact in artifacts:
                if artifact.need_id == failure.dependency_id and artifact.status in {"ACCEPTED", "PARTIAL"}:
                    available.update(export.type for export in artifact.exports)
                    if failure.dependency_id not in dep_ids:
                        dep_ids.append(failure.dependency_id)
        if not available or not dep_ids:
            return None
        capability = self.registry.get(need.capability)
        required = set(need.input_requirements)
        if capability is not None:
            required |= set(capability.spec.required_inputs)
            if not required:
                required = set(capability.spec.accepts)
        converter_spec = _find_converter(self.registry, available, required, exclude=need.capability)
        if converter_spec is None:
            return None
        available_type = sorted(set(converter_spec.accepts) & available)[0]
        produced_type = sorted(set(converter_spec.produces) & required)[0]
        new_need = PlannerNeed(
            id=f"n_convert_{len(needs) + 1}",
            objective=f"convert {available_type} into {produced_type} for {need.id}",
            capability=converter_spec.name,
            depends_on=list(dep_ids),
            input_requirements=[available_type],
            desired_output=produced_type,
            required_evidence=f"typed {produced_type} conversion",
            criticality=need.criticality,
            criticality_hint="core" if need.criticality == NeedCriticality.CORE else "supporting",
            requires=list(need.requires),
            origin="repair",
        )
        need.depends_on = [new_need.id if dep in dep_ids else dep for dep in need.depends_on]
        need.input_requirements = [produced_type]
        need.status = NeedStatus.PENDING
        needs.append(new_need)
        derive_criticality(needs, ctx.goal)
        trace.record(
            "runtime_repair",
            f"inserted {converter_spec.name} to convert {available_type} -> {produced_type}",
            need_id=new_need.id,
            consumer=need.id,
        )
        return new_need

    def _build_request(self, need: PlannerNeed, ctx: ExecutionContext, semantic: SemanticInterpretation, artifacts: list[Artifact], binding, needs: list[PlannerNeed] | None = None) -> ToolRequest:
        inputs: dict[str, Any] = dict(need.constraints or {})
        inputs["raw_query"] = ctx.goal.raw_query
        inputs["language"] = semantic.language
        needs = needs or []
        if need.capability == "entity_resolution":
            mentions = mentions_from_requirements(ctx.goal)
            # A projected subject (F2) may also arrive as a bound SEARCH_QUERY input.
            for subject in self._bound_search_subjects(ctx, binding):
                if not any(str(m.get("mention")) == subject for m in mentions):
                    mentions.append({"mention": subject, "type_hint": "unknown"})
            inputs["mentions"] = mentions
            inputs["role"] = str(need.constraints.get("entity_role") or "BATTER").upper()
        elif need.capability == "local_analytics":
            # F5 (ADR 0020): a Need that feeds a cross-artifact comparison must enumerate
            # its complete qualified population, not a pre-truncated top-N.
            for_comparison = bool(need.constraints.get("for_comparison")) or any(
                other.capability == "python_compute" and need.id in other.depends_on for other in needs
            )
            inputs.pop("entity_sets", None)
            inputs["constraints"] = dict(need.constraints or {})
            inputs["execution_hints"] = dict(need.constraints or {})
            inputs["for_comparison"] = for_comparison
            inputs["entity_role"] = str(need.constraints.get("entity_role") or "").upper() or None
            inputs["intent"] = {
                "objective": need.objective,
                "measure": need.constraints.get("measure"),
                "population": need.constraints.get("game_population"),
                "season": need.constraints.get("season"),
                "date_range": need.constraints.get("date_range"),
                "qualification": need.constraints.get("qualification"),
            }
            from app.capabilities.analytics.conformance import independent_analytical_partitions
            analytical_goal = ctx.goal
            partitions = [g for g in independent_analytical_partitions(ctx.goal)
                          if any(r.id in need.requires and r.kind == RequirementKind.ANALYSIS for r in g.requirements)
                          and set(need.requires) <= {r.id for r in g.requirements}]
            if len(partitions) == 1:
                analytical_goal = partitions[0]
            inputs['_resolution_state'] = ctx.goal.analytical_resolution_state
            inputs['_semantic_deltas'] = [d.model_dump(mode='json') for d in ctx.goal.semantic_deltas]
            inputs["bound_user_intent"] = analytical_goal.raw_query
            inputs["analytical_requirements"] = [r.model_dump(mode="json") for r in analytical_goal.requirements if r.required]
            inputs["analytical_requirement_ids"] = list(need.requires)
            inputs["resolved_time_scope"] = dict(ctx.goal.resolved_time_scope or {})
            inputs["requested_measures"] = self._requested_measure_specs(analytical_goal)
            inputs["require_entities"] = any(
                requirement.kind == RequirementKind.ENTITY and requirement.required
                for requirement in ctx.goal.requirements
            )
        elif need.capability == "web_research":
            bound_subjects = self._bound_search_subjects(ctx, binding)
            inputs["objective"] = need.objective
            inputs["entity_names"] = _dedupe_strings(self._resolved_names(artifacts) + bound_subjects)
            # Candidate (not-yet-canonical) names are valid web subjects (ADR 0015).
            inputs["candidate_names"] = self._candidate_names(ctx.goal, semantic, artifacts)
            inputs["user_wording"] = ctx.goal.raw_query
            inputs["target_information"] = need.required_evidence or need.desired_output
            # M2.5: the canonical resolved temporal scope is shared with the capability; it
            # must not re-interpret a relative expression on its own.
            inputs["resolved_time_scope"] = dict(ctx.goal.resolved_time_scope or {})
            # ADR 0022: the research objective is verified against the goal's explicit answer
            # obligations, not just subject-token overlap. Identity/scope/value obligations
            # are covered by structured artifacts or entity resolution, so they are not
            # prose-verified here.
            inputs["propositions"] = [
                proposition.model_dump(exclude={"coverage"})
                for proposition in ctx.goal.propositions
                if proposition.mandatory and proposition.kind in _PROSE_PROPOSITION_KINDS
            ]
            inputs["mentions"] = [ref.to_mention() for ref in semantic.entity_references] or [
                {"mention": e.mention, "type_hint": e.type_hint} for e in semantic.entities
            ]
            if bound_subjects:
                inputs["search_query"] = bound_subjects[0]
            if need.constraints.get("search_terms"):
                inputs["search_terms"] = need.constraints["search_terms"]
            inputs["max_fetches"] = self.config.web.max_fetches
            inputs["language"] = semantic.language if semantic.language.startswith("zh") else _guess_language(f"{ctx.goal.raw_query} {need.objective}")
        elif need.capability == "dynamic_fact":
            inputs["relation"] = str(need.constraints.get("relation") or "TEAM_MEMBERSHIP").upper()
        elif need.capability == "shared_knowledge":
            inputs["query"] = str(need.constraints.get("query") or need.objective)
        elif need.capability == "python_compute":
            operation = _operation_for_need(need, ctx.artifacts, goal_raw=ctx.goal.raw_query)
            inputs["operation"] = operation.model_dump()
            inputs["global_comparison"] = operation.kind != "LOCAL" or bool(need.constraints.get("global_comparison"))
        return ToolRequest(
            need_id=need.id,
            capability=need.capability,
            objective=need.objective,
            inputs=inputs,
            bindings=binding.bindings,
            references=[],
            max_fetches=int(need.constraints.get("max_fetches") or self.config.web.max_fetches),
        )

    def _resolved_names(self, artifacts: list[Artifact]) -> list[str]:
        names: list[str] = []
        for artifact in artifacts:
            if artifact.kind != "ENTITY_RESOLUTION":
                continue
            for entry in artifact.structured_data.get("resolved", []):
                if entry.get("name"):
                    names.append(str(entry["name"]))
        return names

    def _bound_search_subjects(self, ctx: ExecutionContext, binding) -> list[str]:
        """F2: subjects carried by a bound SEARCH_QUERY / ENTITY_MAPPING export."""
        return bound_search_subjects(ctx, binding)

    def _candidate_names(self, goal: Goal, semantic: SemanticInterpretation, artifacts: list[Artifact]) -> list[str]:
        """Names that are usable for open research even without a canonical id.

        These come from candidate identities, the user's own wording, semantic mentions
        and the interpreter's hypotheses. They must never be used for database execution;
        the Binder enforces the canonical-id contract for those capabilities.
        """
        names: list[str] = []
        for artifact in artifacts:
            if artifact.kind != "ENTITY_RESOLUTION":
                continue
            for entry in artifact.structured_data.get("candidates", []):
                if entry.get("name"):
                    names.append(str(entry["name"]))
            for entry in artifact.structured_data.get("resolved", []):
                if entry.get("mention"):
                    names.append(str(entry["mention"]))
        for entity in semantic.entities:
            names.append(entity.mention)
        for hypothesis in semantic.hypotheses:
            names.extend(str(e) for e in hypothesis.entities if e)
        for requirement in goal.requirements:
            if requirement.kind == RequirementKind.ENTITY:
                reference = requirement.entity_ref
                if reference is not None and (reference.canonical_name or reference.surface):
                    names.append(str(reference.canonical_name or reference.surface))
                elif isinstance(requirement.value, dict) and requirement.value.get("canonical_name"):
                    names.append(str(requirement.value["canonical_name"]))
        seen: set[str] = set()
        unique: list[str] = []
        for name in names:
            key = name.strip()
            if key and key not in seen:
                seen.add(key)
                unique.append(key)
        return unique

    def _requested_measure_specs(self, goal: Goal) -> list[dict[str, Any]]:
        specs: list[dict[str, Any]] = []
        for requirement in goal.requirements:
            if requirement.kind != RequirementKind.MEASURE or not isinstance(requirement.value, dict):
                continue
            specs.append({
                "key": requirement.value.get("key"),
                "label": requirement.value.get("label"),
                "derivable": requirement.value.get("derivable"),
            })
        return specs

    def _binding_scope(self, need: PlannerNeed, goal: Goal) -> Scope:
        """The scope a consumer Need demands from its inputs.

        Used for strict binding compatibility. It is deliberately derived from the Need's
        own constraints, so a comparison Need that consumes two seasons is not forced to
        match either one.
        """
        constraints = dict(need.constraints or {})
        population = constraints.get("game_population")
        entity_names = [
            str(
                (requirement.entity_ref.canonical_name or requirement.entity_ref.surface)
                if requirement.entity_ref is not None
                else requirement.value.get("mention") or ""
            )
            for requirement in goal.requirements
            if requirement.kind == RequirementKind.ENTITY and isinstance(requirement.value, dict)
        ]
        measure_keys = [
            str(requirement.value.get("key"))
            for requirement in goal.requirements
            if requirement.kind == RequirementKind.MEASURE
            and isinstance(requirement.value, dict)
            and requirement.value.get("key")
        ]
        return Scope(
            season=_safe_int(constraints.get("season")),
            game_population=str(population).upper() if population else None,
            entity_names=[name for name in entity_names if name],
            measures=measure_keys,
        )

    def _feedback(self, goal: Goal, needs: list[PlannerNeed], outcomes: list[ToolOutcome]) -> str:
        unsatisfied = [n for n in needs if n.status in {NeedStatus.UNSATISFIED, NeedStatus.BLOCKED, NeedStatus.CANCELLED}]
        if not unsatisfied:
            return ""
        parts = ["Unsatisfied or blocked work (these are not final; choose a different permitted path):"]
        for need in unsatisfied:
            parts.append(f"- {need.id} ({need.capability}, {need.criticality.value}): {need.status.value}; objective={need.objective}")
        relevant_outcomes = [o for o in outcomes if o.need_id in {n.id for n in unsatisfied}]
        for outcome in relevant_outcomes:
            retry = "retryable" if outcome.retryable else "not retryable"
            parts.append(
                f"  outcome {outcome.status}/{outcome.code} ({retry}, {outcome.classification or 'unclassified'}): {outcome.message}"
            )
        parts.append("Guidance: an entity dictionary miss or a database contract failure does not forbid other sources. "
                     "Replace a failed database Need with a web_research or shared_knowledge Need when it could still help; "
                     "do not repeat the same failing request.")
        remaining = [
            f"- {r.statement}"
            for r in goal.requirements
            if r.required
        ]
        parts.append("User requirements to satisfy (do not raise the bar):")
        parts.extend(remaining)
        parts.append("Repair the graph: reuse accepted artifacts, replace failed capabilities, add only genuinely missing work.")
        return "\n".join(parts)

    def _core_satisfied(self, goal: Goal, needs: list[PlannerNeed], artifacts: list[Artifact]) -> bool:
        # Cheap check: every core need has an accepted artifact.
        core = [n for n in needs if n.criticality == NeedCriticality.CORE]
        if not core:
            return False
        for need in core:
            if not any(a.need_id == need.id and a.status in {"ACCEPTED", "PARTIAL"} for a in artifacts):
                return False
        return True

    def _clarification(self, goal: Goal, needs: list[PlannerNeed], outcomes: list[ToolOutcome], artifacts: list[Artifact], semantic: SemanticInterpretation | None = None) -> Clarification | None:
        # A grounded candidate can leave Requirement-local source residue. Every
        # unproved novel attribution, however compact, requires user authority.
        if semantic is not None and not semantic.structured_hints.get('ga04_no_reclarify'):
            from app.semantic.analytical_candidate import assess_candidate
            from app.semantic.clarification import closed_domain_replacements, target_digest
            for req in goal.requirements:
                if req.derivation_kind != "governed_semantic_candidate" or not isinstance(req.value, dict):
                    continue
                confirmed_sources = {d.source_span for d in goal.semantic_deltas
                                     if d.candidate_id == req.id}
                parents = [p.model_dump(mode="json") for p in goal.requirements
                           if p.id in req.parent_requirement_ids]
                for parent in parents:
                    if parent.get("kind") not in {"ANALYSIS", "EVIDENCE"}:
                        continue
                    text = str(parent.get("bound_source_clause") or goal.raw_query)
                    assessment = assess_candidate(text, req.value, parents)
                    if assessment.admitted and assessment.uncovered_spans:
                        sources = req.value.get('source_spans') or []
                        span = next((s for s in sources if isinstance(s, str) and s not in confirmed_sources
                                     and any(s in r or r in s for r in assessment.uncovered_spans)), None)
                        if span is None:
                            span = next((s for s in sources if isinstance(s, str) and s not in confirmed_sources), None)
                        if span is None:
                            continue
                        unresolved = ('; '.join(r for r in assessment.uncovered_spans
                                                 if len(sources) == 1 or r in span or span in r)
                                      or span)
                        footprint = {k: v for k, v in req.value.items()
                                     if k not in {'source_spans', 'ignored_spans', 'attributions'}}
                        alternatives = closed_domain_replacements(req.value, unresolved)
                        return Clarification(
                            question=f"For the unresolved wording {unresolved!r}, do you mean only {footprint}, or does another condition matter?",
                            options=[f"Only {footprint}; no other condition in {unresolved!r}",
                                     f"Keep {footprint} and the additional condition in {unresolved!r}",
                                     f"The interpretation {footprint} is wrong",
                                     *(label for label, _ in alternatives)],
                            option_actions=['DISCHARGE_RESIDUE', 'PRESERVE_RESIDUE',
                                            'REPLACE_INTERPRETATION',
                                            *('REPLACE_INTERPRETATION' for _ in alternatives)],
                            option_replacements=[None, None, None, *(value for _, value in alternatives)],
                            goal_id=goal.id, requirement_id=parent['id'], candidate_id=req.id,
                            source_span=span, confirmed_residue=unresolved,
                            target_digest=target_digest(goal, parent['id'], req.id, span))
        # F1 (ADR 0018): a contextual reference with more than one plausible antecedent is
        # genuine ambiguity; ask instead of guessing a canonical id.
        if semantic is not None:
            for reference in semantic.entity_references:
                if reference.ambiguous:
                    chinese = any("\u4e00" <= ch <= "\u9fff" for ch in goal.raw_query)
                    question = (
                        "你指的是前面提到的哪一位？请给出姓名或球队。"
                        if chinese
                        else "Which of the previously mentioned entities do you mean? Give a name or team."
                    )
                    return Clarification(question=question, options=list(reference.options), goal_id=goal.id)
        for outcome in outcomes:
            if outcome.code != "IDENTITY_AMBIGUOUS":
                continue
            need = next((n for n in needs if n.id == outcome.need_id), None)
            options: list[str] = []
            for artifact in artifacts:
                if artifact.need_id == outcome.need_id:
                    for item in artifact.structured_data.get("ambiguous", []):
                        options.extend(item.get("options", []))
            if options:
                chinese = any("\u4e00" <= ch <= "\u9fff" for ch in goal.raw_query)
                question = (
                    "你指的是哪位球员？请选择其中一位，或提供球队/全名。"
                    if chinese
                    else "Which player do you mean? Choose one, or give the team/full name."
                )
                return Clarification(question=question, options=options, goal_id=goal.id, need_id=outcome.need_id)
        return None


def _guess_language(text: str) -> str:
    return "zh" if any("\u4e00" <= ch <= "\u9fff" for ch in text or "") else "en"


_PROSE_PROPOSITION_KINDS = {
    PropositionKind.FACT,
    PropositionKind.EVENT,
    PropositionKind.COMPARISON,
    PropositionKind.EXPLANATION,
    PropositionKind.GROUNDED,
}


def _ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _is_contextual_mention(text: str) -> bool:
    return is_contextual_surface(text)


def bound_search_subjects(ctx: ExecutionContext, binding) -> list[str]:
    """F2: subjects carried by a bound SEARCH_QUERY / ENTITY_MAPPING export."""
    subjects: list[str] = []
    if binding is None or ctx is None:
        return subjects
    for bound in getattr(binding, "bindings", []):
        if bound.export_type not in {"SEARCH_QUERY", "ENTITY_MAPPING"}:
            continue
        export = ctx.find_export(bound.export_id)
        if export is None or not isinstance(export.payload, dict):
            continue
        payload = export.payload
        for key in ("subjects", "names"):
            for item in payload.get(key) or []:
                if item:
                    subjects.append(str(item))
        query = payload.get("query")
        if query:
            subjects.append(str(query))
        for entry in payload.get("resolved", []) or []:
            if isinstance(entry, dict) and entry.get("name"):
                subjects.append(str(entry["name"]))
    return _dedupe_strings(subjects)


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = str(value).strip()
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result


_GLOBAL_MARKERS = (
    "largest", "biggest", "maximum", "minimum", "smallest", "most", "least", "rank",
    "ranking", "top", "global", "extremum",
    "最大", "最小", "最多", "最少", "排名", "极值",
)
_DELTA_MARKERS = (
    "difference", "delta", "change", "improvement", "increase", "decrease", "gain",
    "compare", "versus", "vs",
    "差值", "相差", "变化", "提升", "下降", "增加", "减少", "比较", "对比",
)
_JOIN_MARKERS = (
    "combine", "combined", "both", "together", "along with", "as well as",
    "同时", "分别", "以及", "结合", "并且",
)
_ASCII_GLOBAL_MARKERS = tuple(marker for marker in _GLOBAL_MARKERS if all(ord(ch) < 128 for ch in marker))
_CJK_GLOBAL_MARKERS = tuple(marker for marker in _GLOBAL_MARKERS if any(ord(ch) >= 128 for ch in marker))


def _looks_global(objective: str) -> bool:
    lowered = (objective or "").lower()
    for marker in _ASCII_GLOBAL_MARKERS:
        if re.search(rf"(?<![a-z]){re.escape(marker)}(?![a-z])", lowered):
            return True
    return any(marker in lowered for marker in _CJK_GLOBAL_MARKERS)


def _looks_delta(objective: str) -> bool:
    lowered = (objective or "").lower()
    return any(marker in lowered for marker in _DELTA_MARKERS)


def _looks_join(objective: str) -> bool:
    lowered = (objective or "").lower()
    return any(marker in lowered for marker in _JOIN_MARKERS)


def _operation_for_need(need: PlannerNeed, artifacts: list[Artifact], *, goal_raw: str = "") -> GlobalOperation:
    """Infer a structured operation from the declared constraints and the real inputs.

    Explicit ``constraints.operation`` wins. Otherwise the operation is derived from the
    objective and the actual dependency enumerations; anything that cannot be classified
    is ``UNKNOWN`` and fails closed for multi-input work.
    """
    raw = (need.constraints or {}).get("operation")
    if isinstance(raw, dict):
        try:
            return GlobalOperation.model_validate(raw)
        except Exception:  # noqa: BLE001
            return GlobalOperation(kind="UNKNOWN")
    dep_ids = set(need.depends_on)
    contracts = [
        artifact.enumeration
        for artifact in artifacts
        if artifact.need_id in dep_ids and artifact.enumeration is not None
    ]
    if not contracts:
        return GlobalOperation(kind="LOCAL")
    identity = _shared_entity_kind(contracts)
    objective = f"{need.objective or ''} {goal_raw or ''}"
    if len(contracts) <= 1:
        return GlobalOperation(kind="RANK" if _looks_global(objective) else "LOCAL", identity=identity)
    if _looks_delta(objective):
        kind = "DELTA"
    elif _looks_join(objective):
        kind = "JOIN"
    elif _looks_global(objective):
        kind = "RANK"
    else:
        kind = "UNKNOWN"
    return GlobalOperation(kind=kind, identity=identity, partition_by=_infer_partition_hint(contracts))


def _shared_entity_kind(contracts) -> str:
    kinds = {contract.entity_kind for contract in contracts if contract.entity_kind}
    return next(iter(kinds)) if len(kinds) == 1 else ""


def _infer_partition_hint(contracts) -> str:
    seasons = [contract.season for contract in contracts]
    if all(season is not None for season in seasons) and len(set(seasons)) > 1:
        return "SEASON"
    sources = [contract.source for contract in contracts]
    if all(sources) and len(set(sources)) > 1:
        return "SOURCE"
    return ""


def _find_converter(registry, available: set[str], required: set[str], *, exclude: str):
    for spec in sorted(registry.specs(), key=lambda item: item.name):
        if spec.name == exclude:
            continue
        if set(spec.accepts) & available and set(spec.produces) & required:
            return spec
    return None


_BINDING_FAILURE_TO_OUTCOME = {
    "EMPTY_ENTITY_SET": "UNRESOLVED_REQUIRED_INPUT",
    "ENTITY_INCOMPATIBLE": "UNRESOLVED_REQUIRED_INPUT",
    "ARTIFACT_NOT_ACCEPTED": "UNRESOLVED_REQUIRED_INPUT",
    "EXPORT_TYPE_INCOMPATIBLE": "UNRESOLVED_REQUIRED_INPUT",
    "UNRESOLVED_REQUIRED_INPUT": "UNRESOLVED_REQUIRED_INPUT",
    "SCOPE_INCOMPATIBLE": "SCOPE_MISMATCH",
}


def _binding_failure_code(binding) -> str:
    for failure in binding.failures:
        if failure.required:
            return _BINDING_FAILURE_TO_OUTCOME.get(failure.code, failure.code)
    if binding.failures:
        return _BINDING_FAILURE_TO_OUTCOME.get(binding.failures[0].code, binding.failures[0].code)
    return "UNRESOLVED_REQUIRED_INPUT"


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit() and len(value.strip()) == 4:
        return int(value.strip())
    return None


def _redact_request(request: ToolRequest) -> dict[str, Any]:
    inputs = dict(request.inputs or {})
    inputs.pop("raw_query", None)
    return {"capability": request.capability, "objective": request.objective, "inputs": inputs,
            "bindings": [b.export_type for b in request.bindings]}
