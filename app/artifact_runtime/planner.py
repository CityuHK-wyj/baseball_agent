"""Semantic interpretation and artifact/dataflow planning.

The semantic layer is a *helper*: it produces a permissive :class:`SemanticBrief`
(goal, entities, explicit constraints, ambiguities, hints) and never blocks the planner
behind a giant deterministic parser or a dual-agreement gate.

The planner is an artifact/dataflow planner. It creates Needs dynamically, chooses tools
by capability contract, and prefers passing Artifact references to copying payloads.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.llm.parsing import parse_json_object
from app.llm.provider import ModelProvider, ProviderError
from app.models.artifact_runtime import (Goal, Need, RuntimeArtifact, Scope, SemanticBrief,
                                         ToolRequest)
from app.artifact_runtime.convergence import (PlannerFeedback, READY, planning_state)
from app.artifact_runtime.temporal import parse_season, parse_time_window


# ---------------------------------------------------------------------------
# Semantic interpretation
# ---------------------------------------------------------------------------


class SemanticInterpreter(Protocol):
    def brief(self, *, message: str, history: str, resolved_entities: tuple[str, ...],
              unknowns: tuple[str, ...], today: str) -> SemanticBrief: ...


SEMANTIC_PROMPT = """You are the semantic layer of a baseball assistant. Interpret the
user's message permissively. Do NOT refuse unknown references. Preserve every explicit
constraint the user states (time scope, entity, population, game type, threshold,
ranking, comparison target) in "constraints"; never drop one.

Answer in the user's language. Return ONLY JSON:
{{
  "goal_statement": "what the user wants, one sentence",
  "understanding": "free-form interpretation",
  "entities": ["players/teams mentioned"],
  "constraints": ["explicit constraints, verbatim meaning"],
  "ambiguities": ["material ambiguities only"],
  "analysis_hints": ["how to answer"],
  "research_queries": ["web/knowledge queries for unknown references"],
  "unresolved": ["unknown references to investigate"],
  "assumptions": ["reasonable assumptions to disclose"],
  "time_window": {{"start":"YYYY-MM-DD","end":"YYYY-MM-DD"}} or null,
  "season": 2025 or null,
  "clarification_question": "" or a question only if two interpretations materially differ,
  "clarification_options": []
}}

Today: {today}
Conversation so far:
{history}

Entities already resolved: {resolved_entities}
Open unknowns: {unknowns}

User message: {message}
"""


class LLMSemanticInterpreter:
    def __init__(self, provider: ModelProvider, model: str, timeout: float = 45.0,
                 fallback: "SemanticInterpreter | None" = None,
                 max_tokens: int | None = None, reasoning_effort: str | None = None,
                 deadline: float | None = None) -> None:
        self._provider = provider
        self._model = model
        self._timeout = timeout
        self._fallback = fallback or RuleBasedSemanticInterpreter()
        self._max_tokens = max_tokens
        self._reasoning_effort = reasoning_effort
        self._deadline = deadline

    def brief(self, *, message: str, history: str, resolved_entities: tuple[str, ...],
              unknowns: tuple[str, ...], today: str) -> SemanticBrief:
        prompt = SEMANTIC_PROMPT.format(
            today=today, history=history or "(none)",
            resolved_entities=", ".join(resolved_entities) or "(none)",
            unknowns=", ".join(unknowns) or "(none)", message=message)
        try:
            response = self._provider.complete(
                prompt, model=self._model, timeout=self._timeout,
                max_tokens=self._max_tokens, reasoning_effort=self._reasoning_effort,
                deadline=self._deadline)
            return _parse_brief(response.text)
        except (ProviderError, ValueError):
            return self._fallback.brief(message=message, history=history,
                                        resolved_entities=resolved_entities,
                                        unknowns=unknowns, today=today)


class RuleBasedSemanticInterpreter:
    """Deterministic, language-level interpretation. No phrase-specific branches."""

    def brief(self, *, message: str, history: str, resolved_entities: tuple[str, ...],
              unknowns: tuple[str, ...], today: str) -> SemanticBrief:
        from datetime import date
        day = date.fromisoformat(today) if today else date.today()
        constraints: list[str] = []
        window = parse_time_window(message, day)
        season = parse_season(message)
        proposed = None
        if window is not None:
            constraints.append(f"time scope {window.start}..{window.end}")
            proposed = Scope(time_range=window)
        elif season is not None:
            constraints.append(f"season {season}")
            proposed = Scope(seasons=(season,))
        return SemanticBrief(
            brief_id="brief-rule", goal_statement=message,
            understanding="Open-ended baseball question; research then answer.",
            entities=resolved_entities, constraints=tuple(constraints),
            unresolved=unknowns, research_queries=(message,),
            proposed_scope=proposed, source="deterministic")


def _parse_brief(text: str) -> SemanticBrief:
    data = parse_json_object(text)
    if not isinstance(data, dict):
        raise ValueError("semantic brief must be a JSON object")
    window = data.get("time_window")
    scope = None
    if isinstance(window, dict) and window.get("start") and window.get("end"):
        from app.models.contracts import TimeRange
        from datetime import date
        try:
            scope = Scope(time_range=TimeRange(
                start=date.fromisoformat(str(window["start"])),
                end=date.fromisoformat(str(window["end"]))))
        except ValueError:
            scope = None
    season = data.get("season")
    if scope is None and isinstance(season, int):
        scope = Scope(seasons=(season,))
    return SemanticBrief(
        brief_id="brief-llm",
        goal_statement=str(data.get("goal_statement") or ""),
        understanding=str(data.get("understanding") or ""),
        entities=_strings(data.get("entities")),
        constraints=_strings(data.get("constraints")),
        ambiguities=_strings(data.get("ambiguities")),
        analysis_hints=_strings(data.get("analysis_hints")),
        research_queries=_strings(data.get("research_queries")),
        unresolved=_strings(data.get("unresolved")),
        proposed_scope=scope,
        assumptions=_strings(data.get("assumptions")),
        clarification_question=str(data.get("clarification_question") or ""),
        clarification_options=_strings(data.get("clarification_options")),
        source="llm")


def _strings(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if str(item).strip())
    return ()


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlannerAction:
    need_id: str
    request: ToolRequest
    rationale: str = ""


class Planner(Protocol):
    def initial_needs(self, *, goal: Goal, brief: SemanticBrief,
                      context: "PlannerContext") -> tuple[Need, ...]: ...

    def add_needs(self, *, goal: Goal, brief: SemanticBrief,
                  existing: tuple[Need, ...], artifacts: tuple[RuntimeArtifact, ...],
                  gaps: tuple[str, ...], context: "PlannerContext") -> tuple[Need, ...]: ...

    def next_action(self, *, goal: Goal, needs: tuple[Need, ...],
                    artifacts: tuple[RuntimeArtifact, ...],
                    context: "PlannerContext") -> PlannerAction | None: ...


# Canonical parameter keys. The planner prompt asks for these directly, and this map
# also normalizes capability-shaped keys (which a model may emit) into them.
_CAPABILITY_PARAM_KEYS: dict[str, str] = {
    "TEAM_NAME": "team",
    "SEARCH_QUERY": "query",
    "ENTITY_CONTEXT": "context",
    "ANALYTICAL_QUERY": "analytical_query",
    "DATE_RANGE": "__date_range__",
    "SEASON": "season",
    "PLAYER_NAME": "names",
    "PLAYER_ID_SET": "player_ids",
    "ENTITY_MENTION": "mentions",
}


def normalize_tool_inputs(inputs: dict) -> dict:
    """Normalize flexible model output into the concrete parameter keys tools expect."""
    normalized: dict = {}
    for key, value in inputs.items():
        canonical = _CAPABILITY_PARAM_KEYS.get(key, key)
        if canonical == "__date_range__":
            if isinstance(value, dict) and value.get("start") and value.get("end"):
                normalized.setdefault("start", value["start"])
                normalized.setdefault("end", value["end"])
            elif isinstance(value, str) and ".." in value:
                start, _, end = value.partition("..")
                normalized.setdefault("start", start.strip())
                normalized.setdefault("end", end.strip())
            continue
        if canonical in ("team", "query", "context") and isinstance(value, dict):
            for inner in ("name", "team", "team_name", "query", "text", "value"):
                if isinstance(value.get(inner), str):
                    value = value[inner]
                    break
        normalized[canonical] = value
    return normalized


@dataclass
class PlannerContext:
    """What the planner may consult. Keeps tool contracts and catalog declarative."""

    capability_names: tuple[str, ...] = ()
    available_input_types: tuple[str, ...] = ()
    attempted: set[tuple[str, str]] = field(default_factory=set)
    tool_capabilities: dict[str, tuple[str, tuple[str, ...]]] = field(default_factory=dict)
    catalog_summary: str = ""
    export_ref_hints: dict[str, tuple[str, ...]] = field(default_factory=dict)
    budget_remaining: int = 6
    # v0.5 convergence surface: bounded, trustworthy views built by ``convergence``.
    capabilities: tuple = ()
    schema_tables: tuple = ()
    available_exports: tuple = ()
    feedback: PlannerFeedback | None = None
    unavailable_capabilities: tuple[str, ...] = ()

    def produces_for(self, tool_name: str) -> tuple[str, ...]:
        entry = self.tool_capabilities.get(tool_name)
        return entry[1] if entry else ()

    def accepts_for(self, tool_name: str) -> tuple[str, ...]:
        entry = self.tool_capabilities.get(tool_name)
        return entry[0] if entry else ()

    def render_capabilities(self) -> str:
        if self.capabilities:
            return "\n".join(item.render() for item in self.capabilities)
        return json.dumps(self.tool_capabilities, ensure_ascii=False)

    def render_schema(self) -> str:
        if self.schema_tables:
            return "\n".join(item.render() for item in self.schema_tables)
        return self.catalog_summary

    def render_available_exports(self) -> str:
        if not self.available_exports:
            return "(no Artifacts produced yet in this run)"
        return "\n".join(item.render() for item in self.available_exports)

    def render_feedback(self) -> str:
        if self.feedback is None:
            return "(no prior attempts in this run)"
        return self.feedback.render()


class DeterministicPlanner:
    """Generic capability planner used without an LLM.

    It creates a knowledge need and a web-research need for the goal. It never maps
    phrases to bespoke analyses, so an unseen question yields a bounded, disclosed answer
    instead of a silently scope-changed one. Local/analytical needs are only created by
    an LLM planner or a scripted planner that can express the IR parameters.
    """

    def initial_needs(self, *, goal: Goal, brief: SemanticBrief, context: PlannerContext
                      ) -> tuple[Need, ...]:
        needs: list[Need] = []
        if "shared_knowledge" in context.tool_capabilities:
            needs.append(Need(
                need_id="need-knowledge", objective=goal.statement,
                expected_information="Canonical/contextual baseball knowledge relevant to the goal",
                required_scope=None, preferred_capabilities=("KNOWLEDGE_CANDIDATE",),
                proposed_capability="shared_knowledge", criticality="CORE"))
        if "web_research" in context.tool_capabilities:
            needs.append(Need(
                need_id="need-web", objective=goal.statement,
                expected_information="Sourced web evidence relevant to the goal",
                required_scope=brief.proposed_scope,
                preferred_capabilities=("WEB_EVIDENCE",),
                proposed_capability="web_research", criticality="OPTIONAL"))
        return tuple(needs)

    def add_needs(self, *, goal: Goal, brief: SemanticBrief, existing: tuple[Need, ...],
                  artifacts: tuple[RuntimeArtifact, ...], gaps: tuple[str, ...],
                  context: PlannerContext) -> tuple[Need, ...]:
        return ()

    def next_action(self, *, goal: Goal, needs: tuple[Need, ...],
                    artifacts: tuple[RuntimeArtifact, ...],
                    context: PlannerContext) -> PlannerAction | None:
        # A dependency is ready for *dataflow* once it has produced evidence, even if its
        # own Need verdict is only PARTIAL: a partial upstream product can still feed a
        # downstream action that needs its value. Structurally impossible capabilities and
        # already-attempted pairs are not re-proposed blindly.
        unavailable = set(context.unavailable_capabilities)
        if context.feedback is not None:
            unavailable |= set(context.feedback.unavailable_capabilities)
        for need in needs:
            state = planning_state(need, needs, attempted=context.attempted,
                                   unavailable=unavailable)
            if state != READY:
                continue
            tool_name = need.proposed_capability
            request = self._build_request(need, goal, context)
            return PlannerAction(need_id=need.need_id, request=request,
                                 rationale=f"capability {tool_name} reduces uncertainty")
        return None

    def _build_request(self, need: Need, goal: Goal, context: PlannerContext) -> ToolRequest:
        inputs = normalize_tool_inputs(dict(need.parameters))
        if "query" not in inputs and need.objective:
            inputs["query"] = need.objective
        # No ambient export injection: the engine resolves exact upstream bindings from
        # the Need's declared dependencies. Only explicit planner-declared refs are added.
        refs = list(need.input_refs)
        return ToolRequest(
            request_id=f"req-{need.need_id}", objective=need.objective,
            capability=need.proposed_capability, input_refs=tuple(refs),
            structured_inputs=inputs, expected_outputs=need.preferred_capabilities,
            constraints=goal.constraints, references=goal.constraint_refs)


# ---------------------------------------------------------------------------
# LLM planner
# ---------------------------------------------------------------------------

NEEDS_PROMPT = """You are the artifact/dataflow planner of a baseball agent. Create the
minimum set of information Needs that would satisfy the goal. You may chain tools: a
Need may consume an Artifact produced by another Need through `input_needs`.

Available tools (name: accepts -> produces [truthful restrictions]):
{tool_catalog}

Already produced Artifacts and exports in THIS run (bind only these exact export ids; do
not invent an export name):
{available_exports}

Operational feedback from prior attempts (use it to change strategy; do not blindly
repeat an action whose failure class makes it impossible):
{feedback}

Trusted schema catalog for local analytics (use ONLY these table/field identifiers):
{catalog}

Safe analytical IR shape for `parameters.analytical_query` (use only catalog fields):
{{"query_id":"q","source_kind":"POSTGRES|PARQUET","table":"...",
  "selections":[{{"alias":"...","kind":"GROUP_KEY","field":"..."}},
                {{"alias":"...","kind":"AGGREGATE",
                  "aggregate":{{"op":"COUNT|COUNT_NON_NULL|COUNT_IF|AVG|SUM|MIN|MAX",
                                "field":"...","alias":"...","period":"optional"}}}},
                {{"alias":"...","kind":"DERIVED","expression":{{"op":"PCT|DIFF|RATIO|...",
                   "left":{{"op":"AGG","alias":"..."}},"right":{{"op":"AGG","alias":"..."}}}}}}],
  "filters":[{{"kind":"COMPARE","field":"...","operator":"GTE","value":95}}],
  "entity_set":{{"field":"batter_id","export_ref":"ref id"}} or null,
  "periods":[{{"label":"a","time_range":{{"start":"YYYY-MM-DD","end":"YYYY-MM-DD"}}}}],
  "date_field":"game_date","order_by":"alias","direction":"DESC","limit":10,
  "min_rows":20,
  "qualification":{{"basis":"ROWS|MEASURED|EVENTS|GAMES|ENTITIES_PER_GROUP",
                     "minimum":20,"field":"measured field for MEASURED"}} or null,
  "window":{{"start":"YYYY-MM-DD","end":"YYYY-MM-DD"}} or null}}

Rules:
- NEVER output SQL or arbitrary expressions. Only the JSON above.
- A qualification must name its measured basis: use `min_rows` only for a raw row
  minimum; use `qualification` with basis MEASURED/GAMES/EVENTS for minimum measured
  events, games or events.
- Never name an export, source, table or field that is not shown above. If the needed
  value cannot be produced by an available capability, say so and stop.
- For evidence entity extraction, pass the specific mentions to look for as
  `parameters.focus` (a list) instead of requesting every recognizable entity.
- Put a reusable PLAYER_ID_SET need BEFORE an analytics need that filters on it, and
  reference it in `input_needs`; the runtime resolves that dependency's export.
- In `analytical_query.entity_set.export_ref`, use the exact export id (for example
  `roster-...:PLAYER_ID_SET`) from the available-exports list, never a need id.
- If the request needs a team population, use the `roster` tool (authoritative), never a
  city-name guess. Do NOT give a roster need a time_range (a roster is a player set).
- Use `parameters` with these EXACT concrete keys (never capability names):
    roster            -> {{"team": "New York Yankees"}}
    local_analytics   -> {{"analytical_query": <IR>, "start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}}
    batting_stats     -> {{"season": 2025, "start": "...", "end": "...", "metric": "OPS", "names": [...]}}
    web_research / shared_knowledge -> {{"query": "..."}}
    compute           -> {{"op": "DIFFERENCE|RATIO|PERCENTAGE|MEAN|RANK", ...}}
    entity_resolution -> {{"mentions": [...]}}
    evidence_entities -> {{"focus": ["specific mentions from the goal/context"]}}
- Preserve every explicit user constraint in `required_scope` / `parameters`.

Goal: {goal}
Explicit constraints: {constraints}
Ambiguities: {ambiguities}
Analysis hints: {hints}

Return ONLY JSON: {{"needs":[{{"need_id":"...","objective":"...",
 "expected_information":"...","criticality":"CORE|OPTIONAL",
 "proposed_capability":"tool name","preferred_capabilities":["..."],
 "parameters":{{...}},"input_needs":["need id"],
 "required_scope":{{"entities":[],"population":"","seasons":[],"metric":"",
                    "time_range":{{"start":"...","end":"..."}}}}}}]}}
"""


class LLMPlanner:
    """LLM-backed need planner with a deterministic fallback."""

    def __init__(self, provider: ModelProvider, model: str, timeout: float = 45.0,
                 fallback: Planner | None = None, max_tokens: int | None = None,
                 reasoning_effort: str | None = None,
                 deadline: float | None = None) -> None:
        self._provider = provider
        self._model = model
        self._timeout = timeout
        self._fallback = fallback or DeterministicPlanner()
        self._max_tokens = max_tokens
        self._reasoning_effort = reasoning_effort
        self._deadline = deadline

    @staticmethod
    def _prompt(*, goal: Goal, brief: SemanticBrief, context: PlannerContext) -> str:
        return NEEDS_PROMPT.format(
            tool_catalog=context.render_capabilities(),
            catalog=context.render_schema(),
            available_exports=context.render_available_exports(),
            feedback=context.render_feedback(),
            goal=goal.statement,
            constraints="; ".join(goal.constraints) or "(none)",
            ambiguities="; ".join(brief.ambiguities) or "(none)",
            hints="; ".join(brief.analysis_hints) or "(none)")

    def initial_needs(self, *, goal: Goal, brief: SemanticBrief, context: PlannerContext
                      ) -> tuple[Need, ...]:
        prompt = self._prompt(goal=goal, brief=brief, context=context)
        try:
            response = self._provider.complete(prompt, model=self._model,
                                               timeout=self._timeout,
                                               max_tokens=self._max_tokens,
                                               reasoning_effort=self._reasoning_effort,
                                               deadline=self._deadline)
            needs = _parse_needs(response.text)
            if needs:
                return needs
        except (ProviderError, ValueError):
            pass
        return self._fallback.initial_needs(goal=goal, brief=brief, context=context)

    def add_needs(self, *, goal: Goal, brief: SemanticBrief, existing: tuple[Need, ...],
                  artifacts: tuple[RuntimeArtifact, ...], gaps: tuple[str, ...],
                  context: PlannerContext) -> tuple[Need, ...]:
        if not gaps:
            return ()
        existing_ids = {need.need_id for need in existing}
        prompt = (
            self._prompt(goal=goal, brief=brief, context=context)
            + f"\n\nExisting needs: {[need.need_id for need in existing]}\n"
              f"Coverage gaps to reduce: {list(gaps)}\n"
              "Return only NEW needs that reduce these gaps (or an empty list).")
        try:
            response = self._provider.complete(prompt, model=self._model,
                                               timeout=self._timeout,
                                               max_tokens=self._max_tokens,
                                               reasoning_effort=self._reasoning_effort,
                                               deadline=self._deadline)
            needs = tuple(need for need in _parse_needs(response.text)
                          if need.need_id not in existing_ids)
            return needs
        except (ProviderError, ValueError):
            return ()

    def next_action(self, *, goal: Goal, needs: tuple[Need, ...],
                    artifacts: tuple[RuntimeArtifact, ...],
                    context: PlannerContext) -> PlannerAction | None:
        return DeterministicPlanner().next_action(goal=goal, needs=needs,
                                                  artifacts=artifacts, context=context)


def _parse_needs(text: str) -> tuple[Need, ...]:
    data = parse_json_object(text)
    if not isinstance(data, dict):
        raise ValueError("needs plan must be a JSON object")
    raw_needs = data.get("needs")
    if not isinstance(raw_needs, list):
        raise ValueError("needs must be a list")
    needs: list[Need] = []
    for index, raw in enumerate(raw_needs):
        if not isinstance(raw, dict):
            continue
        scope = None
        if isinstance(raw.get("required_scope"), dict):
            # A malformed scope must not be silently deleted: that would drop explicit user
            # meaning. Raising makes the caller fall back to a conservative plan.
            scope = Scope.model_validate(raw["required_scope"])
        needs.append(Need(
            need_id=str(raw.get("need_id") or f"need-{index + 1}"),
            objective=str(raw.get("objective") or ""),
            expected_information=str(raw.get("expected_information") or ""),
            required_scope=scope,
            preferred_capabilities=_strings(raw.get("preferred_capabilities")),
            proposed_capability=str(raw.get("proposed_capability") or ""),
            parameters=raw.get("parameters") if isinstance(raw.get("parameters"), dict) else {},
            depends_on=_strings(raw.get("input_needs")),
            criticality="OPTIONAL" if str(raw.get("criticality", "CORE")).upper() == "OPTIONAL"
            else "CORE"))
    return tuple(needs)


class ScriptedPlanner:
    """Test/embedding planner: returns a fixed need list, then capability matching."""

    def __init__(self, needs: tuple[Need, ...]) -> None:
        self._needs = needs

    def initial_needs(self, *, goal: Goal, brief: SemanticBrief, context: PlannerContext
                      ) -> tuple[Need, ...]:
        return self._needs

    def add_needs(self, *, goal: Goal, brief: SemanticBrief, existing: tuple[Need, ...],
                  artifacts: tuple[RuntimeArtifact, ...], gaps: tuple[str, ...],
                  context: PlannerContext) -> tuple[Need, ...]:
        return ()

    def next_action(self, *, goal: Goal, needs: tuple[Need, ...],
                    artifacts: tuple[RuntimeArtifact, ...],
                    context: PlannerContext) -> PlannerAction | None:
        return DeterministicPlanner().next_action(goal=goal, needs=needs,
                                                  artifacts=artifacts, context=context)
