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
        kind = str(data.get("kind", "")).upper()
        if kind not in _VALID_KINDS:
            raise ValueError(f"Invalid planning kind {data.get('kind')!r}")
        known = {item.requirement_id for item in context.requirements}
        tasks: list[AgentTask] = []
        for raw in data.get("tasks", []):
            refs = tuple(raw.get("requirement_refs", ()))
            unknown = set(refs) - known
            if unknown:
                raise ValueError(f"Planner referenced unknown requirement(s) {sorted(unknown)}")
            tasks.append(AgentTask(
                task_id=self._id_factory("task"), objective_ref=context.objective.objective_id,
                requirement_refs=refs, description=raw.get("description", "planned task"),
                source_preference=raw.get("source_preference")))
        planner_terminal = bool(data.get("planner_terminal", kind == "STOP_PLANNING"))
        return PlanningDecision(
            decision_id=self._id_factory("decision"), objective_ref=context.objective.objective_id,
            kind=kind, tasks=tuple(tasks), planner_terminal=planner_terminal,
            terminal_reason=data.get("terminal_reason", ""), rationale=data.get("rationale", "llm plan"),
            round=context.round)
