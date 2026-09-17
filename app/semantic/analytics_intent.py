"""Deterministic extraction of typed analytical constraints from a query.

The target query — top-N exit velocity over two-strike, fastball > 95 mph, upper-zone
pitches — needs typed semantics (count, pitch velocity, pitch family, location, ranking,
population) rather than opaque free-form text. This module turns deterministic
keyword/pattern cues into those typed constraints. It never maps to physical columns
(that belongs to the field mapping layer) and never silently redefines explicit user
intent: ``0-2`` stays exactly ``balls=0, strikes=2``, ``exit velocity >= 95`` maps to
exit velocity and ``fastballs >= 95`` to pitch velocity, and an explicit aggregation
word is preserved rather than defaulted away.
"""

import re
from dataclasses import dataclass

from app.models.contracts import (DEFAULT_EVENT_POPULATION, DEFAULT_GAME_TYPES,
                                  DEFAULT_RANKING_LIMIT, Constraint, CountConstraint,
                                  CountState, LocationConstraint, NumericConstraint,
                                  PitchTypeConstraint, PopulationConstraint,
                                  QualificationConstraint, RankingConstraint)
from app.semantic.field_mapping import (BATTER_RELATIVE_UPPER_EDGE, ZONE_UPPER_OUTSIDE,
                                        ZONE_UPPER_THIRD)

# Ordered cues for the location wording. Explicit thirds do not need a clarification.
# "Just above the zone" is deliberately ambiguous: Statcast zones 11-12 are upper
# outside quadrants, not a strict predicate that plate_z exceeds the batter's sz_top.
_EXPLICIT_LOCATION_CUES: tuple[tuple[str, str], ...] = (
    (r"\bupper\s+third\b", ZONE_UPPER_THIRD),
    (r"\btop\s+third\b", ZONE_UPPER_THIRD),
    (r"\bhigh\s+third\b", ZONE_UPPER_THIRD),
)

_AMBIGUOUS_LOCATION_CUES: tuple[str, ...] = (
    r"\bupper\s+edge\b", r"\btop\s+of\s+the\s+strike\s+zone\b", r"\bupper\s+part\b",
    r"\bhigh\s+in\s+the\s+zone\b", r"\bupper\s+zone\b", r"\bup\s+in\s+the\s+zone\b",
    r"\bjust\s+above\b", r"\babove\s+the\s+strike\s+zone\b", r"\babove\s+the\s+zone\b",
    # 'high fastball' has several defensible readings (upper third, batter-relative
    # upper edge, or the upper outside quadrants). It must clarify, not guess.
    r"\bhigh\s+(?:fastballs?|heat|pitches?|four-seamers?)\b",
)

# Generic two-strike wording. Explicit counts (``0-2``) are handled separately and are
# never broadened into this set.
_TWO_STRIKE_CUES: tuple[str, ...] = (
    r"\btwo\s+strikes\b", r"\b2\s+strikes\b", r"\btwo-strike\b", r"\b2-strike\b",
    r"\bafter\s+reaching\s+two\s+strikes\b", r"\bwith\s+two\s+strikes\b",
)

# Explicit baseball counts as written ``balls-strikes``. Only single digits so date
# ranges (``2023-12-31``) never match.
_COUNT_TOKEN_RE = re.compile(
    r"(?<![\d.-])(?P<balls>[0-3])\s*-\s*(?P<strikes>[0-2])(?![\d.-])")

_FASTBALL_CUES: tuple[str, ...] = (r"\bfastballs?\b", r"\bheater(s)?\b", r"\bgas\b")

_EXIT_VELOCITY_CUES: tuple[str, ...] = (
    r"\bexit\s+velocit(?:y|ies)\b", r"\bexit\s+velo\b", r"\bev\b", r"\blaunch\s+speed\b",
)
_PITCH_VELOCITY_CUES: tuple[str, ...] = (
    r"\bpitch\s+velocit(?:y|ies)\b", r"\bpitch\s+velo\b", r"\brelease\s+speed\b",
    r"\bfastballs?\b", r"\bheaters?\b",
)

_VELOCITY_THRESHOLD_RE = re.compile(
    r"(?P<op>>=|<=|>|<|=|"
    r"greater\s+than\s+or\s+equal\s+to|less\s+than\s+or\s+equal\s+to|"
    r"no\s+less\s+than|no\s+more\s+than|at\s+least|at\s+most|"
    r"greater\s+than|more\s+than|exceeds?|exceeding|beyond|"
    r"above|over|below|under|less\s+than)"
    r"\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mph|miles\s+per\s+hour)?",
    re.IGNORECASE,
)

_OPERATOR_TOKENS: dict[str, str] = {
    ">=": "GTE", "greater than or equal to": "GTE", "no less than": "GTE",
    "at least": "GTE",
    "<=": "LTE", "less than or equal to": "LTE", "no more than": "LTE", "at most": "LTE",
    ">": "GT", "above": "GT", "over": "GT", "greater than": "GT", "more than": "GT",
    "beyond": "GT", "exceeds": "GT", "exceeding": "GT",
    "<": "LT", "below": "LT", "under": "LT", "less than": "LT",
    "=": "EQ",
}

_RANKING_RE = re.compile(r"\b(?P<dir>top|bottom)\s+(?P<limit>\d+)\b", re.IGNORECASE)
# Explicit ranking verb without N, for example "rank hitters by maximum exit velocity".
_RANK_VERB_RE = re.compile(r"\brank(?:ed|s|ing)?\b", re.IGNORECASE)
# Explicit ranking clause markers. The metric after the marker owns the ranking, so a
# filtered metric mentioned earlier in the sentence cannot steal it.
_RANKING_MARKER_RE = re.compile(r"\b(?:ranked|sorted|ordered)\s+by\b|\bby\b", re.IGNORECASE)
_RANKING_METRIC_RE = re.compile(
    r"\b(?:by\s+)?(?:(?P<agg>maximum|max|highest|peak|average|avg|mean|minimum|min|"
    r"lowest|total|sum)\s+)?"
    r"(?P<metric>exit\s+velocit(?:y|ies)|exit\s+velo|ev|launch\s+speed|"
    r"pitch\s+velocit(?:y|ies)|pitch\s+velo|release\s+speed)\b",
    re.IGNORECASE,
)
_AGGREGATIONS: dict[str, str] = {
    "maximum": "MAX", "max": "MAX", "highest": "MAX", "peak": "MAX",
    "average": "AVG", "avg": "AVG", "mean": "AVG",
    "minimum": "MIN", "min": "MIN", "lowest": "MIN",
    "total": "SUM", "sum": "SUM",
}
_RANKING_METRICS: dict[str, str] = {
    "exit velocity": "exit_velocity", "exit velocities": "exit_velocity",
    "exit velo": "exit_velocity", "ev": "exit_velocity", "launch speed": "exit_velocity",
    "pitch velocity": "pitch_velocity", "pitch velocities": "pitch_velocity",
    "pitch velo": "pitch_velocity", "release speed": "pitch_velocity",
}

_QUALIFICATION_RE = re.compile(
    r"(?:\b(?:minimum|min|at\s+least|no\s+less\s+than)\s+(?:of\s+)?|>=)\s*"
    r"(?P<value>\d+)\s*"
    r"(?P<unit>batted[-\s]?balls?|batted[-\s]?ball\s+events?|bbe|balls\s+in\s+play|"
    r"qualifying\s+events?|qualifying\s+batted\s+balls?)\b",
    re.IGNORECASE,
)

_POSTSEASON_CUES: tuple[str, ...] = (r"\bpostseason\b", r"\bplayoffs?\b")
_SPRING_TRAINING_CUES: tuple[str, ...] = (r"\bspring\s+training\b", r"\bspring\s+games?\b")
_EXHIBITION_CUES: tuple[str, ...] = (
    r"\bexhibition\b", r"\bexhibition\s+games?\b", r"\bpreseason\b", r"\bfriendly\b")
_REGULAR_SEASON_CUES: tuple[str, ...] = (r"\bregular\s+season\b", r"\bregular-season\b")
_ALL_GAMES_CUES: tuple[str, ...] = (
    r"\ball\s+games?\b", r"\ball\s+game\s+types?\b", r"\bany\s+game\s+type\b")

_MEASURED_CONTACT_CUES: tuple[str, ...] = (
    r"\bmeasured\s+contact\b", r"\ball\s+contact\b", r"\bcontact\s+events?\b",
    r"\bany\s+contact\b")
_ALL_PITCHES_CUES: tuple[str, ...] = (
    r"\ball\s+pitches\b", r"\bevery\s+pitch\b", r"\bpitch[- ]level\b",
    r"\ball\s+qualifying\s+pitches\b")
_BATTED_BALL_CUES: tuple[str, ...] = (
    r"\bbatted[-\s]?balls?\b", r"\bballs?\s+in\s+play\b", r"\bbbe\b", r"\bfair\s+balls?\b")

_ALL_GAME_TYPES: tuple[str, ...] = (
    "REGULAR_SEASON", "POSTSEASON", "SPRING_TRAINING", "EXHIBITION")


@dataclass(frozen=True)
class AnalyticsIntent:
    constraints: tuple[Constraint, ...]
    location_wording_requested: bool


def _matches(text: str, cues: tuple[str, ...]) -> bool:
    return any(re.search(cue, text, re.IGNORECASE) for cue in cues)


def _nearest_metric(window: str) -> str | None:
    """Return the metric cue closest to the end of ``window`` (the threshold)."""
    best_position = -1
    best_metric: str | None = None
    for cues, metric in ((_EXIT_VELOCITY_CUES, "exit_velocity"),
                         (_PITCH_VELOCITY_CUES, "pitch_velocity")):
        for cue in cues:
            for match in re.finditer(cue, window, re.IGNORECASE):
                if match.start() > best_position:
                    best_position = match.start()
                    best_metric = metric
    return best_metric


def _mask_spans(text: str, spans: tuple[tuple[int, int], ...] | list[tuple[int, int]]) -> str:
    """Blank out protected spans while preserving character offsets.

    Numeric ownership is not a suggestion: a number that belongs to a qualification,
    count, date or ranking limit must never be scanned as a velocity threshold.
    """
    chars = list(text)
    for start, end in spans:
        for index in range(max(0, start), min(len(chars), end)):
            chars[index] = " "
    return "".join(chars)


def _protected_spans(lowered: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for match in re.finditer(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)", lowered):
        spans.append(match.span())
    for match in _COUNT_TOKEN_RE.finditer(lowered):
        spans.append(match.span())
    for match in _QUALIFICATION_RE.finditer(lowered):
        spans.append(match.span())
    for match in _RANKING_RE.finditer(lowered):
        spans.append(match.span("limit"))
    for match in re.finditer(r"(?<!\d)(?:19|20)\d{2}(?!\d)", lowered):
        spans.append(match.span())
    return spans


def _velocity_constraints(raw_query: str) -> list[NumericConstraint]:
    constraints: list[NumericConstraint] = []
    masked = _mask_spans(raw_query, _protected_spans(raw_query.casefold()))
    for match in _VELOCITY_THRESHOLD_RE.finditer(masked):
        operator = _OPERATOR_TOKENS.get(match.group("op").casefold().strip())
        if operator is None:
            continue
        unit = (match.group("unit") or "").casefold()
        window = masked[max(0, match.start() - 60):match.start()]
        metric = _nearest_metric(window)
        if metric is None:
            if not unit:
                # No metric cue and no explicit unit: not a defensible velocity filter.
                continue
            metric = "pitch_velocity"
        constraints.append(NumericConstraint(
            key=metric, operator=operator, value=float(match.group("value")),
            unit="mph", origin="USER_EXPLICIT"))
    return constraints


def _ranking_constraint(lowered: str) -> RankingConstraint | None:
    ranking = _RANKING_RE.search(lowered)
    if ranking is not None:
        direction = "ASC" if ranking.group("dir").casefold() == "bottom" else "DESC"
        limit = int(ranking.group("limit"))
        marker = _RANKING_MARKER_RE.search(lowered, ranking.end())
        clause_start = marker.end() if marker is not None else ranking.end()
        metric_match = _RANKING_METRIC_RE.search(lowered, clause_start)
        if metric_match is None:
            # No metric after an explicit marker: fall back to the text after "top N" so a
            # terse "top 5 exit velocity" still resolves, matching the previous behavior.
            metric_match = _RANKING_METRIC_RE.search(lowered, ranking.end())
    else:
        # "rank hitters by maximum exit velocity" has no explicit N. Use the documented
        # default limit rather than dropping the ranking entirely.
        verb = _RANK_VERB_RE.search(lowered)
        marker = _RANKING_MARKER_RE.search(lowered, verb.end()) if verb is not None else None
        if marker is None:
            return None
        direction, limit = "DESC", DEFAULT_RANKING_LIMIT
        metric_match = _RANKING_METRIC_RE.search(lowered, marker.end())
    if metric_match is None:
        return None
    aggregation_word = (metric_match.group("agg") or "").casefold()
    aggregation = _AGGREGATIONS.get(aggregation_word, "AVG")
    metric_label = " ".join(metric_match.group("metric").casefold().split())
    metric_key = _RANKING_METRICS.get(metric_label)
    if metric_key is None:
        return None
    return RankingConstraint(metric_key=metric_key, aggregation=aggregation,
                             direction=direction, limit=limit, origin="USER_EXPLICIT")


def _count_constraint(lowered: str) -> CountConstraint | None:
    explicit = _COUNT_TOKEN_RE.findall(lowered)
    if explicit:
        states = tuple(sorted({(int(ball), int(strike)) for ball, strike in explicit}))
        strike_levels = {strike for _, strike in states}
        if len(strike_levels) == 1:
            # Uniform strike level: the compact form is exact (every listed ball count
            # with that strike count).
            return CountConstraint(
                strikes=strike_levels.pop(), balls=tuple(sorted({b for b, _ in states})),
                origin="USER_EXPLICIT")
        # Mixed strike levels form an explicit state set, never a cartesian product.
        return CountConstraint(
            states=tuple(CountState(balls=balls, strikes=strikes) for balls, strikes in states),
            balls=(), origin="USER_EXPLICIT")
    if _matches(lowered, _TWO_STRIKE_CUES):
        return CountConstraint(strikes=2, balls=(0, 1, 2, 3), origin="SYSTEM_INFERRED")
    return None


def _qualification_constraint(lowered: str) -> QualificationConstraint | None:
    match = _QUALIFICATION_RE.search(lowered)
    if match is None:
        return None
    return QualificationConstraint(min_batted_balls=int(match.group("value")),
                                   origin="USER_EXPLICIT")


def _population_constraint(lowered: str) -> PopulationConstraint:
    explicit = False
    if _matches(lowered, _POSTSEASON_CUES):
        game_types = ("POSTSEASON",)
        explicit = True
    elif _matches(lowered, _SPRING_TRAINING_CUES):
        game_types = ("SPRING_TRAINING",)
        explicit = True
    elif _matches(lowered, _EXHIBITION_CUES):
        game_types = ("EXHIBITION",)
        explicit = True
    elif _matches(lowered, _ALL_GAMES_CUES):
        game_types = _ALL_GAME_TYPES
        explicit = True
    elif _matches(lowered, _REGULAR_SEASON_CUES):
        game_types = ("REGULAR_SEASON",)
        explicit = True
    else:
        game_types = DEFAULT_GAME_TYPES

    if _matches(lowered, _MEASURED_CONTACT_CUES):
        event_population = "MEASURED_CONTACT"
        explicit = True
    elif _matches(lowered, _ALL_PITCHES_CUES):
        event_population = "ALL_PITCHES"
        explicit = True
    elif _matches(lowered, _BATTED_BALL_CUES):
        event_population = "BATTED_BALL"
        explicit = True
    else:
        event_population = DEFAULT_EVENT_POPULATION

    return PopulationConstraint(
        game_types=tuple(game_types), event_population=event_population,
        origin="USER_EXPLICIT" if explicit else "SYSTEM_INFERRED")


def extract_analytical_constraints(raw_query: str) -> AnalyticsIntent:
    """Return typed analytical constraints plus whether a location clarification is due."""
    lowered = raw_query.casefold()
    constraints: list[Constraint] = []

    count = _count_constraint(lowered)
    if count is not None:
        constraints.append(count)

    if _matches(lowered, _FASTBALL_CUES):
        constraints.append(PitchTypeConstraint(family="fastball", origin="SYSTEM_INFERRED"))

    constraints.extend(_velocity_constraints(raw_query))

    ranking = _ranking_constraint(lowered)
    if ranking is not None:
        constraints.append(ranking)

    qualification = _qualification_constraint(lowered)
    if qualification is not None:
        constraints.append(qualification)

    explicit_location: str | None = None
    for pattern, definition in _EXPLICIT_LOCATION_CUES:
        if re.search(pattern, lowered):
            explicit_location = definition
            break
    ambiguous_location = _matches(lowered, _AMBIGUOUS_LOCATION_CUES)
    if explicit_location is not None:
        constraints.append(LocationConstraint(definition=explicit_location,
                                              origin="SYSTEM_INFERRED"))
        location_wording_requested = False
    else:
        location_wording_requested = ambiguous_location

    if not constraints:
        return AnalyticsIntent(constraints=(), location_wording_requested=False)

    # Every analytical query carries the population it was computed over. The default is
    # regular-season fair batted balls, but it is explicit on the objective, requirement
    # and artifact rather than implied by the physical query.
    constraints.append(_population_constraint(lowered))
    return AnalyticsIntent(constraints=tuple(constraints),
                           location_wording_requested=location_wording_requested)


def location_clarification_options() -> tuple[tuple[str, str, str], ...]:
    """(value, label, rationale) triples for the upper-location clarification."""
    return (
        (ZONE_UPPER_THIRD, "Upper third of the strike zone (zones 1-3)",
         "high inside the strike zone, the common reading of 'near the upper edge'"),
        (ZONE_UPPER_OUTSIDE, "Upper outside quadrants (zones 11-12)",
         "the Statcast shadow zones laterally outside the upper half; these codes are not "
         "a strict above-sz_top predicate"),
        (BATTER_RELATIVE_UPPER_EDGE, "Exact batter-relative upper edge (needs sz_top/sz_bot)",
         "three-inch band at or below the batter-specific zone top; requires retained zone fields"),
    )
