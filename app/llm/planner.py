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
from app.models.planning import AgentTask, PlanningDecision, SupportingNeed

_VALID_KINDS = ("PLAN", "REPLAN", "STOP_PLANNING")
_ALLOWED_DECISION_FIELDS = frozenset(
    {"kind", "tasks", "supporting_needs", "planner_terminal", "terminal_reason", "rationale"})
_ALLOWED_TASK_FIELDS = frozenset({
    "requirement_refs", "description", "source_preference", "task_type", "objective",
    "instructions", "expected_evidence", "search_hints"})
_ALLOWED_NEED_FIELDS = frozenset({
    "need_id", "description", "artifact_type", "data_keys", "parent_ref", "task_type",
    "objective", "instructions", "expected_evidence", "search_hints"})
_MAX_TASKS = 32
_MAX_NEEDS = 8
_MAX_FREE_TEXT = 20000

_TASK_TYPES = frozenset({
    "GENERIC", "LOCAL_ANALYTICS", "WEB_RESEARCH", "ENTITY_RESOLUTION", "KNOWLEDGE",
    "COMPUTATION", "SYNTHESIS"})


def _planner_context(context: PlannerContext) -> dict:
    """A bounded, payload-free projection: no raw artifact bodies.

    The raw query and the open-world understanding are included because the semantic
    layer is an interpretation aid, not an information bottleneck. Physical schema is
    still never included.
    """
    return {
        "raw_query": context.raw_query or context.objective.raw_query,
        "understanding": (context.understanding.model_dump(mode="json")
                          if context.understanding is not None else None),
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
        "recovery_signals": list(context.recovery_signals),
        "web_recovery_available": context.web_recovery_available,
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
        raw_needs = data.get("supporting_needs", [])
        if not isinstance(raw_needs, list):
            raise ValueError("Planning supporting_needs must be an array")
        if len(raw_needs) > _MAX_NEEDS:
            raise ValueError(f"Planning output exceeds {_MAX_NEEDS} supporting needs")
        needs: list[SupportingNeed] = []
        for raw in raw_needs:
            if not isinstance(raw, dict):
                raise ValueError("Each supporting need must be an object")
            unknown_fields = set(raw) - _ALLOWED_NEED_FIELDS
            if unknown_fields:
                raise ValueError(f"Supporting need contains unknown field(s): {sorted(unknown_fields)}")
            need_id = raw.get("need_id")
            description = raw.get("description")
            data_keys = raw.get("data_keys")
            if not isinstance(need_id, str) or not need_id.strip():
                raise ValueError("Supporting need_id must be a non-empty string")
            if not isinstance(description, str) or not description.strip():
                raise ValueError("Supporting need description must be a non-empty string")
            if not isinstance(data_keys, list) or not data_keys or not all(
                    isinstance(item, str) and item.strip() for item in data_keys):
                raise ValueError("Supporting need data_keys must be a non-empty string array")
            need_type = str(raw.get("task_type") or "WEB_RESEARCH").upper()
            if need_type not in _TASK_TYPES:
                need_type = "WEB_RESEARCH"
            artifact_type = str(raw.get("artifact_type") or "EVIDENCE").upper()
            if artifact_type not in ("TABLE", "EVIDENCE", "FEATURE"):
                raise ValueError("Supporting need artifact_type is invalid")
            needs.append(SupportingNeed(
                need_id=need_id.strip(), objective_ref=context.objective.objective_id,
                description=description.strip(), artifact_type=artifact_type,
                data_keys=tuple(item.strip() for item in data_keys),
                parent_ref=(raw.get("parent_ref") or None), task_type=need_type,
                objective=str(raw.get("objective") or ""),
                instructions=str(raw.get("instructions") or ""),
                expected_evidence=str(raw.get("expected_evidence") or ""),
                search_hints=tuple(item for item in (raw.get("search_hints") or [])
                                   if isinstance(item, str))))
        known = {item.requirement_id for item in context.requirements}
        known |= {f"supporting-{need.need_id}" for need in needs}
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
            task_type = str(raw.get("task_type") or "GENERIC").upper()
            if task_type not in _TASK_TYPES:
                task_type = "GENERIC"
            objective = raw.get("objective") or ""
            instructions = raw.get("instructions") or ""
            expected_evidence = raw.get("expected_evidence") or ""
            for field, value in (("objective", objective), ("instructions", instructions),
                                 ("expected_evidence", expected_evidence)):
                if not isinstance(value, str):
                    raise ValueError(f"Planned task {field} must be a string")
                if len(value) > _MAX_FREE_TEXT:
                    raise ValueError(f"Planned task {field} exceeds the free-text limit")
            raw_hints = raw.get("search_hints") or []
            if not isinstance(raw_hints, list) or not all(isinstance(item, str) for item in raw_hints):
                raise ValueError("Planned task search_hints must be a string array")
            tasks.append(AgentTask(
                task_id=self._id_factory("task"), objective_ref=context.objective.objective_id,
                requirement_refs=refs, description=description.strip(),
                source_preference=source_preference, task_type=task_type,
                objective=objective, instructions=instructions,
                expected_evidence=expected_evidence, search_hints=tuple(raw_hints)))
        planner_terminal = data.get("planner_terminal", kind == "STOP_PLANNING")
        if not isinstance(planner_terminal, bool):
            raise ValueError("Planning planner_terminal must be a boolean")
        terminal_reason = data.get("terminal_reason", "")
        rationale = data.get("rationale", "llm plan")
        if not isinstance(terminal_reason, str) or not isinstance(rationale, str):
            raise ValueError("Planning terminal_reason and rationale must be strings")
        return PlanningDecision(
            decision_id=self._id_factory("decision"), objective_ref=context.objective.objective_id,
            kind=kind, tasks=tuple(tasks), supporting_needs=tuple(needs),
            planner_terminal=planner_terminal,
            terminal_reason=terminal_reason, rationale=rationale,
            round=context.round)
