"""Multi-turn conversation: Goal lifecycle, follow-ups, clarification resume.

Each Goal, Need, Attempt and ToolRequest has explicit ownership. A follow-up starts a new
Goal that inherits context but not the previous Goal's unresolved requirements. A
clarification answer resumes the Goal that produced the question.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.contracts.models import (
    AnswerProjection,
    Clarification,
    ConversationMessage,
    ConversationState,
    Goal,
    TurnType,
)


@dataclass
class PreparedTurn:
    turn_type: TurnType
    parent_goal: Goal | None
    clarification: Clarification | None
    interpreter_context: dict[str, Any] = field(default_factory=dict)
    effective_query: str = ""


class ConversationManager:
    def __init__(self, store=None):
        self.store = store
        self.sessions: dict[str, ConversationState] = {}

    def get(self, session_id: str) -> ConversationState:
        if session_id not in self.sessions:
            if self.store is not None:
                loaded = self.store.load_conversation(session_id)
                if loaded is not None:
                    self.sessions[session_id] = loaded
                    return loaded
            self.sessions[session_id] = ConversationState(session_id=session_id)
        return self.sessions[session_id]

    def prepare(self, session_id: str, text: str) -> PreparedTurn:
        state = self.get(session_id)
        clarification = state.pending_clarification
        active_goal = state.active_goal()
        if clarification is not None:
            original = state.goal(clarification.goal_id)
            effective = text
            if original is not None:
                effective = f"{original.raw_query}\n（用户补充 / user clarification: {text}）"
            return PreparedTurn(
                turn_type=TurnType.CLARIFICATION_ANSWER,
                parent_goal=original,
                clarification=clarification,
                interpreter_context=self._context(state, active_goal),
                effective_query=effective,
            )
        turn_type = TurnType.FOLLOW_UP if self._looks_like_follow_up(text, state) else TurnType.INDEPENDENT_GOAL
        return PreparedTurn(
            turn_type=turn_type,
            parent_goal=active_goal if turn_type == TurnType.FOLLOW_UP else None,
            clarification=None,
            interpreter_context=self._context(state, active_goal),
            effective_query=text,
        )

    def commit(self, session_id: str, *, user_text: str, goal: Goal, answer: str, projection: AnswerProjection) -> None:
        state = self.get(session_id)
        state.messages.append(ConversationMessage(role="user", text=user_text, goal_id=goal.id))
        state.messages.append(ConversationMessage(role="assistant", text=answer, goal_id=goal.id))
        state.goals.append(goal)
        state.active_goal_id = goal.id
        state.pending_clarification = projection.clarification
        # Keep recent resolved entities for follow-ups.
        for artifact in projection.supporting_artifact_ids:
            pass
        state.accepted_context = {
            "last_goal_statement": goal.statement,
            "last_goal_id": goal.id,
            "resolved_scope": goal.resolved_time_scope,
        }
        if len(state.goals) > 20:
            state.goals = state.goals[-20:]
        if len(state.messages) > 40:
            state.messages = state.messages[-40:]
        if self.store is not None:
            self.store.save_conversation(state)

    def record_entities(self, session_id: str, entities: list[dict[str, Any]], *, goal_id: str | None = None) -> None:
        state = self.get(session_id)
        for entity in entities:
            if entity.get("id"):
                entry = dict(entity)
                if goal_id and not entry.get("goal_id"):
                    entry["goal_id"] = goal_id
                state.recent_entities = [e for e in state.recent_entities if e.get("id") != entry.get("id")]
                state.recent_entities.append(entry)
        state.recent_entities = state.recent_entities[-6:]
        if self.store is not None:
            self.store.save_conversation(state)

    def clear(self, session_id: str) -> None:
        self.sessions[session_id] = ConversationState(session_id=session_id)
        if self.store is not None:
            self.store.save_conversation(self.sessions[session_id])

    # ------------------------------------------------------------------ helpers
    def _context(self, state: ConversationState, active_goal: Goal | None) -> dict[str, Any]:
        # A prior assistant answer is model synthesis, not factual authority. Mark it so the
        # interpreter cannot launder composer inference into a later turn's Requirement. Any
        # fact must be re-established through Artifact -> Evidence -> Judge (WSA-06 §30/§31).
        recent = [
            {
                "role": message.role,
                "text": message.text,
                "authority": "unverified_model_synthesis" if message.role == "assistant" else "user_statement",
            }
            for message in state.messages[-6:]
        ]
        context: dict[str, Any] = {"recent_messages": recent}
        if active_goal is not None:
            from app.capabilities.analytics.authority import resolve_authority
            approved, _, _ = resolve_authority(
                [r.model_dump(mode="json") for r in active_goal.requirements],
                bound_user_message=active_goal.raw_query,
                clarification_deltas=[d.model_dump(mode="json") for d in active_goal.semantic_deltas])
            approved_ids = {r['id'] for r in approved}
            context["active_goal"] = {
                "statement": active_goal.statement,
                "statement_authority": "semantic_interpretation_not_a_user_quote",
                "raw_query": active_goal.raw_query,
                "requirements": [
                    {"kind": r.kind.value, "statement": r.statement, "value": r.value,
                     "provenance": r.provenance.model_dump(mode='json') if r.provenance else None}
                    for r in active_goal.requirements if r.required and r.id in approved_ids
                ],
            }
        if state.pending_clarification is not None:
            context["pending_clarification"] = {
                "question": state.pending_clarification.question,
                "options": state.pending_clarification.options,
            }
        if state.recent_entities:
            context["recent_entities"] = state.recent_entities
        return context

    def _looks_like_follow_up(self, text: str, state: ConversationState) -> bool:
        if not state.goals:
            return False
        stripped = (text or "").strip()
        if len(stripped) <= 12:
            markers = ("那", "他", "她", "再", "还", "呢", "换", "另外", "同样", "去年", "今年", "same", "and ", "what about", "how about")
            if any(marker in stripped.lower() for marker in markers):
                return True
        return False
