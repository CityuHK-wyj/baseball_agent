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
from app.llm.prompts import SEMANTIC_PROMPT
from app.llm.provider import ModelProvider, ProviderError
from app.models.semantic_candidate import (CandidateConstraint, EvidenceSpan,
                                           SemanticAmbiguity, SemanticCandidate)
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
        payload["extractor"] = self.name
        payload["model"] = response.model or self._model
        try:
            return SemanticCandidate.model_validate(payload)
        except ValidationError as error:
            raise SemanticSchemaError(
                f"Model output does not match the semantic candidate schema: {error.error_count()} error(s)"
            ) from None
