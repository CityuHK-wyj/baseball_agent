"""Scope comparison: requested vs actual coverage.

Every Need and Artifact can describe its scope. This module compares them and produces
explicit coverage gaps. The central invariant:

    requested temporal scope != evidence temporal scope  ->  coverage gap

The gap is a first-class value visible to the planner and sufficiency judge; it is not
papered over with prose and it never silently satisfies the Need.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models.artifact_runtime import RuntimeArtifact, Scope
from app.models.contracts import TimeRange


@dataclass(frozen=True)
class ScopeComparison:
    entity: float = 1.0
    temporal: float = 1.0
    population: float = 1.0
    measure: float = 1.0
    quality: float = 1.0
    reasons: tuple[str, ...] = ()
    gaps: tuple[str, ...] = field(default_factory=tuple)

    @property
    def blocking(self) -> bool:
        """True when a scope gap must prevent the Need/Goal from being SATISFIED."""
        return bool(self.gaps)

    @property
    def aggregate(self) -> float:
        return round((self.entity + self.temporal + self.population + self.measure
                      + self.quality) / 5.0, 4)


def _norm(value: str) -> str:
    return value.strip().casefold()


_POPULATION_CATEGORY_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pitchers", ("pitcher", "starter", "reliever", "rotation", "arm")),
    ("players", ("roster", "player", "batter", "hitter", "position player",
                  "position-player", "lineup")),
    ("teams", ("team", "club", "franchise")),
    ("league", ("league", "mlb")),
)


def population_category(text: str) -> str:
    """Coarse population category so flexible naming still compares meaningfully."""
    normalized = _norm(text)
    if not normalized:
        return ""
    for category, tokens in _POPULATION_CATEGORY_TOKENS:
        if any(token in normalized for token in tokens):
            return category
    return normalized


# General baseball measure families: a free-form requested measure and a physical
# catalog field match when they share a family. This is domain vocabulary, not a
# query-specific mapping.
_MEASURE_FAMILY_TOKENS: dict[str, tuple[str, ...]] = {
    "exit_velocity": ("ev", "exit", "launch", "hard", "hardhit", "launchspeed"),
    "pitch_velocity": ("pitch velocity", "release", "velo", "releasespeed", "pitchvelo"),
    "spin_rate": ("spin", "spinrate"),
    "launch_angle": ("launchangle", "angle"),
    "distance": ("distance", "hitdistance"),
    "woba": ("woba",),
    "batting_average": ("avg", "average", "ba", "battingaverage"),
    "ops": ("ops", "onbaseplusslugging"),
    "obp": ("obp", "onbase"),
    "slg": ("slg", "slugging"),
    "era": ("era", "earnedrunaverage"),
    "whip": ("whip", "walksperinning"),
    "strikeouts": ("strikeout", "so", "krate", "k"),
    "walks": ("walk", "bb", "bbrate"),
}


def measure_families(text: str) -> set[str]:
    normalized = _norm(text)
    if not normalized:
        return set()
    squeezed = "".join(ch for ch in normalized if ch.isalnum() or ch == " ")
    families: set[str] = set()
    for family, tokens in _MEASURE_FAMILY_TOKENS.items():
        for token in tokens:
            if len(token) <= 2:
                if re.search(r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])", squeezed):
                    families.add(family)
            elif token in squeezed:
                families.add(family)
    return families


def _entity_coverage(requested: tuple[str, ...], actual: tuple[str, ...]
                     ) -> tuple[float, list[str], list[str]]:
    reasons: list[str] = []
    gaps: list[str] = []
    if not requested:
        return 1.0, reasons, gaps
    actual_norm = {_norm(item) for item in actual}
    if not actual_norm:
        gaps.append("evidence does not identify any requested entity")
        return 0.0, reasons, gaps
    matched = 0
    for entity in requested:
        needle = _norm(entity)
        if any(needle == item or needle in item or item in needle for item in actual_norm):
            matched += 1
    coverage = matched / len(requested)
    if coverage < 1.0:
        missing = [item for item in requested
                   if not any(_norm(item) == a or _norm(item) in a or a in _norm(item)
                              for a in actual_norm)]
        gaps.append("evidence missing requested entities: " + ", ".join(missing))
    return coverage, reasons, gaps


def _overlap_days(window: TimeRange, other: TimeRange) -> int:
    start = max(window.start, other.start)
    end = min(window.end, other.end)
    return max((end - start).days + 1, 0)


def _temporal_coverage(requested: Scope, actual: Scope
                       ) -> tuple[float, list[str], list[str]]:
    reasons: list[str] = []
    gaps: list[str] = []
    if requested.seasons and not requested.time_range:
        if not actual.seasons:
            gaps.append("requested season scope is not stated by the evidence")
            return 0.0, reasons, gaps
        matched = len(set(requested.seasons) & set(actual.seasons))
        coverage = matched / len(set(requested.seasons))
        if coverage < 1.0:
            gaps.append("evidence does not cover requested season(s): "
                        + ", ".join(str(item) for item in requested.seasons))
        return coverage, reasons, gaps
    if requested.time_range is None:
        return 1.0, reasons, gaps
    if actual.time_range is None:
        gaps.append("requested date window is not covered by the evidence")
        return 0.0, reasons, gaps
    overlap = _overlap_days(requested.time_range, actual.time_range)
    requested_days = max((requested.time_range.end - requested.time_range.start).days + 1, 1)
    actual_days = max((actual.time_range.end - actual.time_range.start).days + 1, 1)
    # An artifact aggregated over a *broader* window than requested is not evidence for
    # the requested window, even though it happens to contain it. Coverage is the
    # overlap over the larger of the two spans, so both supersets and subsets are gaps.
    coverage = min(overlap / max(requested_days, actual_days), 1.0)
    if coverage < 1.0:
        covers_requested = (actual.time_range.start <= requested.time_range.start
                            and actual.time_range.end >= requested.time_range.end)
        if covers_requested and actual_days > requested_days:
            gaps.append(
                f"evidence aggregates over a broader window {actual.time_range.start}.."
                f"{actual.time_range.end} than the requested {requested.time_range.start}.."
                f"{requested.time_range.end}")
        else:
            gaps.append(
                f"requested window {requested.time_range.start}..{requested.time_range.end} "
                f"is not covered by evidence window {actual.time_range.start}.."
                f"{actual.time_range.end}")
    return coverage, reasons, gaps


def _population_coverage(requested: Scope, actual: Scope
                         ) -> tuple[float, list[str], list[str]]:
    reasons: list[str] = []
    gaps: list[str] = []
    if not requested.population:
        return 1.0, reasons, gaps
    if not actual.population:
        gaps.append("evidence does not state its population")
        return 0.0, reasons, gaps
    requested_category = population_category(requested.population)
    actual_category = population_category(actual.population)
    if requested_category and requested_category == actual_category:
        return 1.0, reasons, gaps
    if _norm(requested.population) == _norm(actual.population):
        return 1.0, reasons, gaps
    gaps.append(f"requested population {requested.population!r} differs from "
                f"evidence population {actual.population!r}")
    return 0.0, reasons, gaps


def _measure_coverage(requested: Scope, actual: Scope) -> tuple[float, list[str], list[str]]:
    reasons: list[str] = []
    gaps: list[str] = []
    if not requested.metric:
        return 1.0, reasons, gaps
    if not actual.metric:
        gaps.append(f"evidence does not state the requested measure {requested.metric!r}")
        return 0.0, reasons, gaps
    if _norm(requested.metric) == _norm(actual.metric):
        return 1.0, reasons, gaps
    requested_families = measure_families(requested.metric)
    actual_families = measure_families(actual.metric)
    if requested_families & actual_families:
        return 1.0, reasons, gaps
    requested_tokens = set(_norm(requested.metric).replace("_", " ").split())
    actual_tokens = set(_norm(actual.metric).replace("_", " ").split())
    if requested_tokens & actual_tokens:
        return 1.0, reasons, gaps
    gaps.append(f"requested measure {requested.metric!r} differs from evidence "
                f"measure {actual.metric!r}")
    return 0.0, reasons, gaps


def _quality(artifact: RuntimeArtifact | None, confidence: float) -> float:
    if artifact is None:
        return max(0.0, min(confidence, 1.0))
    base = max(0.0, min(artifact.confidence, 1.0))
    if artifact.status == "OK":
        return base
    if artifact.status == "PARTIAL":
        return min(base, 0.5)
    if artifact.status == "EMPTY":
        return min(base, 0.2)
    return 0.0


def compare_scope(requested: Scope | None, actual: Scope | None, *,
                  artifact: RuntimeArtifact | None = None,
                  confidence: float = 0.5) -> ScopeComparison:
    if requested is None:
        return ScopeComparison(quality=_quality(artifact, confidence))
    if actual is None:
        return ScopeComparison(
            entity=0.0, temporal=0.0, population=0.0, measure=0.0,
            quality=_quality(artifact, confidence),
            gaps=("evidence declares no scope, so it cannot be shown to satisfy the request",))
    entity, r1, g1 = _entity_coverage(requested.entities, actual.entities)
    temporal, r2, g2 = _temporal_coverage(requested, actual)
    population, r3, g3 = _population_coverage(requested, actual)
    measure, r4, g4 = _measure_coverage(requested, actual)
    # A roster / entity-mapping artifact is a set of player identities, not a time-series
    # measurement. When it matches the requested entities, an unstated time range is not
    # a coverage gap (membership is governed by the roster source, not a date aggregate).
    if (artifact is not None and artifact.kind in ("team_roster", "entity_mapping")
            and entity >= 1.0 and not requested.metric):
        temporal, g2 = 1.0, []
    reasons = tuple(r1 + r2 + r3 + r4)
    gaps = tuple(g1 + g2 + g3 + g4)
    return ScopeComparison(entity=entity, temporal=temporal, population=population,
                           measure=measure, quality=_quality(artifact, confidence),
                           reasons=reasons, gaps=gaps)


def scope_from_time_range(window: TimeRange | None, *, entities: tuple[str, ...] = (),
                          metric: str = "", population: str = "",
                          game_types: tuple[str, ...] = (),
                          event_population: str = "",
                          source_coverage: tuple[str, ...] = ()) -> Scope:
    return Scope(entities=entities, population=population, time_range=window,
                 game_types=game_types, metric=metric, event_population=event_population,
                 source_coverage=source_coverage)
