"""Bounded semantic vocabulary and natural-language extractors.

The vocabulary is the *only* domain language an extractor may use. It is deliberately
small and semantic (``pitch_velocity``, not ``release_speed``). The LLM extractor never
receives physical columns, raw rows, SQL examples or previous artifacts.

Two extractors implement the same Protocol:

* ``DeterministicSemanticExtractor`` wraps the high-confidence lexical parser. It is the
  tested default and the safe fallback.
* ``LLMSemanticExtractor`` asks a provider-agnostic ``ModelProvider`` for a closed
  ``SemanticCandidate`` and parses it strictly.
"""

import json
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from app.llm.parsing import parse_json_object
from app.llm.prompts import SEMANTIC_PROMPT, SEMANTIC_REVIEW_PROMPT
from app.llm.provider import ModelProvider, ProviderError
from app.models.semantic_candidate import (CandidateConstraint, EvidenceSpan,
                                           SemanticAmbiguity, SemanticCandidate)
from app.models.understanding import MAX_FREE_TEXT
from app.semantic import analytics_intent as _ai
from app.semantic.field_mapping import (BATTER_RELATIVE_UPPER_EDGE,
                                        DEFAULT_LOCATION_DEFINITIONS, ZONE_UPPER_OUTSIDE,
                                        ZONE_UPPER_THIRD)


class SemanticExtractionError(RuntimeError):
    """Base for extractor failures. Messages are safe to surface."""


class SemanticProviderError(SemanticExtractionError):
    """The model provider failed (network, credential, timeout)."""


class SemanticSchemaError(SemanticExtractionError):
    """The model output did not match the closed candidate schema."""


@dataclass(frozen=True)
class SemanticVocabulary:
    """Closed semantic vocabulary for v0.1 analytics interpretation."""

    metrics: tuple[str, ...] = ("pitch_velocity", "exit_velocity")
    operators: tuple[str, ...] = ("EQ", "GT", "GTE", "LT", "LTE")
    aggregations: tuple[str, ...] = ("AVG", "MAX", "MIN", "SUM")
    directions: tuple[str, ...] = ("ASC", "DESC")
    origins: tuple[str, ...] = ("USER_EXPLICIT", "USER_CONFIRMED", "CONTEXT_INFERRED",
                                "SYSTEM_INFERRED", "SYSTEM_DEFAULT")
    game_types: tuple[str, ...] = ("REGULAR_SEASON", "POSTSEASON", "SPRING_TRAINING",
                                   "EXHIBITION")
    event_populations: tuple[str, ...] = ("BATTED_BALL", "MEASURED_CONTACT", "ALL_PITCHES")
    locations: tuple[str, ...] = tuple(item.definition for item in DEFAULT_LOCATION_DEFINITIONS)
    pitch_families: tuple[str, ...] = ("fastball", "breaking", "offspeed")
    units: tuple[str, ...] = ("mph",)
    max_balls: int = 3
    max_strikes: int = 2
    count_state_note: str = ("Explicit counts are exact states: 0-2 is [(0,2)]; "
                             "'0-2 or 1-1' is [(0,2),(1,1)] and never a cartesian product.")

    def as_prompt_json(self) -> str:
        return json.dumps({
            "metrics": self.metrics,
            "operators": self.operators,
            "aggregations": self.aggregations,
            "directions": self.directions,
            "origins": self.origins,
            "game_types": self.game_types,
            "event_populations": self.event_populations,
            "location_definitions": self.locations,
            "pitch_families": self.pitch_families,
            "units": self.units,
            "count": {"max_balls": self.max_balls, "max_strikes": self.max_strikes,
                      "note": self.count_state_note},
        }, ensure_ascii=False)


class SemanticExtractor(Protocol):
    name: str

    def extract(self, raw_query: str, vocabulary: SemanticVocabulary) -> SemanticCandidate: ...


# -- Deterministic extractor -------------------------------------------------


def _first_span(patterns: tuple[str, ...], text: str) -> tuple[int, int] | None:
    import re
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match is not None:
            return match.span()
    return None


def _evidence(raw_query: str, span: tuple[int, int] | None,
              metric_patterns: tuple[str, ...] = ()) -> EvidenceSpan:
    if span is None:
        return EvidenceSpan()
    start, end = span
    for pattern in metric_patterns:
        import re
        for match in re.finditer(pattern, raw_query[max(0, start - 30):start], re.IGNORECASE):
            start = max(0, start - 30) + match.start()
    text = raw_query[start:end]
    if not text.strip():
        return EvidenceSpan()
    return EvidenceSpan(text=text, start=start, end=end)


def _numeric_span(raw_query: str, constraint) -> tuple[int, int] | None:
    masked = _ai._mask_spans(raw_query, _ai._protected_spans(raw_query.casefold()))
    for match in _ai._VELOCITY_THRESHOLD_RE.finditer(masked):
        operator = _ai._OPERATOR_TOKENS.get(match.group("op").casefold().strip())
        if operator == constraint.operator and float(match.group("value")) == float(constraint.value):
            return match.span()
    return None


class DeterministicSemanticExtractor:
    """High-confidence lexical extraction expressed as a SemanticCandidate.

    It handles facts it can prove from fixed vocabulary (dates and years are handled by
    the normalizer). Composition that it cannot prove is left to the validator to reject
    or to clarification; it never guesses a metric for an unowned number.
    """

    name = "deterministic"

    def extract(self, raw_query: str, vocabulary: SemanticVocabulary) -> SemanticCandidate:
        intent = _ai.extract_analytical_constraints(raw_query)
        constraints = tuple(self._convert(item, raw_query) for item in intent.constraints)
        ambiguities: tuple[SemanticAmbiguity, ...] = ()
        if intent.location_wording_requested:
            span = _first_span(_ai._AMBIGUOUS_LOCATION_CUES, raw_query)
            ambiguities = (SemanticAmbiguity(
                kind="location.upper_edge",
                evidence=_evidence(raw_query, span),
                question="Which upper-zone definition should the pitch-location filter use?",
                candidates=(BATTER_RELATIVE_UPPER_EDGE, ZONE_UPPER_THIRD, ZONE_UPPER_OUTSIDE)),)
        return SemanticCandidate(constraints=constraints, ambiguities=ambiguities,
                                 extractor=self.name)

    def _convert(self, constraint, raw_query: str) -> CandidateConstraint:
        kind = constraint.kind
        if kind == "COUNT":
            if constraint.states:
                span = _first_span((_ai._COUNT_TOKEN_RE.pattern,), raw_query)
                return CandidateConstraint(
                    kind="COUNT", states=constraint.states, origin=constraint.origin,
                    evidence=_evidence(raw_query, span))
            patterns = (_ai._COUNT_TOKEN_RE.pattern,) if constraint.origin == "USER_EXPLICIT" \
                else _ai._TWO_STRIKE_CUES
            span = _first_span(patterns, raw_query)
            return CandidateConstraint(
                kind="COUNT", strikes=constraint.strikes, balls=constraint.balls,
                origin=constraint.origin, evidence=_evidence(raw_query, span))
        if kind == "PITCH_TYPE":
            span = _first_span(_ai._FASTBALL_CUES, raw_query)
            return CandidateConstraint(kind="PITCH_TYPE", family=constraint.family,
                                       origin=constraint.origin,
                                       evidence=_evidence(raw_query, span))
        if kind == "NUMERIC":
            span = _numeric_span(raw_query, constraint)
            patterns = _ai._EXIT_VELOCITY_CUES if constraint.key == "exit_velocity" \
                else _ai._PITCH_VELOCITY_CUES
            return CandidateConstraint(
                kind="NUMERIC", metric=constraint.key, operator=constraint.operator,
                value=float(constraint.value), unit=constraint.unit, origin=constraint.origin,
                evidence=_evidence(raw_query, span, patterns))
        if kind == "RANKING":
            span = _first_span((_ai._RANKING_RE.pattern,), raw_query)
            if span is None:
                # "rank hitters by maximum exit velocity" has no explicit N.
                verb = _first_span((_ai._RANK_VERB_RE.pattern,), raw_query)
                metric_span = _first_span((_ai._RANKING_METRIC_RE.pattern,), raw_query)
                if verb is not None and metric_span is not None:
                    span = (verb[0], metric_span[1])
            return CandidateConstraint(
                kind="RANKING", metric_key=constraint.metric_key,
                aggregation=constraint.aggregation, direction=constraint.direction,
                limit=constraint.limit, origin=constraint.origin,
                evidence=_evidence(raw_query, span))
        if kind == "QUALIFICATION":
            span = _first_span((_ai._QUALIFICATION_RE.pattern,), raw_query)
            return CandidateConstraint(
                kind="QUALIFICATION", min_batted_balls=constraint.min_batted_balls,
                origin=constraint.origin, evidence=_evidence(raw_query, span))
        if kind == "POPULATION":
            span = _first_span(
                (*_ai._POSTSEASON_CUES, *_ai._SPRING_TRAINING_CUES, *_ai._EXHIBITION_CUES,
                 *_ai._ALL_GAMES_CUES, *_ai._REGULAR_SEASON_CUES, *_ai._MEASURED_CONTACT_CUES,
                 *_ai._ALL_PITCHES_CUES, *_ai._BATTED_BALL_CUES), raw_query)
            return CandidateConstraint(
                kind="POPULATION", game_types=tuple(constraint.game_types),
                event_population=constraint.event_population, origin=constraint.origin,
                evidence=_evidence(raw_query, span))
        if kind == "LOCATION":
            span = _first_span(tuple(pattern for pattern, _ in _ai._EXPLICIT_LOCATION_CUES),
                               raw_query)
            return CandidateConstraint(kind="LOCATION", definition=constraint.definition,
                                       origin=constraint.origin,
                                       evidence=_evidence(raw_query, span))
        raise SemanticSchemaError(f"Unsupported deterministic constraint kind: {kind}")


# -- LLM extractor -----------------------------------------------------------

# The provider returns JSON, so the model sometimes spells origin casually. Map the
# observed synonyms onto the closed origin vocabulary; an unknown value is left as-is
# so the closed schema still rejects it.
_ORIGIN_SYNONYMS: dict[str, str] = {
    "user": "USER_EXPLICIT",
    "explicit": "USER_EXPLICIT",
    "user_stated": "USER_EXPLICIT",
    "user_confirmed": "USER_CONFIRMED",
    "confirmed": "USER_CONFIRMED",
    "context_inferred": "CONTEXT_INFERRED",
    "inferred": "SYSTEM_INFERRED",
    "system_inferred": "SYSTEM_INFERRED",
    "default": "SYSTEM_DEFAULT",
    "system_default": "SYSTEM_DEFAULT",
}


def _drop_nulls(value):
    """Recursively drop ``None`` values so absent and null mean the same thing.

    The candidate schema defaults every inapplicable field, so a model that emits all
    keys with ``null`` for the irrelevant ones must not fail schema validation.
    """
    if isinstance(value, dict):
        return {key: _drop_nulls(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_drop_nulls(item) for item in value]
    return value


def _normalize_origin(constraint: dict) -> None:
    origin = constraint.get("origin")
    if isinstance(origin, str):
        key = origin.strip().casefold().replace("-", "_").replace(" ", "_")
        if key in _ORIGIN_SYNONYMS:
            constraint["origin"] = _ORIGIN_SYNONYMS[key]


def _normalize_evidence(evidence, raw_query: str) -> None:
    """Reconcile model-supplied offsets with the query.

    ``evidence.text`` is the grounding source; offsets are derived provenance. A model
    often reports the right text with drifting offsets, so recompute them from the text
    when they disagree. The deterministic validator still rejects any text that is not
    actually present in the query.
    """
    if not isinstance(evidence, dict):
        return
    text = evidence.get("text")
    if not isinstance(text, str) or not text:
        return
    start, end = evidence.get("start"), evidence.get("end")
    if isinstance(start, int) and isinstance(end, int) \
            and 0 <= start <= end <= len(raw_query) \
            and raw_query[start:end].casefold() == text.casefold():
        return
    found = raw_query.casefold().find(text.casefold())
    if found >= 0:
        evidence["start"] = found
        evidence["end"] = found + len(text)
    else:
        evidence.pop("start", None)
        evidence.pop("end", None)


def _normalize_constraint_fields(constraint: dict, raw_query: str) -> None:
    """Accept structural field-name variants without inventing semantics.

    Grounding is still enforced by the deterministic validator against the query; this
    only lets a model reuse a value it already produced under the wrong label or casing.
    """
    _normalize_origin(constraint)
    _normalize_evidence(constraint.get("evidence"), raw_query)
    for field in ("kind", "operator", "aggregation", "direction", "event_population"):
        value = constraint.get(field)
        if isinstance(value, str):
            constraint[field] = value.strip().upper()
    if isinstance(constraint.get("game_types"), list):
        constraint["game_types"] = [str(item).strip().upper()
                                    for item in constraint["game_types"]]
    if constraint.get("kind") == "RANKING" and not constraint.get("metric_key"):
        constraint["metric_key"] = constraint.get("metric") or constraint.get("key")
    if constraint.get("kind") == "NUMERIC" and not constraint.get("metric"):
        constraint["metric"] = constraint.get("key") or constraint.get("metric_key")


def _normalize_candidate_payload(payload: dict, raw_query: str) -> dict:
    payload = _drop_nulls(payload)
    for constraint in payload.get("constraints") or ():
        if isinstance(constraint, dict):
            _normalize_constraint_fields(constraint, raw_query)
    for ambiguity in payload.get("ambiguities") or ():
        if isinstance(ambiguity, dict):
            _normalize_evidence(ambiguity.get("evidence"), raw_query)
    _normalize_free_form(payload)
    return payload


def _coerce_text(value) -> str:
    """Coerce an open-world free-form value to a bounded string.

    Models naturally return small objects (for example ``{name, type}`` for an entity
    mention). Free-form fields must not reject the whole interpretation because of that
    shape; the closed *typed* constraints remain strict.
    """
    if isinstance(value, str):
        return value[:MAX_FREE_TEXT]
    if isinstance(value, dict):
        for key in ("name", "title", "description", "text", "summary", "claim",
                    "question", "value", "label"):
            if isinstance(value.get(key), str):
                return value[key][:MAX_FREE_TEXT]
        return json.dumps(value, ensure_ascii=False)[:MAX_FREE_TEXT]
    if isinstance(value, (list, tuple)):
        return "; ".join(_coerce_text(item) for item in value)[:MAX_FREE_TEXT]
    if value is None:
        return ""
    return str(value)[:MAX_FREE_TEXT]


def _normalize_free_form(payload: dict) -> None:
    for field in ("user_goal", "semantic_brief", "planner_notes", "analysis_strategy"):
        if field in payload and not isinstance(payload[field], str):
            payload[field] = _coerce_text(payload[field])
    for field in ("entity_mentions", "unresolved_concepts", "search_hints", "interpretations"):
        if field not in payload or payload[field] is None:
            continue
        value = payload[field]
        items = value if isinstance(value, (list, tuple)) else [value]
        payload[field] = [_coerce_text(item) for item in items if _coerce_text(item)]


def _validate_constraints(raw_constraints, vocabulary: SemanticVocabulary
                          ) -> tuple[CandidateConstraint, ...]:
    """Validate constraints individually. A structurally malformed constraint is dropped;
    an invalid *origin* is a semantic vocabulary violation and is still rejected.

    A constraint that parses but uses a value outside the closed vocabulary (for example a
    free-form location definition) is dropped from the proposal rather than nullifying the
    whole interpretation. The deterministic validator and lexical anchors remain the
    authority over whatever survives; an explicit fact the model dropped still fails
    anchoring.
    """
    valid: list[CandidateConstraint] = []
    for raw in raw_constraints or ():
        if not isinstance(raw, dict):
            continue
        try:
            item = CandidateConstraint.model_validate(raw)
        except ValidationError as error:
            invalid_origin = any(
                error_info.get("loc") and "origin" in error_info["loc"]
                for error_info in error.errors())
            if invalid_origin:
                raise SemanticSchemaError(
                    "constraint origin is not in the closed origin vocabulary") from None
            # Otherwise drop the malformed constraint and keep the rest.
            continue
        if _constraint_in_vocabulary(item, vocabulary):
            valid.append(item)
    return tuple(valid)


def _constraint_in_vocabulary(item: CandidateConstraint,
                             vocabulary: SemanticVocabulary) -> bool:
    if item.kind == "NUMERIC":
        return (item.metric in vocabulary.metrics and item.operator in vocabulary.operators
                and (item.unit or "mph") in vocabulary.units and item.value is not None)
    if item.kind == "PITCH_TYPE":
        return item.family in vocabulary.pitch_families
    if item.kind == "LOCATION":
        return item.definition in vocabulary.locations
    if item.kind == "COUNT":
        if item.states:
            return all(state.balls <= vocabulary.max_balls and state.strikes <= vocabulary.max_strikes
                       for state in item.states)
        return (item.strikes is not None and item.strikes <= vocabulary.max_strikes
                and all(ball <= vocabulary.max_balls for ball in (item.balls or (0,))))
    if item.kind == "QUALIFICATION":
        return bool(item.min_batted_balls and item.min_batted_balls > 0)
    if item.kind == "POPULATION":
        game_types = item.game_types or ("REGULAR_SEASON",)
        return (all(game in vocabulary.game_types for game in game_types)
                and (item.event_population or "BATTED_BALL") in vocabulary.event_populations)
    if item.kind == "RANKING":
        return (item.metric_key in vocabulary.metrics
                and (item.aggregation or "AVG") in vocabulary.aggregations
                and (item.direction or "DESC") in vocabulary.directions)
    return False


def _validate_ambiguities(raw_ambiguities) -> tuple[SemanticAmbiguity, ...]:
    valid: list[SemanticAmbiguity] = []
    for raw in raw_ambiguities or ():
        if not isinstance(raw, dict):
            continue
        try:
            valid.append(SemanticAmbiguity.model_validate(raw))
        except ValidationError:
            continue
    return tuple(valid)


class LLMSemanticExtractor:
    """Constrained extraction through the provider-agnostic ``ModelProvider`` seam."""

    name = "llm"

    def __init__(self, provider: ModelProvider, model: str, timeout: float = 30.0,
                 prompt=SEMANTIC_PROMPT) -> None:
        self._provider = provider
        self._model = model
        self._timeout = timeout
        self._prompt = prompt

    def extract(self, raw_query: str, vocabulary: SemanticVocabulary) -> SemanticCandidate:
        prompt = self._prompt.render(query=raw_query,
                                     vocabulary_json=vocabulary.as_prompt_json())
        try:
            response = self._provider.complete(prompt, model=self._model, timeout=self._timeout)
        except ProviderError as error:
            raise SemanticProviderError(str(error)) from None
        try:
            data = parse_json_object(response.text)
        except ValueError as error:
            raise SemanticSchemaError(str(error)) from None
        if not isinstance(data, dict):
            raise SemanticSchemaError("Model output must be a JSON object")
        payload = {key: value for key, value in data.items()
                   if key not in ("extractor", "model")}
        payload = _normalize_candidate_payload(payload, raw_query)
        payload["extractor"] = self.name
        payload["model"] = response.model or self._model
        try:
            payload["constraints"] = _validate_constraints(payload.get("constraints"),
                                                            vocabulary)
            payload["ambiguities"] = _validate_ambiguities(payload.get("ambiguities"))
            return SemanticCandidate.model_validate(payload)
        except ValidationError as error:
            raise SemanticSchemaError(
                f"Model output does not match the semantic candidate schema: {error.error_count()} error(s)"
            ) from None


class LLMSemanticReviewer(LLMSemanticExtractor):
    """Independent second reader. Never shown the extractor's candidate."""

    name = "llm-reviewer"

    def __init__(self, provider: ModelProvider, model: str, timeout: float = 30.0) -> None:
        super().__init__(provider, model, timeout, prompt=SEMANTIC_REVIEW_PROMPT)
