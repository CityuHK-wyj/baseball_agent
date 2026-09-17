"""Deterministic semantic validation: the authority over extractor proposals.

The extractor proposes; this module decides. It validates a closed ``SemanticCandidate``
against the bounded vocabulary, verifies provenance against the raw query, detects
numeric cross-binding and contradictory clauses, and only then emits the canonical typed
domain constraints.

Two failure classes:

* *recoverable* - the extractor's proposal is malformed or ungrounded. The hybrid parser
  may fall back to the high-confidence deterministic extractor.
* *unrecoverable* - the proposal is internally contradictory or re-uses one number for
  two meanings. Falling back would silently pick a meaning, so the request fails closed
  into clarification.
"""

from dataclasses import dataclass

import re

from app.models.contracts import (DEFAULT_RANKING_LIMIT, Constraint, CountConstraint,
                                  LocationConstraint, NumericConstraint,
                                  PitchTypeConstraint, PopulationConstraint,
                                  QualificationConstraint, RankingConstraint)
from app.models.semantic_candidate import (CandidateConstraint, SemanticAmbiguity,
                                           SemanticCandidate, SemanticProvenance)
from app.models.semantic_review import SemanticReviewResult
from app.models.understanding import SemanticUnderstanding
from app.semantic.lexical_anchors import extract_lexical_anchors
from app.semantic.semantic_extractor import SemanticVocabulary
from app.semantic.semantic_reconciler import reconcile_with_anchors

PARSER_VERSION = "hybrid-semantic-v1"

RECOVERABLE_CODES = frozenset({
    "MISSING_FIELD", "UNSUPPORTED_METRIC", "UNSUPPORTED_OPERATOR", "UNSUPPORTED_AGGREGATION",
    "UNSUPPORTED_DIRECTION", "UNSUPPORTED_LOCATION", "UNSUPPORTED_PITCH_FAMILY",
    "UNSUPPORTED_GAME_TYPE", "UNSUPPORTED_EVENT_POPULATION", "UNSUPPORTED_UNIT",
    "UNSUPPORTED_KIND", "EVIDENCE_NOT_GROUNDED", "EVIDENCE_SPAN_MISMATCH",
    "UNGROUNDED_EXPLICIT", "EVIDENCE_METRIC_MISMATCH", "INCOMPATIBLE_EVENT_POPULATION",
    "ANCHOR_CONFLICT", "ANCHOR_MISSING", "UNANCHORED_EXPLICIT",
})

# A numeric metric must not be grounded in a qualification-unit phrase, and its metric
# cue must agree with the claimed metric. This is the deterministic guard against a model
# binding a qualification number to a velocity.
_QUALIFICATION_UNIT_RE = re.compile(r"\b(?:bbe|batted[-\s]?balls?|balls?\s+in\s+play)\b",
                                    re.IGNORECASE)
_ANY_VELOCITY_CUE_RE = re.compile(r"\b(?:mph|miles|velocit|speed|pitch|fastball|heater|release|exit|launch|ev)\b",
                                  re.IGNORECASE)
_PITCH_VELOCITY_CUE_RE = re.compile(r"\b(?:pitch|fastball|heater|release|mph|velocit|speed)\b",
                                    re.IGNORECASE)
_EXIT_VELOCITY_CUE_RE = re.compile(r"\b(?:exit|launch|ev)\b", re.IGNORECASE)


class SemanticValidationError(Exception):
    """A candidate could not be trusted. ``recoverable`` decides fallback vs clarify."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.recoverable = code in RECOVERABLE_CODES


@dataclass(frozen=True)
class SemanticParseResult:
    constraints: tuple[Constraint, ...]
    provenance: tuple[SemanticProvenance, ...] = ()
    ambiguities: tuple[SemanticAmbiguity, ...] = ()
    extractor: str = "deterministic"
    parser_version: str = PARSER_VERSION
    fallback_reason: str = ""
    clarification_reason: str = ""
    location_wording_requested: bool = False
    # Bounded observability: a small structured summary, never raw model text.
    summary: tuple[str, ...] = ()
    # The structured dual-review outcome, when dual review ran. Never hidden reasoning.
    review: SemanticReviewResult | None = None
    # Open-world interpretation. It accompanies the canonical typed constraints so the
    # Planner can reason from natural-language meaning as well as typed facts.
    understanding: SemanticUnderstanding | None = None

    @property
    def failed_closed(self) -> bool:
        return bool(self.clarification_reason)


def _check_evidence(candidate: CandidateConstraint, raw_query: str) -> None:
    evidence = candidate.evidence
    if not evidence.text:
        if candidate.origin in ("USER_EXPLICIT", "USER_CONFIRMED"):
            raise SemanticValidationError(
                "UNGROUNDED_EXPLICIT",
                f"{candidate.kind} is marked explicit but has no evidence text")
        return
    if evidence.text.casefold() not in raw_query.casefold():
        raise SemanticValidationError(
            "EVIDENCE_NOT_GROUNDED", f"{candidate.kind} evidence is not present in the query")
    if evidence.start is not None and evidence.end is not None:
        if raw_query[evidence.start:evidence.end].casefold() != evidence.text.casefold():
            raise SemanticValidationError(
                "EVIDENCE_SPAN_MISMATCH",
                f"{candidate.kind} evidence span does not match the query text")


def _check_metric_evidence(candidate: CandidateConstraint, metric: str) -> None:
    evidence = candidate.evidence.text
    if not evidence:
        return
    if _QUALIFICATION_UNIT_RE.search(evidence) and not _ANY_VELOCITY_CUE_RE.search(evidence):
        raise SemanticValidationError(
            "EVIDENCE_METRIC_MISMATCH",
            f"{metric} is grounded in a qualification phrase {evidence!r}")
    if metric == "exit_velocity" and _PITCH_VELOCITY_CUE_RE.search(evidence) \
            and not _EXIT_VELOCITY_CUE_RE.search(evidence):
        raise SemanticValidationError(
            "EVIDENCE_METRIC_MISMATCH",
            f"exit_velocity is grounded in pitch-velocity evidence {evidence!r}")
    if metric == "pitch_velocity" and _EXIT_VELOCITY_CUE_RE.search(evidence) \
            and not _PITCH_VELOCITY_CUE_RE.search(evidence):
        raise SemanticValidationError(
            "EVIDENCE_METRIC_MISMATCH",
            f"pitch_velocity is grounded in exit-velocity evidence {evidence!r}")


def _require(value, code: str, kind: str):
    if value is None:
        raise SemanticValidationError("MISSING_FIELD", f"{kind} requires the {code} field")
    return value


def _canonical(candidate: CandidateConstraint, vocabulary: SemanticVocabulary) -> Constraint:
    origin = candidate.origin
    if candidate.kind == "NUMERIC":
        metric = _require(candidate.metric, "metric", "NUMERIC")
        if metric not in vocabulary.metrics:
            raise SemanticValidationError("UNSUPPORTED_METRIC", f"unsupported metric {metric!r}")
        _check_metric_evidence(candidate, metric)
        operator = _require(candidate.operator, "operator", "NUMERIC")
        if operator not in vocabulary.operators:
            raise SemanticValidationError("UNSUPPORTED_OPERATOR", f"unsupported operator {operator!r}")
        unit = candidate.unit or "mph"
        if unit not in vocabulary.units:
            raise SemanticValidationError("UNSUPPORTED_UNIT", f"unsupported unit {unit!r}")
        value = _require(candidate.value, "value", "NUMERIC")
        return NumericConstraint(key=metric, operator=operator, value=float(value), unit=unit,
                                 origin=origin)
    if candidate.kind == "PITCH_TYPE":
        family = _require(candidate.family, "family", "PITCH_TYPE")
        if family not in vocabulary.pitch_families:
            raise SemanticValidationError("UNSUPPORTED_PITCH_FAMILY",
                                          f"unsupported pitch family {family!r}")
        return PitchTypeConstraint(family=family, origin=origin)
    if candidate.kind == "LOCATION":
        definition = _require(candidate.definition, "definition", "LOCATION")
        if definition not in vocabulary.locations:
            raise SemanticValidationError("UNSUPPORTED_LOCATION",
                                          f"unsupported location definition {definition!r}")
        return LocationConstraint(definition=definition, origin=origin)
    if candidate.kind == "COUNT":
        if candidate.states:
            for state in candidate.states:
                if state.balls > vocabulary.max_balls or state.strikes > vocabulary.max_strikes:
                    raise SemanticValidationError(
                        "MISSING_FIELD", "count state is outside the valid range")
            return CountConstraint(states=tuple(candidate.states), balls=(), origin=origin)
        if candidate.strikes is None:
            raise SemanticValidationError("MISSING_FIELD", "COUNT requires strikes or states")
        balls = tuple(candidate.balls) if candidate.balls else (0, 1, 2, 3)
        if any(ball > vocabulary.max_balls for ball in balls):
            raise SemanticValidationError("MISSING_FIELD", "ball count is outside the valid range")
        return CountConstraint(strikes=candidate.strikes, balls=balls, origin=origin)
    if candidate.kind == "QUALIFICATION":
        value = _require(candidate.min_batted_balls, "min_batted_balls", "QUALIFICATION")
        if value <= 0:
            raise SemanticValidationError("MISSING_FIELD",
                                          "qualification must be a positive integer")
        return QualificationConstraint(min_batted_balls=int(value), origin=origin)
    if candidate.kind == "POPULATION":
        game_types = tuple(candidate.game_types) if candidate.game_types else ("REGULAR_SEASON",)
        for game_type in game_types:
            if game_type not in vocabulary.game_types:
                raise SemanticValidationError("UNSUPPORTED_GAME_TYPE",
                                              f"unsupported game type {game_type!r}")
        event_population = candidate.event_population or "BATTED_BALL"
        if event_population not in vocabulary.event_populations:
            raise SemanticValidationError("UNSUPPORTED_EVENT_POPULATION",
                                          f"unsupported event population {event_population!r}")
        return PopulationConstraint(game_types=game_types, event_population=event_population,
                                    origin=origin)
    if candidate.kind == "RANKING":
        metric_key = _require(candidate.metric_key, "metric_key", "RANKING")
        if metric_key not in vocabulary.metrics:
            raise SemanticValidationError("UNSUPPORTED_METRIC",
                                          f"unsupported ranking metric {metric_key!r}")
        aggregation = candidate.aggregation or "AVG"
        if aggregation not in vocabulary.aggregations:
            raise SemanticValidationError("UNSUPPORTED_AGGREGATION",
                                          f"unsupported aggregation {aggregation!r}")
        direction = candidate.direction or "DESC"
        if direction not in vocabulary.directions:
            raise SemanticValidationError("UNSUPPORTED_DIRECTION",
                                          f"unsupported direction {direction!r}")
        # A rank request without a stated count keeps the documented default, exactly as
        # the deterministic extractor does; the validator is where absent dimensions get
        # their default.
        limit = candidate.limit or DEFAULT_RANKING_LIMIT
        return RankingConstraint(metric_key=metric_key, aggregation=aggregation,
                                 direction=direction, limit=int(limit), origin=origin)
    raise SemanticValidationError("UNSUPPORTED_KIND", f"unsupported kind {candidate.kind!r}")


def _signature(candidate: CandidateConstraint) -> tuple:
    return (candidate.kind, candidate.metric, candidate.family, candidate.definition,
            candidate.operator, candidate.value, candidate.min_batted_balls,
            tuple((state.balls, state.strikes) for state in candidate.states),
            candidate.strikes, tuple(candidate.balls), candidate.metric_key,
            candidate.aggregation, candidate.direction, candidate.limit,
            tuple(candidate.game_types), candidate.event_population)


def _check_ownership(candidates: tuple[CandidateConstraint, ...]) -> None:
    """One numeric/qualification span must never be reused for a different meaning."""
    by_text: dict[str, tuple] = {}
    by_span: dict[tuple[int, int], tuple] = {}
    for candidate in candidates:
        if candidate.kind not in ("NUMERIC", "QUALIFICATION"):
            continue
        signature = _signature(candidate)
        evidence = candidate.evidence
        if evidence.text:
            key = evidence.text.casefold().strip()
            if key in by_text and by_text[key] != signature:
                raise SemanticValidationError(
                    "DUPLICATE_EVIDENCE_OWNERSHIP",
                    f"evidence {evidence.text!r} is claimed by more than one clause")
            by_text[key] = signature
        if evidence.start is not None and evidence.end is not None:
            span = (evidence.start, evidence.end)
            if span in by_span and by_span[span] != signature:
                raise SemanticValidationError(
                    "DUPLICATE_EVIDENCE_OWNERSHIP",
                    "one query span is claimed by more than one clause")
            by_span[span] = signature


def _check_contradictions(candidates: tuple[CandidateConstraint, ...]) -> None:
    # Exit velocity is only defined on contact. A proposal that ranks by exit velocity
    # over every pitch is not a defensible meaning, so it is rejected (recoverably) and
    # the deterministic extractor supplies the batted-ball population.
    uses_exit_velocity = any(
        (item.kind == "NUMERIC" and item.metric == "exit_velocity")
        or (item.kind == "RANKING" and item.metric_key == "exit_velocity")
        for item in candidates)
    event_populations = {item.event_population for item in candidates
                         if item.kind == "POPULATION"}
    if uses_exit_velocity and "ALL_PITCHES" in event_populations:
        raise SemanticValidationError(
            "INCOMPATIBLE_EVENT_POPULATION",
            "exit velocity is only defined on batted balls, not all pitches")
    rankings = [c for c in candidates if c.kind == "RANKING"]
    ranking_signatures = {(c.metric_key, c.aggregation or "AVG") for c in rankings}
    if len(rankings) > 1 and len(ranking_signatures) > 1:
        raise SemanticValidationError(
            "CONTRADICTORY_AGGREGATION",
            "the request asks for more than one ranking/aggregation without a comparison")
    counts = [c for c in candidates if c.kind == "COUNT"]
    count_signatures = {_signature(c) for c in counts}
    if len(counts) > 1 and len(count_signatures) > 1:
        raise SemanticValidationError("CONTRADICTORY_COUNT",
                                      "the request contains conflicting count states")
    explicit_populations = [c for c in candidates
                            if c.kind == "POPULATION" and c.origin in ("USER_EXPLICIT", "USER_CONFIRMED")]
    population_signatures = {(tuple(c.game_types), c.event_population) for c in explicit_populations}
    if len(population_signatures) > 1:
        raise SemanticValidationError("CONTRADICTORY_POPULATION",
                                      "the request names incompatible populations")
    explicit_locations = [c for c in candidates
                          if c.kind == "LOCATION" and c.origin in ("USER_EXPLICIT", "USER_CONFIRMED")]
    location_signatures = {c.definition for c in explicit_locations}
    if len(location_signatures) > 1:
        raise SemanticValidationError("CONTRADICTORY_LOCATION",
                                      "the request names incompatible location definitions")


def _dedupe(constraints: list[Constraint]) -> tuple[Constraint, ...]:
    seen: list[Constraint] = []
    for item in constraints:
        if item not in seen:
            seen.append(item)
    return tuple(seen)


def validate_candidate(candidate: SemanticCandidate, raw_query: str,
                       vocabulary: SemanticVocabulary,
                       *, fallback_reason: str = "") -> SemanticParseResult:
    """Validate a candidate and return canonical constraints plus provenance.

    Raises ``SemanticValidationError`` when the candidate cannot be trusted.
    """
    for item in candidate.constraints:
        _check_evidence(item, raw_query)
    _check_ownership(candidate.constraints)

    canonical: list[Constraint] = []
    provenance: list[SemanticProvenance] = []
    for item in candidate.constraints:
        constraint = _canonical(item, vocabulary)
        canonical.append(constraint)
        provenance.append(SemanticProvenance(
            kind=constraint.kind, key=constraint.key,
            evidence_text=item.evidence.text, evidence_start=item.evidence.start,
            evidence_end=item.evidence.end, origin=item.origin))

    _check_contradictions(candidate.constraints)

    # Independent lexical reconciliation: a candidate must not contradict what the user
    # actually wrote, and must not drop an explicit restriction the user wrote. The
    # anchors are narrow high-confidence facts, never a full language understanding.
    anchors = extract_lexical_anchors(raw_query)
    for difference in reconcile_with_anchors(tuple(canonical), anchors):
        raise SemanticValidationError(difference.code, difference.detail)

    # Explicit population beats any inferred/default population.
    explicit_populations = [item for item in canonical
                            if isinstance(item, PopulationConstraint)
                            and item.origin in ("USER_EXPLICIT", "USER_CONFIRMED")]
    if explicit_populations:
        canonical = [item for item in canonical
                     if not isinstance(item, PopulationConstraint)
                     or item in explicit_populations]

    deduped = _dedupe(canonical)

    # Defaults only after validation confirms the dimension was absent.
    if deduped and not any(isinstance(item, PopulationConstraint) for item in deduped):
        deduped = (*deduped, PopulationConstraint(origin="SYSTEM_INFERRED"))

    # Unresolved material ambiguity must block execution even when a candidate also
    # proposes a resolved value. A model may identify ambiguity but may never remove the
    # ambiguity that product/domain policy marks as material.
    location_ambiguous = anchors.location_ambiguity or any(
        ambiguity.kind.startswith("location") for ambiguity in candidate.ambiguities)
    summary = tuple(f"{item.kind}:{item.key}" for item in deduped)
    understanding = _build_understanding(candidate, raw_query, deduped)
    return SemanticParseResult(
        constraints=deduped, provenance=tuple(provenance),
        ambiguities=tuple(candidate.ambiguities), extractor=candidate.extractor,
        fallback_reason=fallback_reason,
        location_wording_requested=location_ambiguous,
        summary=summary, understanding=understanding)


def _derive_brief(constraints: tuple[Constraint, ...]) -> str:
    """A deterministic, human-readable brief used when the model supplies none."""
    if not constraints:
        return ""
    parts: list[str] = []
    for item in constraints:
        if isinstance(item, RankingConstraint):
            parts.append(f"rank by {item.aggregation} {item.metric_key} ({item.direction} {item.limit})")
        elif isinstance(item, NumericConstraint):
            parts.append(f"{item.key} {item.operator} {item.value:g} {item.unit}")
        elif isinstance(item, CountConstraint):
            parts.append(f"count {item.exact_states}")
        elif isinstance(item, PitchTypeConstraint):
            parts.append(f"{item.family} pitches")
        elif isinstance(item, LocationConstraint):
            parts.append(f"location {item.definition}")
        elif isinstance(item, QualificationConstraint):
            parts.append(f"minimum {item.min_batted_balls} batted balls")
        elif isinstance(item, PopulationConstraint):
            parts.append(f"population {', '.join(item.game_types)}/{item.event_population}")
    return "; ".join(parts)


def _build_understanding(candidate: SemanticCandidate, raw_query: str,
                         constraints: tuple[Constraint, ...]) -> SemanticUnderstanding:
    """Project an extractor candidate into the open-world understanding contract."""
    return SemanticUnderstanding(
        raw_query=raw_query,
        user_goal=candidate.user_goal or raw_query,
        semantic_brief=candidate.semantic_brief or _derive_brief(constraints),
        planner_notes=candidate.planner_notes,
        analysis_strategy=candidate.analysis_strategy,
        known_constraints=constraints,
        candidate_entity_mentions=tuple(candidate.entity_mentions),
        unresolved_concepts=tuple(candidate.unresolved_concepts),
        search_hints=tuple(candidate.search_hints),
        ambiguities=tuple(ambiguity.kind for ambiguity in candidate.ambiguities),
        interpretations=tuple(candidate.interpretations),
    )
