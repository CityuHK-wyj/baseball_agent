"""Deterministic extraction of typed analytical constraints from a query.

The target query — top-N exit velocity over two-strike, fastball > 95 mph, upper-zone
pitches — needs typed semantics (count, pitch velocity, pitch family, location, ranking)
rather than opaque free-form text. This module turns deterministic keyword/pattern cues
into those typed constraints. It never maps to physical columns (that belongs to the
field mapping layer) and never silently redefines the user's location wording.
"""

import re
from dataclasses import dataclass

from app.models.contracts import (CountConstraint, Constraint, LocationConstraint,
                                  NumericConstraint, PitchTypeConstraint, RankingConstraint)
from app.semantic.field_mapping import (BATTER_RELATIVE_UPPER_EDGE, ZONE_ABOVE_UPPER_EDGE,
                                        ZONE_UPPER_THIRD)

# Ordered cues for the location wording. Explicit thirds/edges do not need a
# clarification; the bare "upper edge" wording is genuinely ambiguous.
_EXPLICIT_LOCATION_CUES: tuple[tuple[str, str], ...] = (
    (r"\bupper\s+third\b", ZONE_UPPER_THIRD),
    (r"\btop\s+third\b", ZONE_UPPER_THIRD),
    (r"\bhigh\s+third\b", ZONE_UPPER_THIRD),
    (r"\bjust\s+above\b", ZONE_ABOVE_UPPER_EDGE),
    (r"\babove\s+the\s+strike\s+zone\b", ZONE_ABOVE_UPPER_EDGE),
)

_AMBIGUOUS_LOCATION_CUES: tuple[str, ...] = (
    r"\bupper\s+edge\b", r"\btop\s+of\s+the\s+strike\s+zone\b", r"\bupper\s+part\b",
    r"\bhigh\s+in\s+the\s+zone\b", r"\bupper\s+zone\b", r"\bup\s+in\s+the\s+zone\b",
)

_TWO_STRIKE_CUES: tuple[str, ...] = (
    r"\btwo\s+strikes\b", r"\b2\s+strikes\b", r"\btwo-strike\b", r"\b2-strike\b",
    r"\bafter\s+reaching\s+two\s+strikes\b", r"\b0-2\b", r"\b1-2\b", r"\b2-2\b",
    r"\b0-2\s*,\s*1-2\s*,\s*2-2\b",
)

_FASTBALL_CUES: tuple[str, ...] = (r"\bfastballs?\b", r"\bheater(s)?\b", r"\bgas\b")

# Pitch velocity above/below a mph threshold near fastball/pitch wording.
_PITCH_VELOCITY_RE = re.compile(
    r"(?P<dir>above|over|greater\s+than|more\s+than|exceeds?|beyond|>)\s*(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>mph|miles\s+per\s+hour)|\b(?P<gt>>)\s*(?P<value2>\d+(?:\.\d+)?)\s*(?P<unit2>mph)",
    re.IGNORECASE)

_RANKING_RE = re.compile(
    r"\b(?P<dir>top|bottom)\s+(?P<limit>\d+)\b.*?\bby\s+exit\s+velocity\b"
    r"|\bby\s+exit\s+velocity\b.*?\b(?P<dir2>top|bottom)\s+(?P<limit2>\d+)\b",
    re.IGNORECASE)


@dataclass(frozen=True)
class AnalyticsIntent:
    constraints: tuple[Constraint, ...]
    location_wording_requested: bool


def extract_analytical_constraints(raw_query: str) -> AnalyticsIntent:
    """Return typed analytical constraints plus whether a location clarification is due."""
    lowered = raw_query.casefold()
    constraints: list[Constraint] = []

    if any(re.search(cue, lowered) for cue in _TWO_STRIKE_CUES):
        constraints.append(CountConstraint(strikes=2, balls=(0, 1, 2, 3),
                                           origin="SYSTEM_INFERRED"))

    if any(re.search(cue, lowered) for cue in _FASTBALL_CUES):
        constraints.append(PitchTypeConstraint(family="fastball", origin="SYSTEM_INFERRED"))

    velocity = _PITCH_VELOCITY_RE.search(raw_query)
    if velocity is not None:
        value = float(velocity.group("value") or velocity.group("value2"))
        direction = (velocity.group("dir") or velocity.group("gt") or "").strip()
        operator = "LT" if direction in ("below", "under") else "GT"
        constraints.append(NumericConstraint(key="pitch_velocity", operator=operator,
                                             value=value, unit="mph",
                                             origin="SYSTEM_INFERRED"))

    ranking = _RANKING_RE.search(lowered)
    if ranking is not None:
        limit = int(ranking.group("limit") or ranking.group("limit2"))
        direction = (ranking.group("dir") or ranking.group("dir2") or "top").strip()
        constraints.append(RankingConstraint(metric_key="exit_velocity", aggregation="AVG",
                                             direction="ASC" if direction == "bottom" else "DESC",
                                             limit=limit, origin="SYSTEM_INFERRED"))

    explicit_location: str | None = None
    for pattern, definition in _EXPLICIT_LOCATION_CUES:
        if re.search(pattern, lowered):
            explicit_location = definition
            break
    ambiguous_location = any(re.search(cue, lowered) for cue in _AMBIGUOUS_LOCATION_CUES)
    if explicit_location is not None:
        constraints.append(LocationConstraint(definition=explicit_location,
                                              origin="SYSTEM_INFERRED"))
        return AnalyticsIntent(constraints=tuple(constraints), location_wording_requested=False)

    if ambiguous_location:
        # Keep the semantic default explicit (batter-relative upper edge) but surface a
        # clarification because the wording is genuinely ambiguous and the exact
        # definition requires fields the local archive may not provide.
        return AnalyticsIntent(constraints=tuple(constraints), location_wording_requested=True)

    return AnalyticsIntent(constraints=tuple(constraints), location_wording_requested=False)


def location_clarification_options() -> tuple[tuple[str, str, str], ...]:
    """(value, label, rationale) triples for the upper-edge location clarification."""
    return (
        (ZONE_UPPER_THIRD, "Upper third of the strike zone (zones 1-3)",
         "high in the strike zone, the common reading of 'near the upper edge'"),
        (ZONE_ABOVE_UPPER_EDGE, "Just above the strike zone (zones 11-12)",
         "the upper edge / shadow zone above the strike zone"),
        (BATTER_RELATIVE_UPPER_EDGE, "Exact batter-relative upper edge (needs sz_top/sz_bot)",
         "most precise definition; the current local archive lacks those fields"),
    )
