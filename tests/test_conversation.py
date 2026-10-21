from datetime import date

from app.contracts.models import (
    Clarification,
    ConversationMessage,
    ConversationState,
    Goal,
    RequirementKind,
    TurnType,
    UserRequirement,
)
from app.runtime.conversation import ConversationManager


def _state_with_goal() -> ConversationState:
    goal = Goal(statement="2025 Ohtani batting", raw_query="大谷翔平2025赛季打击表现如何？")
    goal.requirements = [UserRequirement(kind=RequirementKind.SEASON, statement="2025", value=2025)]
    state = ConversationState(session_id="s", active_goal_id=goal.id, goals=[goal])
    state.messages = [ConversationMessage(role="user", text=goal.raw_query, goal_id=goal.id)]
    return state


def test_independent_goal_without_history():
    manager = ConversationManager()
    prepared = manager.prepare("s", "山本由伸2025赛季投球表现如何？")
    assert prepared.turn_type == TurnType.INDEPENDENT_GOAL
    assert prepared.parent_goal is None


def test_short_follow_up_inherits_parent():
    manager = ConversationManager()
    manager.sessions["s"] = _state_with_goal()
    prepared = manager.prepare("s", "那2024赛季呢？")
    assert prepared.turn_type == TurnType.FOLLOW_UP
    assert prepared.parent_goal is not None
    assert prepared.parent_goal.id == manager.sessions["s"].active_goal_id


def test_long_independent_question_is_not_crowded_by_history():
    manager = ConversationManager()
    manager.sessions["s"] = _state_with_goal()
    prepared = manager.prepare("s", "请分析一下2023赛季全联盟打者的平均击球初速排名")
    assert prepared.turn_type == TurnType.INDEPENDENT_GOAL
    assert prepared.parent_goal is None


def test_clarification_answer_resumes_original_goal():
    manager = ConversationManager()
    state = _state_with_goal()
    original = state.goals[0]
    state.pending_clarification = Clarification(question="which player?", options=["1:A", "2:B"], goal_id=original.id)
    manager.sessions["s"] = state
    prepared = manager.prepare("s", "B")
    assert prepared.turn_type == TurnType.CLARIFICATION_ANSWER
    assert prepared.parent_goal is not None and prepared.parent_goal.id == original.id
    # The effective query must restate the original question, not treat "B" alone as a query.
    assert original.raw_query in prepared.effective_query
    assert "B" in prepared.effective_query
