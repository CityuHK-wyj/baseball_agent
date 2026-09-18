"""Conversation-context growth investigation for the v0.6 DeepSeek Flash latency round.

Answers, with measured evidence rather than assumption:

* which model roles receive conversation history / artifacts / attempts;
* how large each role's serialized input is, and how it grows per turn;
* how much of the Planner input is schema/capability/export context;
* whether semantic content is duplicated across prompt sections;
* (optionally, ``--live``) whether provider latency grows with input size.

Deterministic mode uses the product runtime with a scripted interpreter/planner so the
serialized inputs are exactly what a live provider would receive. No production change.

Usage:
    python3 docs/reviews/v06-deepseek-flash-latency/context_growth.py
    python3 docs/reviews/v06-deepseek-flash-latency/context_growth.py --live
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

AUDIT = Path(__file__).resolve().parent
REPO = AUDIT.parents[2]
sys.path.insert(0, str(REPO))

TURNS = [1, 2, 4, 8, 12, 16]


def _build_runtime():
    """Product runtime with scripted cognition (no network) and a repeating tool need."""
    from datetime import date

    from app.artifact_runtime.factory import build_runtime
    from app.config import settings
    from app.knowledge.service import KnowledgeBase
    from app.knowledge.store import SqliteKnowledgeStore
    from app.models.artifact_runtime import Need, SemanticBrief

    knowledge_store = SqliteKnowledgeStore(settings.knowledge_store_path)
    knowledge = KnowledgeBase(knowledge_store)

    class FixedInterpreter:
        def brief(self, *, message, history, resolved_entities, unknowns, today):
            from app.artifact_runtime.temporal import parse_season, parse_time_window
            day = date.fromisoformat(today)
            window = parse_time_window(message, day)
            season = parse_season(message)
            from app.models.artifact_runtime import Scope
            scope = None
            if window is not None:
                scope = Scope(time_range=window)
            elif season is not None:
                scope = Scope(seasons=(season,))
            return SemanticBrief(brief_id="b", goal_statement=message,
                                 understanding=message, proposed_scope=scope,
                                 source="scripted")

    class RepeatingPlanner:
        """Creates one knowledge Need per turn so artifacts accumulate across turns."""

        def initial_needs(self, *, goal, brief, context):
            return (Need(need_id=f"need-{goal.revision}", objective=goal.statement,
                         expected_information="knowledge about the request",
                         proposed_capability="shared_knowledge",
                         preferred_capabilities=("KNOWLEDGE_CANDIDATE",),
                         parameters={"query": goal.statement},
                         criticality="CORE"),)

        def add_needs(self, **kwargs):
            return ()

        def next_action(self, *, goal, needs, artifacts, context):
            from app.artifact_runtime.planner import DeterministicPlanner
            return DeterministicPlanner().next_action(goal=goal, needs=needs,
                                                      artifacts=artifacts, context=context)

    runtime = build_runtime(
        runtime_dir=str(AUDIT / "_runtime"), use_llm=False, knowledge=knowledge,
        interpreter=FixedInterpreter(), planner=RepeatingPlanner(),
        store=None, today=lambda: date(2025, 9, 30))
    return runtime, knowledge_store


def _planner_prompt_chars(runtime, conversation) -> int:
    from app.artifact_runtime.planner import LLMPlanner
    goal = conversation.goal
    if goal is None:
        return 0
    from app.models.artifact_runtime import SemanticBrief
    brief = SemanticBrief(brief_id="b", goal_statement=goal.statement)
    context = runtime._planner_context(conversation)  # noqa: SLF001
    return len(LLMPlanner._prompt(goal=goal, brief=brief, context=context))


def _semantic_prompt_chars(runtime, conversation, message: str) -> int:
    from app.artifact_runtime.planner import SEMANTIC_PROMPT
    return len(SEMANTIC_PROMPT.format(
        today="2025-09-30", history=runtime._history(conversation) or "(none)",  # noqa: SLF001
        resolved_entities="(none)", unknowns="(none)", message=message))


def _context_sections(runtime, conversation) -> dict:
    context = runtime._planner_context(conversation)  # noqa: SLF001
    feedback = getattr(context, "feedback", None)
    return {
        "capability_views": len(getattr(context, "capabilities", ()) or ()),
        "schema_tables": len(getattr(context, "schema_tables", ()) or ()),
        "available_exports": len(getattr(context, "available_exports", ()) or ()),
        "attempt_views": len(getattr(feedback, "attempts", ()) or ()) if feedback else 0,
        "chars_capabilities": len(context.render_capabilities()),
        "chars_schema": len(context.render_schema()),
        "chars_available_exports": len(context.render_available_exports()),
        "chars_feedback": len(context.render_feedback()),
        "chars_context_total": (len(context.render_capabilities())
                                + len(context.render_schema())
                                + len(context.render_available_exports())
                                + len(context.render_feedback())),
    }


def deterministic_growth() -> dict:
    runtime, knowledge_store = _build_runtime()
    conversation_id = runtime.start_conversation()
    rows = []
    try:
        for turn in range(1, max(TURNS) + 1):
            message = "designated for assignment"
            result = runtime.send_message(conversation_id, message)
            conversation = runtime.get_conversation(conversation_id)
            row = {
                "turn": turn,
                "status": result.status,
                "history_chars": len(runtime._history(conversation)),  # noqa: SLF001
                "messages": len(conversation.messages),
                "artifacts": len(conversation.artifacts.all()),
                "attempts": len(conversation.attempts),
                "semantic_prompt_chars": _semantic_prompt_chars(runtime, conversation, message),
                "planner_prompt_chars": _planner_prompt_chars(runtime, conversation),
                "planner_duplication": _duplication(runtime, conversation, message),
                **_context_sections(runtime, conversation),
            }
            rows.append(row)
    finally:
        runtime.close()
        knowledge_store.close()
    return {"turns": rows, "sample_turns": TURNS}


def _duplication(runtime, conversation, message: str) -> dict:
    """How often the current query text appears across planner/semantic sections."""
    from app.artifact_runtime.planner import LLMPlanner
    from app.models.artifact_runtime import SemanticBrief
    goal = conversation.goal
    brief = SemanticBrief(brief_id="b", goal_statement=goal.statement if goal else message)
    context = runtime._planner_context(conversation)  # noqa: SLF001
    planner_prompt = LLMPlanner._prompt(goal=goal, brief=brief, context=context) if goal else ""
    planner_context = context.render_capabilities() + context.render_schema() + \
        context.render_available_exports() + context.render_feedback()
    return {
        "query_in_planner_prompt": planner_prompt.count(message),
        "query_in_planner_context": planner_context.count(message),
        "history_chars": len(runtime._history(conversation)),  # noqa: SLF001
    }


def live_input_scaling(sizes=(800, 2000, 6000, 12000, 24000)) -> dict:
    """Measure real provider latency vs input size (same model, bounded output)."""
    from app.config import settings
    from app.llm.openai_provider import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider(settings)
    results = []
    for size in sizes:
        filler = ("The runtime must answer baseball questions using verified evidence. "
                  * (size // 60 + 1))[:size]
        prompt = ("Return ONLY the JSON {\"ok\": true}. Context follows:\n" + filler)
        response = provider.complete(
            prompt, model=settings.llm_planner_model,
            max_tokens=settings.llm_planner_max_tokens,
            reasoning_effort=settings.llm_reasoning_effort,
            deadline=settings.llm_deadline_seconds)
        results.append({
            "prompt_chars": len(prompt),
            "prompt_tokens": response.usage.get("prompt_tokens", 0),
            "completion_tokens": response.usage.get("completion_tokens", 0),
            "reasoning_tokens": response.reasoning_tokens,
            "duration_seconds": response.duration_seconds,
            "first_token_seconds": response.first_token_seconds,
            "output_chars": len(response.text),
        })
    return {"model": settings.llm_planner_model, "samples": results}


def main() -> int:
    report = {
        "model_roles": _role_table(),
        "deterministic_growth": deterministic_growth(),
    }
    if "--live" in sys.argv:
        report["live_input_scaling"] = live_input_scaling()
    out = AUDIT / "context_growth.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report["deterministic_growth"]["turns"], indent=1)[:2000])
    if "--live" in sys.argv:
        print(json.dumps(report["live_input_scaling"], indent=1))
    print(f"wrote {out}")
    return 0


def _role_table() -> dict:
    from app.config import settings
    return {
        "semantic": {
            "component": "app.artifact_runtime.planner.LLMSemanticInterpreter",
            "model": settings.llm_semantic_model,
            "max_tokens": settings.llm_semantic_max_tokens,
            "receives_history": True,
            "receives_artifacts": False,
            "receives_attempts": False,
        },
        "planner_initial": {
            "component": "app.artifact_runtime.planner.LLMPlanner.initial_needs",
            "model": settings.llm_planner_model,
            "max_tokens": settings.llm_planner_max_tokens,
            "receives_history": False,
            "receives_artifacts": True,
            "receives_attempts": True,
        },
        "planner_replan": {
            "component": "app.artifact_runtime.planner.LLMPlanner.add_needs",
            "model": settings.llm_planner_model,
            "max_tokens": settings.llm_planner_max_tokens,
            "receives_history": False,
            "receives_artifacts": True,
            "receives_attempts": True,
        },
        "response": {
            "component": "app.artifact_runtime.response.LLMResponseComposer",
            "model": settings.llm_response_model,
            "max_tokens": settings.llm_response_max_tokens,
            "receives_history": False,
            "receives_artifacts": True,
            "receives_attempts": False,
        },
        "judge": {
            "component": "app.artifact_runtime.sufficiency.CoverageJudge (deterministic)",
            "model": None,
            "max_tokens": 0,
            "receives_history": False,
            "receives_artifacts": True,
            "receives_attempts": False,
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
