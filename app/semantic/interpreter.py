"""Semantic Interpreter: preserve what the user meant. No execution, no giant schema."""

from __future__ import annotations

import json
import re
from typing import Any

from app.contracts.models import (
    Ambiguity,
    AnalysisRequest,
    Constraint,
    EntityMention,
    EvidenceRequest,
    EvidenceIntent,
    RelationSemantics,
    Hypothesis,
    Reference,
    ReferenceType,
    SemanticInterpretation,
    TimeExpression,
    UserAssertion,
)

SYSTEM_PROMPT = """You are the semantic understanding layer of a Baseball Agent.

Preserve what the user means. Do not execute anything. Do not force every concept into an
enum. Keep the raw query intact.

Return JSON:
{
 "language": "zh|en|mixed",
 "understanding": "one short paragraph: what the user wants, in their own terms",
 "entities": [{"mention":"大谷翔平","type_hint":"player|team|league|concept|unknown","ref":"user-span-0","carried":false}],
 "time_expressions": [{"text":"2025年","kind":"season|date|range|relative|unknown","year":2025,"ref":"user-span-2"}],
 "analysis_requests": [{"text":"季后赛表现如何","user_span":"季后赛表现如何","objective":"overall postseason performance","analytical_role":"BATTER","ref":"user-span-3",
   "evidence_intent":{"mode":"CONFIRMED_STATE","information_class":"PERFORMANCE"}}],
 "evidence_requests": [{"text":"有没有伤病报道","user_span":"有没有伤病报道","objective":"find injury reports","ref":"user-span-4",
   "evidence_intent":{"mode":"REPORTED_ACCOUNT","information_class":"INJURY"}}],
 "constraints": [{"kind":"season|game_population|date_range|measure|qualification|entity","value":2025,"source_ref":"user-span-2","explicit":true}],
 "analytical_candidates": [
   {"analytical_role":"BATTER|PITCHER",
    "restrictions":[{"dimension":"opposing_pitcher_hand|batter_hand|pitch_type|balls|strikes|inning|zone|game_type","value":"left|right|...","source_text":"exact provenance substring of the user message","attributed_span":"exact substring within source_text expressing ONLY this restriction"}],
    "splits":[{"dimension":"opposing_pitcher_hand|batter_hand|pitch_type|inning|...","source_text":"exact substring"}],
    "limit":5,
    "ordering":[{"metric":"walks","direction":"DESC","source_text":"exact substring"}],
    "qualification":{"basis":"PA|BBE|PITCHES|GAMES|RESULTS","minimum":200,"source_text":"exact substring"},
    "ignored_spans":["exact task/presentation wording that carries no answer-changing meaning"],
    "source_text":"the clause this candidate interprets"}
 ],
 "population_hints": ["POSTSEASON"],
 "ambiguities": [{"description":"...","options":["..."],"material":true}],
 "user_assertions": [{"text":"他已经被交易","ref":"user-span-5"}],
 "hypotheses": [{"statement":"the question is about the 2024 postseason run","origin":"prior|user|context","confidence":"low|medium|high","entities":["大谷翔平"],"basis":"why you propose it"}],
 "turn_type": "INDEPENDENT_GOAL|FOLLOW_UP|CLARIFICATION_ANSWER|CONSTRAINT_REVISION"
}

RULES
- Every analysis/evidence request and user assertion must cite user_span as an exact quote
  from raw_query. text/objective may paraphrase; the quote proves ancestry, not semantic truth.
  Follow-ups may inherit identity and prior USER intent, never assistant-only factual content.
  Set meaning_origin to CURRENT_USER_MESSAGE, DERIVED_CONTEXT (prior USER intent), or
  ASSISTANT_CONTEXT. Assistant-only factual material belongs in hypotheses, not requests
  or user_assertions; citing a real current-user quote does not change its ancestry.
- EVERY analysis_request AND evidence_request must declare its own evidence_intent.mode.
  An analysis_request is a user deliverable, not an internal tool capability or a neutral
  work item. Preserve the requested epistemic role on that owner even if evidence_requests
  also exist. Different owners can have different roles; never copy a sibling's role.
  Do not split one requested outlook into a factual analysis owner plus a prediction owner.
- evidence_intent.mode is the requested evidence role: CONFIRMED_STATE (default factual
  state), REPORTED_ACCOUNT (reporting itself is requested), FIRSTHAND_ACCOUNT (firsthand or
  community accounts themselves are requested), PREDICTION (prediction is requested).
  Interpret scope and negation semantically; mere mention or rejection of a role is not a
  request for it. information_class is GENERIC|INJURY|TRANSACTION|STATUS|PERFORMANCE|GAME|
  INTERVIEW|COMMUNITY|NARRATIVE. Use stable semantic knowledge as context, never current facts.
- Never drop an explicit constraint (year, population, minimum sample, handedness, pitch type).
- entities are real-world subjects ONLY: players, teams, leagues, people, organizations.
  Statistics, metrics, handedness words, pitch types and evaluation words are NOT entities.
  Put those in constraints or analysis_requests instead. Never emit an entity for a term
  like "打击率", "左投", "四缝线速球", "打者" or "本垒打".
- For requested derived arithmetic, include operation_contract on that analysis_request.
  It is the user's ordered analytical meaning, NOT an execution plan. Schema:
  {"key":"delta","kind":"SUBTRACT","output_field":"difference","operands":[
   {"scope":{"measure":"home_runs","analytical_role":"BATTER","subjects":["user's player name"],"season":2023}},
   {"scope":{"measure":"home_runs","analytical_role":"BATTER","subjects":["user's player name"],"season":2024}}]}
  SUBTRACT and RATIO preserve exact left/right order; PERCENT_CHANGE is (left-right)/right*100.
  Other finite kinds: SUM, MEAN, MIN, MAX, MEDIAN; RANK requires parameters.direction ASC/DESC;
  AGGREGATE requires parameters.reducer SUM/MEAN/MIN/MAX/COUNT. Do NOT guess omitted
  operand scope, direction or qualification. Each scope also supports date_range {start,end},
  game_types (official codes), qualification {basis,minimum}. Empty subjects means league
  total, not an unspecified player. Only full-population scalar inputs are supported.
  If the requested computation cannot be represented faithfully, omit the contract;
  it remains unresolved, and tools cannot manufacture one. Never derive it from assistant
  assertions. Existing analytics requests without derived arithmetic need no contract.
- For explicitly requested original scalar statistics, include reporting_contract on that
  analysis_request: {"cells":[{"measure":"home_runs","analytical_role":"BATTER",
  "subjects":["user's player name"],"season":2023}]}. Each cell has exactly the same scope
  vocabulary as an operation's root scope. Include EVERY requested subject/time/measure
  observation; a compound report can contain multiple cells. Only complete, ungrouped,
  unfiltered population scalars are representable; otherwise keep the ordinary analysis
  request with all its constraints. A reporting contract and an operation contract must be
  separate requests. When original observations AND a derived result are requested, retain
  BOTH deliverables with separate exact user_span ancestry. When only a derived result is
  requested, its operands are internal dependencies: do NOT invent reporting requests for
  them. Never replace an original-statistics request with its operation or vice versa.
- Each analysis/evidence request should include relation_semantics with relation_kind and
  relation_kinds (IDENTITY|SCOPE|VALUE|AGGREGATION|COMPARISON|CAUSE|EFFECT|EXPLANATION|QUOTE|
  STATUS|FRESHNESS|EVENT|FACT|GENERAL). Asking for a table of split values is AGGREGATION,
  not a relational assertion such as A > B. Counts/arithmetic and comparative predicates
  are distinct obligations. Preserve all requested operations; never replace a relation
  with a weaker value request. For a current-team answer slot use predicate TEAM_MEMBERSHIP;
  an injury/status/trade-event question is NOT that predicate. Leave unknown predicates empty.
- For statistical requests relation_semantics must preserve the requested OUTPUT units,
  independently of the input subjects: output_granularity PER_SUBJECT for individual
  lines/cells (including ordinary multi-player performance questions), POOLED only for
  an explicitly requested combined result, UNRESOLVED if ambiguous. subject_refs names
  the subjects of this request using the entity references, not every entity in the turn.
  A list of players does NOT imply pooling, a comparison predicate, or ranking.
  Use one reporting_contract cell per subject/time/measure ONLY when the complete
  requested observation is representable as an ungrouped, unfiltered population scalar.
  This contract has NO additional grouping/filter dimensions: preserve such requests
  as ordinary analysis_requests with their analytical_candidates/constraints instead.
  Output granularity does not remove those dimensions. Never put multiple individuals
  in one original-observation cell. Explicit combined sums/means
  still require operation_contract with separate fully scoped individual operands;
  output_granularity by itself grants no computation or evidential authority. Retain
  individual reports AND aggregate results as separate deliverables when both are asked.
- For statistical analysis set analytical_role to BATTER or PITCHER when interpreted,
  including an elliptical continuation of prior USER intent. Use null if unresolved.
  This subject role is not an additional row restriction; do not emit a role-only
  analytical_candidate when analysis_requests already expresses that role.
- Open evaluation words ("为什么最近变强了", "谁更好") stay as analysis_requests text; do not
  invent a metric name for them.
- analytical_candidates express the *meaning* of a restriction or split without inventing
  vocabulary: e.g. "against southpaws", "LHP", "lefty", "左投", "a pitcher throwing from the
  left side" all become {"dimension":"opposing_pitcher_hand","value":"left"}. Prefer the
  semantic dimension names over physical column names. Always include source_text as an
  exact substring of the user message. Emit a candidate ONLY for meaning the user actually
  stated. Keep source_text as provenance; use attributed_span for the smallest literal
  region claiming this particular meaning. Attribution is a proposal, not proof that
  unclaimed wording is irrelevant or that a broad novel region has only one meaning.
  Never invent a restriction, split, limit, ordering, qualification or number that
  is not in the message. If the meaning is unclear, omit it: the deterministic layer keeps
  unclear wording unresolved rather than guessing. These candidates are untrusted and are
  validated against the governed schema; they are not execution instructions.
- game populations: POSTSEASON (季后赛/playoffs), WORLD_SERIES, REGULAR_SEASON (常规赛),
  SPRING_TRAINING, WILDCARD, DIVISION_SERIES, LEAGUE_CHAMPIONSHIP.
- If the user's message refers to the previous turn (e.g. "那去年呢", "他呢", "换个角度"),
  set turn_type to FOLLOW_UP and include the carried entities/scope in the arrays with
  "carried": true. Name the carried entity explicitly using the conversation context.
- If a clarification question is pending and the message answers it, set turn_type to
  CLARIFICATION_ANSWER and produce a complete restatement of the ORIGINAL question enriched
  with the answer.
- user_assertions are claims the user made; they are UNVERIFIED.
- recent_messages with authority "unverified_model_synthesis" are prior agent answers. They are
  NOT established facts and carry no evidence authority: never treat a prior answer as proof,
  as user intent, or as an authoritative constraint. Any fact they mention must be re-established
  through the normal evidence path.
- hypotheses are candidate explanations, entities or investigation directions. They may be
  derived from your general knowledge and are explicitly NOT verified facts. Use them to
  record what you suspect the user means or what should be investigated; never present a
  hypothesis as an established fact. Confidence is a prior, not verification.
"""


def _request_schema_feedback(data):
    problems = []
    for key in ('analysis_requests', 'evidence_requests'):
        for index, entry in enumerate(data.get(key, []) or []):
            if not isinstance(entry, dict) or entry.get('meaning_origin') == 'ASSISTANT_CONTEXT':
                continue
            intent = entry.get('evidence_intent')
            if not isinstance(intent, dict) or 'mode' not in intent or _evidence_intent(entry) is None:
                problems.append(f'{key}[{index}] requires its own valid evidence_intent.mode')
            if key == 'analysis_requests':
                if entry.get('operation_contract') and entry.get('reporting_contract'):
                    problems.append(f'{key}[{index}] must separate original reporting from computation')
                for name, ground in (('operation_contract', 'operation'), ('reporting_contract', 'reporting')):
                    if not entry.get(name):
                        continue
                    try:
                        if ground == 'operation':
                            from app.semantic.operations import ground_operation_contract
                            ground_operation_contract(entry[name])
                        else:
                            from app.semantic.reporting import ground_reporting_contract
                            ground_reporting_contract(entry[name])
                    except (TypeError, ValueError) as exc:
                        problems.append(f'{key}[{index}].{name}: {exc}')
    return '; '.join(problems)


class SemanticInterpreter:
    def __init__(self, llm, knowledge=None):
        self.llm = llm
        self.knowledge = knowledge

    @property
    def available(self) -> bool:
        return bool(self.llm and self.llm.configured)

    def interpret(
        self,
        raw_query: str,
        *,
        conversation_context: dict[str, Any] | None = None,
        clarification: dict[str, Any] | None = None,
    ) -> SemanticInterpretation:
        if not self.available:
            return self._fallback(raw_query)
        payload = {"raw_query": raw_query, "conversation": conversation_context or {}}
        if self.knowledge is not None:
            payload["stable_semantic_knowledge"] = self.knowledge.semantic_context(raw_query)
        if clarification:
            payload["pending_clarification"] = clarification
        try:
            # One bounded schema repair, with materially new validation feedback. This
            # never guesses a role from wording or copies it from another owner.
            for attempt in range(2):
                result = self.llm.complete_json(
                    role="semantic", system=SYSTEM_PROMPT,
                    user=json.dumps(payload, ensure_ascii=False), max_tokens=3200)
                feedback = _request_schema_feedback(result.data)
                if not feedback or attempt == 1:
                    break
                payload['prior_proposal'] = result.data
                payload['schema_feedback'] = feedback
            interpretation = self._from_json(raw_query, result.data, conversation_context=conversation_context)
            if feedback:
                interpretation.structured_hints['unresolved_request_schema'] = feedback
                interpretation.ambiguities.append(Ambiguity(
                    description=feedback, options=[], material=True))
            return interpretation
        except Exception:  # noqa: BLE001
            return self._fallback(raw_query)

    def propose_clarification_action(self, answer: str, pending) -> str | None:
        """Untrusted free-text hypothesis; backend options must confirm before applying."""
        if not self.available:
            return None
        try:
            result = self.llm.complete_json(
                role="semantic",
                system=("Classify this clarification answer into exactly one proposed action: "
                        "DISCHARGE_RESIDUE, PRESERVE_RESIDUE, REPLACE_INTERPRETATION, "
                        "or UNKNOWN. Return JSON {\"action\":\"...\"}. Do not restate or add requirements. "
                        "This is only a proposal: the user must confirm a backend option."),
                user=json.dumps({"answer": answer, "question": pending.question,
                                 "options": pending.options}, ensure_ascii=False),
                max_tokens=80,
            )
            action = result.data.get("action")
            return action if action in pending.option_actions else None
        except Exception:  # noqa: BLE001 - untrusted proposal is optional
            return None

    # ------------------------------------------------------------------ parsing
    def _from_json(self, raw_query: str, data: dict[str, Any], *, conversation_context=None) -> SemanticInterpretation:
        interpretation = SemanticInterpretation(
            raw_query=raw_query,
            language=str(data.get("language") or detect_language(raw_query)),
            understanding=str(data.get("understanding") or raw_query),
            turn_type=_normalize_turn(data.get("turn_type")),
            structured_hints={"interpreter": "llm"},
        )
        for entry in data.get("entities", []) or []:
            if isinstance(entry, dict) and entry.get("mention"):
                interpretation.entities.append(
                    EntityMention(
                        mention=str(entry["mention"]),
                        type_hint=str(entry.get("type_hint", "unknown")),
                        ref=str(entry.get("ref", "")),
                        carried=bool(entry.get("carried", False)),
                    )
                )
        for entry in data.get("time_expressions", []) or []:
            if isinstance(entry, dict) and entry.get("text"):
                year = entry.get("year")
                interpretation.time_expressions.append(
                    TimeExpression(text=str(entry["text"]), kind=str(entry.get("kind", "unknown")), year=int(year) if isinstance(year, (int, float)) else None, ref=str(entry.get("ref", "")))
                )
        for key, request_type in (("analysis_requests", AnalysisRequest), ("evidence_requests", EvidenceRequest)):
            for entry in data.get(key, []) or []:
                if not isinstance(entry, dict) or not entry.get("text"):
                    continue
                origin = entry.get("meaning_origin", "CURRENT_USER_MESSAGE")
                if origin == "ASSISTANT_CONTEXT":
                    interpretation.hypotheses.append(Hypothesis(statement=str(entry["text"]), origin="context",
                        basis="prior assistant material, not user-origin meaning"))
                    continue
                if origin not in {"CURRENT_USER_MESSAGE", "DERIVED_CONTEXT"}:
                    continue
                span = str(entry.get("user_span") or entry["text"])
                if not span:
                    continue
                if span not in raw_query:
                    # Some models quote the antecedent user's clause for an elliptical
                    # continuation. Resolve that ancestry against backend USER history,
                    # never assistant synthesis; the current message remains the root.
                    context = conversation_context or {}
                    user_messages = [m.get('text', '') for m in context.get('recent_messages', [])
                                     if m.get('role') == 'user']
                    prior_raw = (context.get('active_goal') or {}).get('raw_query', '')
                    user_messages.append(prior_raw)
                    if interpretation.turn_type != 'FOLLOW_UP' or not any(
                        isinstance(text, str) and span in text for text in user_messages if text):
                        continue
                    span, origin = raw_query, 'DERIVED_CONTEXT'
                role = entry.get('analytical_role')
                typed_fields = {'analytical_role': role if role in {'BATTER', 'PITCHER'} else None} if key == 'analysis_requests' else {}
                if key == 'analysis_requests' and entry.get('operation_contract'):
                    from app.semantic.operations import ground_operation_contract
                    try:
                        typed_fields['operation_contract'] = ground_operation_contract(entry['operation_contract'])
                    except (ValueError, TypeError):
                        pass  # unsupported interpretation remains unresolved, never widened
                if key == 'analysis_requests' and entry.get('reporting_contract'):
                    from app.semantic.reporting import ground_reporting_contract
                    try:
                        typed_fields['reporting_contract'] = ground_reporting_contract(entry['reporting_contract'])
                    except (ValueError, TypeError):
                        pass  # preserve the original request; no weakened scalar contract
                getattr(interpretation, key).append(request_type(
                    **typed_fields,
                    text=str(entry["text"]), objective=str(entry.get("objective") or entry["text"]),
                    ref=str(entry.get("ref", "")), user_span=span, meaning_origin=origin,
                    relation_semantics=_requested_relation(entry), evidence_intent=_evidence_intent(entry)))
        for entry in data.get("constraints", []) or []:
            if isinstance(entry, dict) and entry.get("kind"):
                interpretation.constraints.append(Constraint(kind=str(entry["kind"]), value=entry.get("value"), source_ref=str(entry.get("source_ref", "")), explicit=bool(entry.get("explicit", True))))
        for entry in data.get("analytical_candidates", []) or []:
            # Untrusted interpreter candidate. It is grounded (schema + source-span proof)
            # by RequirementBuilder before it can define any execution obligation.
            if isinstance(entry, dict) and entry:
                interpretation.constraints.append(
                    Constraint(kind="analytical", value=entry, source_ref=str(entry.get("source_text", "")), explicit=True)
                )
        for hint in data.get("population_hints", []) or []:
            if isinstance(hint, str):
                interpretation.population_hints.append(hint.upper())
        for entry in data.get("ambiguities", []) or []:
            if isinstance(entry, dict) and entry.get("description"):
                interpretation.ambiguities.append(Ambiguity(description=str(entry["description"]), options=[str(o) for o in entry.get("options", [])], material=bool(entry.get("material", True))))
        for entry in data.get("user_assertions", []) or []:
            if isinstance(entry, dict) and entry.get("text"):
                span = str(entry.get("user_span") or entry["text"])
                if span and span in raw_query and entry.get("meaning_origin", "CURRENT_USER_MESSAGE") == "CURRENT_USER_MESSAGE":
                    interpretation.user_assertions.append(UserAssertion(text=str(entry["text"]), ref=str(entry.get("ref", "")), user_span=span))
        for entry in data.get("hypotheses", []) or []:
            if isinstance(entry, dict) and entry.get("statement"):
                interpretation.hypotheses.append(
                    Hypothesis(
                        statement=str(entry["statement"]),
                        origin=str(entry.get("origin", "prior")),
                        basis=str(entry.get("basis", "")),
                        confidence=str(entry.get("confidence", "low")),
                        entities=[str(e) for e in entry.get("entities", []) if e],
                        ref=str(entry.get("ref", "")),
                    )
                )
        interpretation.references.append(Reference(ref_type=ReferenceType.USER_MESSAGE, ref_id="user-message", source="user", span=raw_query))
        # Keep explicit seasonal facts even if the model omitted a constraint.
        if not any(c.kind == "season" for c in interpretation.constraints):
            for expression in interpretation.time_expressions:
                if expression.year:
                    interpretation.constraints.append(Constraint(kind="season", value=expression.year, source_ref=expression.ref, explicit=True))
        return interpretation

    def _fallback(self, raw_query: str) -> SemanticInterpretation:
        interpretation = SemanticInterpretation(
            raw_query=raw_query,
            language=detect_language(raw_query),
            understanding=raw_query,
            structured_hints={"interpreter": "fallback"},
        )
        for match in re.finditer(r"(19|20)\d{2}", raw_query):
            year = int(match.group(0))
            interpretation.time_expressions.append(TimeExpression(text=match.group(0), kind="season", year=year))
            interpretation.constraints.append(Constraint(kind="season", value=year, source_ref=f"span:{match.start()}", explicit=True))
        if any(token in raw_query for token in ("季后赛", "postseason", "playoff")):
            interpretation.population_hints.append("POSTSEASON")
        # Offline fallback: preserve the mention itself as a candidate-identity hypothesis so
        # downstream layers can investigate even without the interpreter model.
        for match in re.finditer(r"[A-Za-z][A-Za-z.'\- ]{3,30}", raw_query):
            candidate = match.group(0).strip()
            if candidate and not candidate.lower().startswith(("the ", "what ", "how ", "why ")):
                interpretation.hypotheses.append(
                    Hypothesis(statement=f"the subject may be '{candidate}'", origin="context", confidence="low", entities=[candidate])
                )
                break
        interpretation.references.append(Reference(ref_type=ReferenceType.USER_MESSAGE, ref_id="user-message", source="user", span=raw_query))
        return interpretation


def _requested_relation(entry) -> RelationSemantics | None:
    raw = entry.get('relation_semantics')
    if not isinstance(raw, dict):
        return None
    try:
        return RelationSemantics.model_validate(raw)
    except ValueError:
        return None


def _evidence_intent(entry) -> EvidenceIntent | None:
    try:
        raw = entry.get("evidence_intent")
        return EvidenceIntent.model_validate(raw) if isinstance(raw, dict) else None
    except ValueError:
        return None


def detect_language(text: str) -> str:
    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in text or "")
    has_latin = any("a" <= ch.lower() <= "z" for ch in text or "")
    if has_cjk and has_latin:
        return "mixed"
    if has_cjk:
        return "zh"
    if has_latin:
        return "en"
    return "unknown"


def _normalize_turn(value: Any) -> str:
    allowed = {"INDEPENDENT_GOAL", "FOLLOW_UP", "CLARIFICATION_ANSWER", "CONSTRAINT_REVISION"}
    text = str(value or "").upper()
    return text if text in allowed else "INDEPENDENT_GOAL"
