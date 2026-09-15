"""LLM Planner behind the existing Planner Protocol.

Structured output is schema- and invariant-validated. The LLM may only reference
requirement ids present in the context; it cannot modify an Initial Requirement or run
after a terminal decision. On provider/parse/invariant failure it falls back to a
deterministic planner when one is supplied.
"""

import json
from collections.abc import Callable

from app.agent.planner import Planner, PlannerContext
from app.llm.parsing import parse_json_object
from app.llm.prompts import PLANNER_PROMPT, PromptTemplate
from app.llm.provider import ModelProvider, ProviderError
from app.models.planning import AgentTask, PlanningDecision

_VALID_KINDS = ("PLAN", "REPLAN", "STOP_PLANNING")
_ALLOWED_DECISION_FIELDS = frozenset(
    {"kind", "tasks", "planner_terminal", "terminal_reason", "rationale"})
_ALLOWED_TASK_FIELDS = frozenset({"requirement_refs", "description", "source_preference"})
_MAX_TASKS = 32


def _planner_context(context: PlannerContext) -> dict:
    """A bounded, payload-free projection: no raw artifact bodies."""
    return {
        "objective": context.objective.model_dump(mode="json"),
        "requirements": [item.model_dump(mode="json") for item in context.requirements],
        "requirement_states": [item.model_dump(mode="json") for item in context.requirement_states],
        "artifact_index": [item.model_dump(mode="json") for item in context.artifact_index],
        "assessment_summaries": [item.model_dump(mode="json") for item in context.assessment_summaries],
        "execution_summary": context.execution_summary.model_dump(mode="json"),
        "round": context.round, "max_rounds": context.max_rounds,
        "budget_remaining": context.budget_remaining,
        "recoverable_gaps": list(context.recoverable_gaps),
        "policy_blocked_gaps": list(context.policy_blocked_gaps),
        "prior_plan_count": context.prior_plan_count,
    }


class LLMPlanner:
    def __init__(self, provider: ModelProvider, model: str,
                 prompt: PromptTemplate = PLANNER_PROMPT,
                 id_factory: Callable[[str], str] | None = None,
                 fallback: Planner | None = None, timeout: float = 30.0) -> None:
        self._provider = provider
        self._model = model
        self._prompt = prompt
        self._fallback = fallback
        self._timeout = timeout
        self._id_factory = id_factory or (lambda prefix: f"{prefix}-{abs(hash(prefix))}")

    def decide(self, context: PlannerContext) -> PlanningDecision:
        if context.planner_terminal:
            raise RuntimeError("Planner was invoked after a terminal decision")
        try:
            prompt = self._prompt.render(
                context_json=json.dumps(_planner_context(context), ensure_ascii=False))
            response = self._provider.complete(prompt, model=self._model, timeout=self._timeout)
            return self._parse(context, response.text)
        except (ProviderError, ValueError, KeyError):
            if self._fallback is not None:
                return self._fallback.decide(context)
            raise

    def _parse(self, context: PlannerContext, text: str) -> PlanningDecision:
        data = parse_json_object(text)
        unknown_fields = set(data) - _ALLOWED_DECISION_FIELDS
        if unknown_fields:
            raise ValueError(f"Planning output contains unknown field(s): {sorted(unknown_fields)}")
        raw_kind = data.get("kind")
        if not isinstance(raw_kind, str):
            raise ValueError("Planning kind must be a string")
        kind = raw_kind.upper()
        if kind not in _VALID_KINDS:
            raise ValueError(f"Invalid planning kind {data.get('kind')!r}")
        raw_tasks = data.get("tasks", [])
        if not isinstance(raw_tasks, list):
            raise ValueError("Planning tasks must be an array")
        if len(raw_tasks) > _MAX_TASKS:
            raise ValueError(f"Planning output exceeds {_MAX_TASKS} tasks")
        known = {item.requirement_id for item in context.requirements}
        tasks: list[AgentTask] = []
        for raw in raw_tasks:
            if not isinstance(raw, dict):
                raise ValueError("Each planned task must be an object")
            unknown_fields = set(raw) - _ALLOWED_TASK_FIELDS
            if unknown_fields:
                raise ValueError(f"Planned task contains unknown field(s): {sorted(unknown_fields)}")
            raw_refs = raw.get("requirement_refs")
            if not isinstance(raw_refs, list) or not raw_refs or not all(
                    isinstance(reference, str) for reference in raw_refs):
                raise ValueError("Planned task requirement_refs must be a non-empty string array")
            refs = tuple(raw_refs)
            unknown = set(refs) - known
            if unknown:
                raise ValueError(f"Planner referenced unknown requirement(s) {sorted(unknown)}")
            description = raw.get("description", "planned task")
            source_preference = raw.get("source_preference")
            if not isinstance(description, str) or not description.strip():
                raise ValueError("Planned task description must be a non-empty string")
            if source_preference is not None and not isinstance(source_preference, str):
                raise ValueError("Planned task source_preference must be a string or null")
            tasks.append(AgentTask(
                task_id=self._id_factory("task"), objective_ref=context.objective.objective_id,
                requirement_refs=refs, description=description.strip(),
                source_preference=source_preference))
        planner_terminal = data.get("planner_terminal", kind == "STOP_PLANNING")
        if not isinstance(planner_terminal, bool):
            raise ValueError("Planning planner_terminal must be a boolean")
        terminal_reason = data.get("terminal_reason", "")
        rationale = data.get("rationale", "llm plan")
        if not isinstance(terminal_reason, str) or not isinstance(rationale, str):
            raise ValueError("Planning terminal_reason and rationale must be strings")
        return PlanningDecision(
            decision_id=self._id_factory("decision"), objective_ref=context.objective.objective_id,
            kind=kind, tasks=tuple(tasks), planner_terminal=planner_terminal,
            terminal_reason=terminal_reason, rationale=rationale,
            round=context.round)
