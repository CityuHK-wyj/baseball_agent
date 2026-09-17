"""BaseballAgent: the LLM-first conversational application service.

Flow for one user turn:

    message
      -> conversation context + entity resolution
      -> LLM cognition plan (free-form understanding + tool hints, optional clarification)
      -> tool execution (knowledge / web / batting / strict local analytics)
      -> optional recovery re-plan
      -> LLM answer composition
      -> pending clarification OR answered turn

The service is frontend-agnostic: the CLI and any future UI call the same methods. It owns
conversation state and never exposes internal run/requirement ids to the user.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from uuid import uuid4

from app.agent.cognition import Cognition
from app.agent.local_analytics import LocalAnalyticsRunner
from app.knowledge.candidates import CandidateKnowledge, CandidateKnowledgeStore
from app.models.agent_runtime import (AgentMessage, AgentTrace, CognitionPlan, Conversation,
                                      EvidenceItem, PendingClarification, RunStatus,
                                      utcnow)
from app.models.knowledge import KnowledgeQuery
from app.tools.batting import BattingStatsTool, BattingStatsUnavailable
from app.tools.entity_lookup import EntityLookup
from app.tools.pitching import PitchingStatsTool
from app.tools.web_research import WebResearchTool, WebResearchUnavailable

_MAX_HISTORY_MESSAGES = 10
_MAX_RESEARCH_QUERIES = 4


@dataclass
class AgentTurnResult:
    conversation_id: str
    status: RunStatus
    answer: str
    pending_clarification: PendingClarification | None = None
    evidence: tuple[EvidenceItem, ...] = ()
    trace: AgentTrace | None = None


@dataclass
class _Gathered:
    evidence: list[EvidenceItem] = field(default_factory=list)
    recovery_codes: list[str] = field(default_factory=list)
    sql_requests: list[str] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)


class BaseballAgent:
    def __init__(self, cognition: Cognition, knowledge, entity_lookup: EntityLookup,
                 web: WebResearchTool | None = None, batting: BattingStatsTool | None = None,
                 pitching: PitchingStatsTool | None = None,
                 local: LocalAnalyticsRunner | None = None,
                 candidates: CandidateKnowledgeStore | None = None,
                 today=date.today, id_factory=None) -> None:
        self._cognition = cognition
        self._knowledge = knowledge
        self._entity_lookup = entity_lookup
        self._web = web
        self._batting = batting
        self._pitching = pitching
        self._local = local
        self._candidates = candidates
        self._today = today
        self._id = id_factory or (lambda prefix: f"{prefix}-{uuid4().hex}")
        self._conversations: dict[str, Conversation] = {}

    # -- public service API --------------------------------------------------
    def close(self) -> None:
        for resource in getattr(self, "_owned_resources", ()):
            try:
                resource.close()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass
        self._owned_resources = ()

    def start_conversation(self) -> Conversation:
        conversation = Conversation(conversation_id=self._id("conv"))
        self._conversations[conversation.conversation_id] = conversation
        return conversation

    def get_conversation(self, conversation_id: str) -> Conversation:
        conversation = self._conversations.get(conversation_id)
        if conversation is None:
            raise KeyError(f"Unknown conversation {conversation_id!r}")
        return conversation

    def get_status(self, conversation_id: str) -> RunStatus:
        return self.get_conversation(conversation_id).status

    def get_trace(self, conversation_id: str) -> AgentTrace | None:
        return getattr(self.get_conversation(conversation_id), "_last_trace", None)

    def send_message(self, conversation_id: str, text: str) -> AgentTurnResult:
        return self._turn(conversation_id, text, is_clarification_answer=False)

    def respond_to_clarification(self, conversation_id: str, text: str) -> AgentTurnResult:
        return self._turn(conversation_id, text, is_clarification_answer=True)

    # -- turn loop -----------------------------------------------------------
    def _turn(self, conversation_id: str, text: str, *, is_clarification_answer: bool
              ) -> AgentTurnResult:
        conversation = self.get_conversation(conversation_id)
        if conversation.status == "WAITING_FOR_USER" and not is_clarification_answer:
            # Treat a plain message as the clarification answer.
            is_clarification_answer = True
        if is_clarification_answer and conversation.pending_clarification is not None:
            resolved = conversation.pending_clarification
            conversation.accepted_context.append(
                f"Clarification asked: {resolved.question} | user answered: {text}")
            conversation.pending_clarification = None
        conversation.messages.append(AgentMessage(role="user", text=text))
        conversation.status = "RUNNING"
        conversation.turns += 1

        resolved_mentions = self._resolve_known_entities(text)
        plan = self._cognition.plan(
            message=text, history=self._history(conversation),
            resolved_entities=tuple(item.display_name for item in resolved_mentions),
            unknowns=tuple(conversation.accepted_context[-3:]), today=self._today().isoformat())

        if plan.clarification is not None and plan.clarification.required \
                and not self._already_clarified(conversation, plan.clarification.question):
            pending = PendingClarification(
                clarification_id=self._id("clarify"), question=plan.clarification.question,
                options=plan.clarification.options, reason=plan.clarification.reason,
                kind=plan.clarification.kind)
            conversation.pending_clarification = pending
            conversation.status = "WAITING_FOR_USER"
            conversation.last_goal = plan.user_goal or conversation.last_goal
            conversation.last_understanding = plan.understanding
            conversation.messages.append(AgentMessage(role="agent", text=pending.question))
            trace = self._trace(text, plan, _Gathered(), "WAITING_FOR_USER", pending.question)
            self._remember_trace(conversation, trace)
            return AgentTurnResult(conversation_id, "WAITING_FOR_USER", pending.question,
                                   pending, (), trace)

        gathered = self._gather(plan, resolved_mentions, session_message=text)
        self._maybe_replan(conversation, plan, gathered, text)

        if not gathered.evidence and plan.direct_answer:
            gathered.evidence.append(EvidenceItem(kind="NOTE", summary="Direct answer",
                                                  source="model", text=plan.direct_answer))

        status: RunStatus = self._status_for(gathered)
        answer = self._cognition.compose(
            message=text, understanding=plan.understanding or plan.user_goal,
            assumptions=plan.assumptions, evidence=tuple(gathered.evidence))
        conversation.messages.append(AgentMessage(role="agent", text=answer))
        conversation.status = status
        conversation.last_goal = plan.user_goal or conversation.last_goal
        conversation.last_understanding = plan.understanding
        self._record_entities(conversation, tuple(item.display_name for item in resolved_mentions))
        self._record_unknowns(conversation, plan, gathered, text)
        trace = self._trace(text, plan, gathered, status, answer)
        self._remember_trace(conversation, trace)
        return AgentTurnResult(conversation_id, status, answer, None,
                               tuple(gathered.evidence), trace)

    # -- gathering -----------------------------------------------------------
    def _gather(self, plan: CognitionPlan, resolved_mentions, *, session_message: str) -> _Gathered:
        gathered = _Gathered()
        if plan.needs_knowledge or plan.knowledge_queries:
            self._gather_knowledge(plan, gathered)
        if plan.needs_batting_stats or plan.batting_entity_names or plan.batting_team:
            self._gather_batting(plan, gathered)
        if plan.needs_pitching_stats or plan.pitching_entity_names:
            self._gather_pitching(plan, gathered)
        for hint in plan.local_metrics:
            self._gather_local(hint, resolved_mentions, gathered, plan)
        queries = list(plan.research_queries)
        if not gathered.evidence and not queries:
            queries.append(session_message)
        self._gather_web(queries, gathered)
        return gathered

    def _gather_knowledge(self, plan: CognitionPlan, gathered: _Gathered) -> None:
        queries = list(plan.knowledge_queries) or [plan.user_goal]
        for query in queries[:2]:
            gathered.tool_calls.append(f"knowledge.search({query})")
            matches = self._knowledge.search(query, max_items=4)
            for match in matches[:4]:
                item = match.item
                gathered.evidence.append(EvidenceItem(
                    kind="KNOWLEDGE", summary=item.title, source="shared-knowledge",
                    reference=item.knowledge_id, text=item.summary,
                    data={"knowledge_type": item.knowledge_type,
                          "authority": item.source_authority}))

    def _gather_batting(self, plan: CognitionPlan, gathered: _Gathered) -> None:
        if self._batting is None:
            gathered.recovery_codes.append("BATTING_STATS_TOOL_UNAVAILABLE")
            return
        year = plan.batting_year or self._today().year
        names = tuple(plan.batting_entity_names)
        gathered.tool_calls.append(f"batting.season({year})")
        try:
            if plan.batting_team and not names:
                evidence = self._batting.season_evidence(year, team=plan.batting_team,
                                                         metric=(plan.batting_metrics or ("OPS",))[0])
            else:
                evidence = self._batting.season_evidence(year, names=names,
                                                         metric=(plan.batting_metrics or ("OPS",))[0])
            gathered.evidence.append(evidence)
        except BattingStatsUnavailable as error:
            gathered.recovery_codes.append("BATTING_STATS_UNAVAILABLE")
            gathered.steps.append(str(error))

    def _gather_pitching(self, plan: CognitionPlan, gathered: _Gathered) -> None:
        if self._pitching is None:
            gathered.recovery_codes.append("PITCHING_STATS_TOOL_UNAVAILABLE")
            return
        year = plan.pitching_year or self._today().year
        gathered.tool_calls.append(f"pitching.season({year})")
        try:
            gathered.evidence.append(self._pitching.season_evidence(
                year, names=tuple(plan.pitching_entity_names)))
        except BattingStatsUnavailable as error:
            gathered.recovery_codes.append("PITCHING_STATS_UNAVAILABLE")
            gathered.steps.append(str(error))

    def _gather_local(self, hint, resolved_mentions, gathered: _Gathered,
                      plan: CognitionPlan) -> None:
        if self._local is None:
            gathered.recovery_codes.append("UNSUPPORTED_LOCAL_ANALYTICS")
            return
        ids = list(hint.entity_ids)
        for name in hint.entity_names:
            resolved = self._entity_lookup.resolve(name)
            if resolved.resolved:
                ids.append(resolved.canonical.entity_key.partition(":")[2])
        for entity in resolved_mentions:
            if entity.entity_key.startswith("MLBAM:") and str(entity.entity_key.partition(":")[2]).isdigit():
                if entity.entity_key.partition(":")[2] not in ids and (
                        not hint.entity_names or entity.display_name in hint.entity_names):
                    ids.append(entity.entity_key.partition(":")[2])
        effective = hint.model_copy(update={"entity_ids": tuple(dict.fromkeys(ids))})
        gathered.tool_calls.append(f"local_analytics({effective.metric})")
        outcome = self._local.execute(effective)
        if outcome.ok and outcome.evidence is not None:
            gathered.evidence.append(outcome.evidence)
            if outcome.sql_request:
                gathered.sql_requests.append(outcome.sql_request)
            gathered.steps.extend(outcome.caveats)
        else:
            gathered.recovery_codes.append(outcome.recovery_code or "UNSUPPORTED_LOCAL_ANALYTICS")
            gathered.steps.append(outcome.detail)
            if not plan.research_queries:
                gathered.steps.append("local analytics unavailable; web research is the recovery path")

    def _gather_web(self, queries: list[str], gathered: _Gathered) -> None:
        if self._web is None or not queries:
            return
        for query in queries[:_MAX_RESEARCH_QUERIES]:
            gathered.tool_calls.append(f"web.research({query})")
            try:
                gathered.evidence.extend(self._web.research(query, fetch_pages=2))
            except WebResearchUnavailable as error:
                gathered.recovery_codes.append("WEB_RESEARCH_UNAVAILABLE")
                gathered.steps.append(str(error))

    # -- recovery ------------------------------------------------------------
    def _maybe_replan(self, conversation: Conversation, plan: CognitionPlan,
                      gathered: _Gathered, message: str) -> None:
        if not gathered.recovery_codes or gathered.evidence or self._web is None:
            return
        # One bounded recovery attempt: research the raw question on the web.
        gathered.tool_calls.append("web.research(recovery)")
        try:
            gathered.evidence.extend(self._web.research(message, fetch_pages=2))
        except WebResearchUnavailable as error:
            gathered.steps.append(f"recovery web research failed: {error}")

    # -- helpers -------------------------------------------------------------
    def _resolve_known_entities(self, text: str):
        resolved = []
        seen: set[str] = set()
        for entity in self._entity_lookup.dictionary.entities():
            for surface in (entity.display_name, *entity.aliases):
                if len(surface) >= 2 and surface.casefold() in text.casefold():
                    if entity.entity_key not in seen:
                        seen.add(entity.entity_key)
                        resolved.append(entity)
                    break
        return tuple(resolved)

    def _history(self, conversation: Conversation) -> str:
        parts = []
        for message in conversation.messages[-_MAX_HISTORY_MESSAGES:]:
            parts.append(f"{message.role}: {message.text}")
        if conversation.accepted_context:
            parts.extend(conversation.accepted_context[-3:])
        return "\n".join(parts)

    @staticmethod
    def _already_clarified(conversation: Conversation, question: str) -> bool:
        needle = question.strip().casefold()[:40]
        return any(needle and needle in item.casefold() for item in conversation.accepted_context)

    @staticmethod
    def _status_for(gathered: _Gathered) -> RunStatus:
        if gathered.evidence:
            empty_local = any(item.kind == "LOCAL_ANALYTICS" and not item.data.get("rows")
                              for item in gathered.evidence)
            if gathered.recovery_codes or empty_local or any(
                    item.data.get("caveats") for item in gathered.evidence):
                return "LIMITED"
            return "COMPLETE"
        return "FAILED"

    def _record_entities(self, conversation: Conversation, names: tuple[str, ...]) -> None:
        for name in names:
            if name and name not in conversation.recent_entities:
                conversation.recent_entities.append(name)
        del conversation.recent_entities[:-8]

    def _record_unknowns(self, conversation: Conversation, plan: CognitionPlan,
                         gathered: _Gathered, message: str) -> None:
        unresolved = tuple(plan.unresolved)
        if not unresolved or self._candidates is None:
            return
        web_text = " ".join(item.text for item in gathered.evidence if item.kind == "WEB")
        for mention in unresolved:
            if self._entity_lookup.resolve(mention).resolved:
                continue
            self._candidates.submit(CandidateKnowledge(
                candidate_id=self._id("candidate"), knowledge_type="CONTEXT_REFERENCE",
                surface=mention, language="und", context=plan.understanding[:500],
                confidence="LOW", discovered_from_query=message,
                evidence=(web_text[:1000],) if web_text else (),
                provenance=tuple(item.reference for item in gathered.evidence
                                 if item.kind == "WEB")[:3]))

    def _trace(self, message: str, plan: CognitionPlan, gathered: _Gathered, status: str,
               answer: str) -> AgentTrace:
        return AgentTrace(
            raw_query=message, understanding=plan.understanding, plan=plan,
            steps=tuple(gathered.steps), tool_calls=tuple(gathered.tool_calls),
            sql_requests=tuple(gathered.sql_requests), status=status,
            evidence=tuple(item.summary for item in gathered.evidence), answer=answer)

    @staticmethod
    def _remember_trace(conversation: Conversation, trace: AgentTrace) -> None:
        setattr(conversation, "_last_trace", trace)
