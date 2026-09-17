"""LLM-first cognition: understand, plan, research and explain.

Two responsibilities, both permissive by default:

* ``plan`` turns a user message plus context into a free-form understanding and a set of
  actionable *hints* (knowledge/web/batting/local analytics). It may also decide a genuine
  clarification is required.
* ``compose`` turns accepted evidence into a natural answer in the user's language.

The LLM never emits SQL, physical column names, table names or file paths. Its structured
output is validated by the caller and compiled by the strict action boundary.
"""

from __future__ import annotations

import json
from typing import Protocol

from app.llm.parsing import parse_json_object
from app.llm.provider import ModelProvider, ProviderError
from app.models.agent_runtime import (ClarificationDecision, CognitionPlan, EvidenceItem,
                                      LocalMetricHint)
from app.models.understanding import MAX_FREE_TEXT
from app.semantic.field_mapping import DEFAULT_LOCATION_DEFINITIONS

_MAX_EVIDENCE_ITEMS = 12
_MAX_TEXT_PER_ITEM = 4000

# The trusted catalog the Planner may choose from. It contains semantic names only; no
# physical schema is ever shown to the model.
TOOL_CATALOG = {
    "shared_knowledge": "Curated baseball rules, terms, teams, players, awards (fast, authoritative).",
    "web_research": "Live web search + page reading. Use for unknown references, recent news, "
                    "current stats, context and cultural slang. Returns sourced text.",
    "batting_stats": "Live batting lines (PA, AVG, OBP, SLG, OPS, HR, BB, SO) by season or date range.",
    "pitching_stats": "Live pitching lines (W-L, ERA, WHIP, SO, BB, IP, SO9) by season. Use for pitchers.",
    "local_analytics": "Strict read-only Statcast. Supports exactly: metric exit_velocity or "
                       "pitch_velocity; aggregation AVG/MAX/MIN/SUM; pitch family fastball/breaking/"
                       "offspeed; locations " + ", ".join(
                           sorted(item.definition for item in DEFAULT_LOCATION_DEFINITIONS))
                       + "; game types REGULAR_SEASON/POSTSEASON/SPRING_TRAINING/EXHIBITION; "
                       "event populations BATTED_BALL/MEASURED_CONTACT/ALL_PITCHES. Coverage: "
                       "Parquet 2015..2023, PostgreSQL 2024..2026.",
}

PLAN_PROMPT = """You are the planning brain of a baseball assistant. Understand the user's
actual goal and decide what to do next. Be permissive: unknown references are research
targets, not errors. Prefer making a reasonable assumption and disclosing it over asking
the user, unless the ambiguity materially changes the answer.

You may only choose from these tools:
{tool_catalog}

Answer in the user's language. Return ONLY a JSON object with this shape:
{{
  "user_goal": "one sentence: what the user really wants",
  "understanding": "short free-form interpretation",
  "analysis_strategy": "how you will answer; for vague words like 'better' choose a balanced approach",
  "assumptions": ["reasonable assumptions you will disclose"],
  "unresolved": ["unknown references you must research"],
  "entities": ["player/team mentions"],
  "time_hints": ["time expressions"],
  "needs_knowledge": false,
  "knowledge_queries": ["search terms for curated baseball knowledge"],
  "research_queries": ["web search queries; use for unknown references, recent stats, news, context"],
  "needs_batting_stats": false,
  "batting_year": null,
  "batting_team": null,
  "batting_entity_names": ["players to compare"],
  "batting_metrics": ["OPS","OBP","SLG","HR","BB%","K%"],
  "needs_pitching_stats": false,
  "pitching_year": null,
  "pitching_entity_names": ["pitchers"],
  "local_metrics": [
    {{"metric":"exit_velocity","aggregation":"AVG","direction":"DESC","limit":10,
      "entity_names":["..."],"entity_ids":[],"start":"YYYY-MM-DD or null","end":"YYYY-MM-DD or null",
      "pitch_family":"fastball or null","location_definition":null,
      "game_types":[],"event_population":null,"min_batted_balls":null,"note":"..."}}
  ],
  "clarification": {{"question":"...", "reason":"...", "options":["..."], "kind":"..."}} or null,
  "direct_answer": "only for a pure definition/small-talk question you can answer directly"
}}

Rules:
- NEVER output SQL, column names, table names or file paths.
- For a comparison across years, emit one local_metrics entry per year with explicit start/end.
- For "recent/last N days", set start/end from today's date: {today}.
- Only ask a clarification when two materially different interpretations would change the answer.
- If a question is about why/how a player is performing, prefer research_queries plus batting_stats.
- If a question is about a term/rule (e.g. "what is DFA"), set needs_knowledge true and provide direct_answer.

Conversation so far (most recent last):
{history}

Entities already resolved: {resolved_entities}
Known unknowns in this conversation: {unknowns}

User message: {message}
"""

ANSWER_PROMPT = """You are the baseball assistant answering the user in their own language.
Use ONLY the evidence below. Do not invent numbers. Answer the question directly first, then
give the key supporting numbers, then state important assumptions and limitations. Do not
mention internal object ids, artifacts, requirements or SQL. Keep it concise and readable.

User question: {message}
Your interpretation: {understanding}
Assumptions: {assumptions}

Evidence:
{evidence}

Write the final answer now.
"""


class Cognition(Protocol):
    def plan(self, *, message: str, history: str, resolved_entities: tuple[str, ...],
             unknowns: tuple[str, ...], today: str) -> CognitionPlan: ...

    def compose(self, *, message: str, understanding: str, assumptions: tuple[str, ...],
                evidence: tuple[EvidenceItem, ...]) -> str: ...


class LLMCognition:
    """Provider-backed cognition with a deterministic fallback on failure."""

    def __init__(self, provider: ModelProvider, model: str, timeout: float = 45.0,
                 fallback: "DeterministicCognition | None" = None,
                 answer_model: str | None = None) -> None:
        self._provider = provider
        self._model = model
        self._answer_model = answer_model or model
        self._timeout = timeout
        self._fallback = fallback or DeterministicCognition()

    def plan(self, *, message: str, history: str, resolved_entities: tuple[str, ...],
             unknowns: tuple[str, ...], today: str) -> CognitionPlan:
        prompt = PLAN_PROMPT.format(
            tool_catalog=json.dumps(TOOL_CATALOG, ensure_ascii=False),
            today=today, history=history or "(none)",
            resolved_entities=", ".join(resolved_entities) or "(none)",
            unknowns=", ".join(unknowns) or "(none)", message=message)
        try:
            response = self._provider.complete(prompt, model=self._model, timeout=self._timeout)
            return _parse_plan(response.text)
        except (ProviderError, ValueError):
            return self._fallback.plan(message=message, history=history,
                                       resolved_entities=resolved_entities,
                                       unknowns=unknowns, today=today)

    def compose(self, *, message: str, understanding: str, assumptions: tuple[str, ...],
                evidence: tuple[EvidenceItem, ...]) -> str:
        prompt = ANSWER_PROMPT.format(
            message=message, understanding=understanding,
            assumptions="; ".join(assumptions) or "(none)",
            evidence=_render_evidence(evidence))
        try:
            response = self._provider.complete(prompt, model=self._answer_model,
                                               timeout=self._timeout)
            text = (response.text or "").strip()
            if text:
                return text
        except ProviderError:
            pass
        return self._fallback.compose(message=message, understanding=understanding,
                                      assumptions=assumptions, evidence=evidence)


def _year_in(text: str) -> int | None:
    import re
    match = re.search(r"(?<!\d)(20\d{2})(?!\d)", text)
    return int(match.group(1)) if match else None


def _year_from_iso(text: str) -> int | None:
    try:
        return int(text[:4])
    except (TypeError, ValueError):
        return None


def _render_evidence(evidence: tuple[EvidenceItem, ...]) -> str:
    if not evidence:
        return "(no evidence was gathered)"
    lines: list[str] = []
    for index, item in enumerate(evidence[:_MAX_EVIDENCE_ITEMS]):
        lines.append(f"[{index + 1}] ({item.kind}) {item.summary} — {item.source} "
                     f"{item.reference}")
        if item.data:
            lines.append("    data: " + json.dumps(item.data, ensure_ascii=False)[:2000])
        if item.text:
            lines.append("    text: " + item.text[:_MAX_TEXT_PER_ITEM])
    return "\n".join(lines)


def _parse_plan(text: str) -> CognitionPlan:
    data = parse_json_object(text)
    if not isinstance(data, dict):
        raise ValueError("plan must be a JSON object")
    local_metrics: list[LocalMetricHint] = []
    for raw in data.get("local_metrics") or []:
        if not isinstance(raw, dict):
            continue
        local_metrics.append(LocalMetricHint(
            metric=str(raw.get("metric") or ""),
            aggregation=str(raw.get("aggregation") or "AVG"),
            direction=str(raw.get("direction") or "DESC"),
            limit=_int(raw.get("limit"), 10),
            entity_names=_str_tuple(raw.get("entity_names")),
            entity_ids=_str_tuple(raw.get("entity_ids")),
            start=_opt_str(raw.get("start")), end=_opt_str(raw.get("end")),
            pitch_family=_opt_str(raw.get("pitch_family")),
            location_definition=_opt_str(raw.get("location_definition")),
            game_types=_str_tuple(raw.get("game_types")),
            event_population=_opt_str(raw.get("event_population")),
            min_batted_balls=_int_or_none(raw.get("min_batted_balls")),
            note=str(raw.get("note") or "")))
    clarification = None
    raw_clar = data.get("clarification")
    if isinstance(raw_clar, dict) and str(raw_clar.get("question") or "").strip():
        clarification = ClarificationDecision(
            question=str(raw_clar.get("question"))[:1000],
            reason=str(raw_clar.get("reason") or "")[:1000],
            options=_str_tuple(raw_clar.get("options")),
            kind=str(raw_clar.get("kind") or "GENERAL"))
    return CognitionPlan(
        user_goal=str(data.get("user_goal") or "")[:MAX_FREE_TEXT],
        understanding=str(data.get("understanding") or "")[:MAX_FREE_TEXT],
        analysis_strategy=str(data.get("analysis_strategy") or "")[:MAX_FREE_TEXT],
        assumptions=_str_tuple(data.get("assumptions")),
        unresolved=_str_tuple(data.get("unresolved")),
        entities=_str_tuple(data.get("entities")),
        time_hints=_str_tuple(data.get("time_hints")),
        needs_knowledge=bool(data.get("needs_knowledge")),
        knowledge_queries=_str_tuple(data.get("knowledge_queries")),
        research_queries=_str_tuple(data.get("research_queries")),
        needs_batting_stats=bool(data.get("needs_batting_stats")),
        batting_year=_int_or_none(data.get("batting_year")),
        batting_team=_opt_str(data.get("batting_team")),
        batting_entity_names=_str_tuple(data.get("batting_entity_names")),
        batting_metrics=_str_tuple(data.get("batting_metrics")) or ("OPS", "OBP", "SLG", "HR"),
        needs_pitching_stats=bool(data.get("needs_pitching_stats")),
        pitching_year=_int_or_none(data.get("pitching_year")),
        pitching_entity_names=_str_tuple(data.get("pitching_entity_names")),
        local_metrics=tuple(local_metrics),
        clarification=clarification,
        direct_answer=str(data.get("direct_answer") or "")[:MAX_FREE_TEXT],
        source="llm")


def _str_tuple(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _opt_str(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _int_or_none(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# -- deterministic fallback --------------------------------------------------


class DeterministicCognition:
    """Best-effort planner used when the LLM is unavailable. Never blocks the request."""

    _KNOWLEDGE_CUES = ("是什么意思", "是什么", "什么是", "规则", "what is", "what does",
                       "define", "definition", "which division", "属于哪个分区")
    _EV_CUES = ("ev", "exit velocity", "exit velo", "初速", "击球初速", "擊球初速")
    _PITCHING_CUES = ("投手", "pitcher", "era", "whip", "三振", "奪三振", "夺三振",
                      "球速", "投球", "先发", "先發", "变强", "變強")
    _RECENT_CUES = ("最近", "近", "last", "recent", "past", "今年", "本赛季", "本賽季")

    def plan(self, *, message: str, history: str, resolved_entities: tuple[str, ...],
             unknowns: tuple[str, ...], today: str) -> CognitionPlan:
        lowered = message.casefold()
        if any(cue in lowered for cue in self._KNOWLEDGE_CUES):
            return CognitionPlan(user_goal=message, understanding="Definition / knowledge question.",
                                 needs_knowledge=True, knowledge_queries=(message,),
                                 source="deterministic")
        year = _year_in(message) or _year_from_iso(today)
        if resolved_entities:
            local_metrics: tuple[LocalMetricHint, ...] = ()
            if any(cue in lowered for cue in self._EV_CUES):
                local_metrics = (LocalMetricHint(
                    metric="exit_velocity", aggregation="AVG", direction="DESC", limit=10,
                    entity_names=resolved_entities, note=message),)
            return CognitionPlan(
                user_goal=message,
                understanding="Compare the named players using batting and/or local analytics.",
                analysis_strategy="Compare season batting lines and quality of contact.",
                unresolved=unknowns, entities=resolved_entities,
                needs_batting_stats=True, batting_year=year,
                batting_entity_names=resolved_entities,
                batting_metrics=("OPS", "OBP", "SLG", "HR", "BB", "SO"),
                needs_pitching_stats=any(cue in lowered for cue in self._PITCHING_CUES),
                pitching_year=year, pitching_entity_names=resolved_entities,
                local_metrics=local_metrics, source="deterministic")
        return CognitionPlan(
            user_goal=message, understanding="Open-ended baseball question; research then answer.",
            analysis_strategy="Gather web evidence, then synthesize.",
            unresolved=unknowns, research_queries=(message,),
            source="deterministic")

    def compose(self, *, message: str, understanding: str, assumptions: tuple[str, ...],
                evidence: tuple[EvidenceItem, ...]) -> str:
        if not evidence:
            return "I could not find reliable information to answer that yet."
        lines = [evidence[0].summary]
        for item in evidence[:4]:
            if item.text:
                lines.append(item.text[:1200])
        if assumptions:
            lines.append("Assumptions: " + "; ".join(assumptions))
        return "\n\n".join(lines)
