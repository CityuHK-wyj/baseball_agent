"""Semantic reconciliation.

Two jobs, both deliberately deterministic:

1. ``reconcile_with_anchors`` compares canonical candidate constraints against the narrow
   lexical anchors of the raw query. It detects a candidate value that contradicts what
   the user wrote and an explicit restriction the candidate dropped. It does not
   understand arbitrary English; it enforces closed-world consistency.

2. ``SemanticReconciler`` compares the canonical meaning of Candidate A (extractor) and
   Candidate B (reviewer). Non-material differences (evidence offsets, ordering,
   synonym-normalized enums, equivalent wording) reconcile automatically. Material
   differences are never resolved by code or a third model: they become a Clarification,
   and the user is the semantic authority.
"""

from dataclasses import dataclass

from app.models.contracts import (DEFAULT_EVENT_POPULATION, DEFAULT_GAME_TYPES,
                                  DEFAULT_RANKING_LIMIT, Constraint, CountConstraint,
                                  LocationConstraint, NumericConstraint,
                                  PitchTypeConstraint, PopulationConstraint,
                                  QualificationConstraint, RankingConstraint)
from app.models.semantic_candidate import SemanticCandidate
from app.models.semantic_review import (AgreementStatus, MaterialDifference,
                                        SemanticReviewResult)
from app.semantic.lexical_anchors import LexicalAnchors

# Material disagreement codes that can be repaired by re-running the deterministic
# high-confidence path (recoverable) versus ones that must clarify immediately.
RECOVERABLE_ANCHOR_CODES = frozenset({
    "ANCHOR_CONFLICT", "ANCHOR_MISSING", "UNANCHORED_EXPLICIT",
})
NON_RECOVERABLE_ANCHOR_CODES = frozenset({"AMBIGUOUS_RANKING", "AMBIGUOUS_QUERY"})


@dataclass(frozen=True)
class AnchorDifference:
    dimension: str
    code: str
    detail: str

    @property
    def recoverable(self) -> bool:
        return self.code in RECOVERABLE_ANCHOR_CODES


def _states(constraint: CountConstraint) -> tuple[tuple[int, int], ...]:
    return constraint.exact_states


def _round(value: float) -> float:
    return round(float(value), 6)


def reconcile_with_anchors(constraints: tuple[Constraint, ...],
                           anchors: LexicalAnchors) -> tuple[AnchorDifference, ...]:
    """Return structured anchor differences. Empty means the candidate is consistent."""
    differences: list[AnchorDifference] = []
    seen: set[tuple[str, str]] = set()

    def add(dimension: str, code: str, detail: str) -> None:
        key = (dimension, code)
        if key in seen:
            return
        seen.add(key)
        differences.append(AnchorDifference(dimension=dimension, code=code, detail=detail))

    # -- numeric thresholds -------------------------------------------------
    candidate_numerics = [item for item in constraints if isinstance(item, NumericConstraint)]
    anchor_numerics = anchors.numerics()
    for anchor in anchor_numerics:
        matches = [item for item in candidate_numerics if item.key == anchor.key]
        if not matches:
            add(f"numeric:{anchor.key}", "ANCHOR_MISSING",
                f"the query states {anchor.key} {anchor.operator} {anchor.value:g} "
                "but the candidate omits it")
        elif not any(_round(item.value) == _round(anchor.value) and item.operator == anchor.operator
                     for item in matches):
            add(f"numeric:{anchor.key}", "ANCHOR_CONFLICT",
                f"the query states {anchor.key} {anchor.operator} {anchor.value:g}, "
                "which contradicts the candidate")
    for item in candidate_numerics:
        if not any(other.key == item.key and _round(other.value) == _round(item.value)
                   and other.operator == item.operator for other in anchor_numerics):
            add(f"numeric:{item.key}", "UNANCHORED_EXPLICIT",
                f"the candidate asserts {item.key} {item.operator} {item.value:g} "
                "but the query does not state it")

    # -- qualification ------------------------------------------------------
    candidate_quals = [item for item in constraints if isinstance(item, QualificationConstraint)]
    phrases = anchors.qualification_phrases
    if phrases:
        if not candidate_quals:
            add("qualification", "ANCHOR_MISSING",
                "the query states a qualification but the candidate omits it")
        for phrase in phrases:
            if phrase.value is not None and not any(
                    item.min_batted_balls == phrase.value for item in candidate_quals):
                add("qualification", "ANCHOR_CONFLICT",
                    f"the query states qualification {phrase.value} but the candidate "
                    "proposes a different value")
    elif candidate_quals:
        add("qualification", "UNANCHORED_EXPLICIT",
            "the candidate asserts a qualification the query does not state")

    # -- counts -------------------------------------------------------------
    candidate_counts = [item for item in constraints if isinstance(item, CountConstraint)]
    anchor_counts = anchors.counts()
    if anchor_counts:
        anchor_states = {_states(item) for item in anchor_counts}
        if not candidate_counts:
            add("count", "ANCHOR_MISSING", "the query states a count but the candidate omits it")
        elif not any(_states(item) in anchor_states for item in candidate_counts):
            add("count", "ANCHOR_CONFLICT",
                "the query states an exact count but the candidate proposes different states")
    elif candidate_counts:
        add("count", "UNANCHORED_EXPLICIT", "the candidate asserts a count the query does not state")

    # -- ranking ------------------------------------------------------------
    candidate_rankings = [item for item in constraints if isinstance(item, RankingConstraint)]
    explicit_limits = {item.limit for item in anchors.ranking_limits if item.explicit}
    if len(explicit_limits) > 1:
        add("ranking", "AMBIGUOUS_RANKING",
            f"the query states more than one ranking limit: {sorted(explicit_limits)}")
    anchor_rankings = anchors.rankings()
    if anchor_rankings:
        if not candidate_rankings:
            add("ranking", "ANCHOR_MISSING", "the query states a ranking but the candidate omits it")
        else:
            anchor = anchor_rankings[0]
            for item in candidate_rankings:
                if item.metric_key != anchor.metric_key:
                    add("ranking", "ANCHOR_CONFLICT",
                        f"the query ranks by {anchor.metric_key} but the candidate ranks by "
                        f"{item.metric_key}")
                elif item.aggregation != anchor.aggregation:
                    add("ranking", "ANCHOR_CONFLICT",
                        f"the query ranks by {anchor.aggregation} but the candidate uses "
                        f"{item.aggregation}")
                elif item.direction != anchor.direction:
                    add("ranking", "ANCHOR_CONFLICT",
                        "the query ranking direction contradicts the candidate")
                elif explicit_limits and item.limit not in explicit_limits:
                    add("ranking", "ANCHOR_CONFLICT",
                        f"the query ranking limit {sorted(explicit_limits)} contradicts "
                        f"the candidate limit {item.limit}")
    else:
        for _ in candidate_rankings:
            add("ranking", "UNANCHORED_EXPLICIT",
                "the candidate asserts a ranking the query does not state")

    # -- population ---------------------------------------------------------
    candidate_pops = [item for item in constraints if isinstance(item, PopulationConstraint)]
    population = anchors.population
    if population is not None:
        game_explicit = (population.game_types is not None
                         and tuple(population.game_types) != tuple(DEFAULT_GAME_TYPES))
        event_explicit = (population.event_population is not None
                          and population.event_population != DEFAULT_EVENT_POPULATION)
        if candidate_pops:
            if population.game_types is not None and not any(
                    tuple(item.game_types) == tuple(population.game_types)
                    for item in candidate_pops):
                add("population", "ANCHOR_CONFLICT",
                    f"the query population {list(population.game_types)} is not the candidate's")
            if population.event_population is not None and not any(
                    item.event_population == population.event_population for item in candidate_pops):
                add("population", "ANCHOR_CONFLICT",
                    f"the query event population {population.event_population} is not the "
                    "candidate's")
        elif game_explicit or event_explicit:
            add("population", "ANCHOR_MISSING",
                "the query states a non-default population but the candidate omits it")
    else:
        default = (tuple(DEFAULT_GAME_TYPES), DEFAULT_EVENT_POPULATION)
        for item in candidate_pops:
            if item.origin in ("USER_EXPLICIT", "USER_CONFIRMED") and \
                    (tuple(item.game_types), item.event_population) != default:
                add("population", "UNANCHORED_EXPLICIT",
                    "the candidate asserts a population the query does not state")
                break

    # -- pitch family -------------------------------------------------------
    anchor_families = set(anchors.pitch_families())
    candidate_families = {item.family for item in constraints if isinstance(item, PitchTypeConstraint)}
    if anchor_families and candidate_families and not (anchor_families & candidate_families):
        add("pitch_type", "ANCHOR_CONFLICT",
            f"the query pitch family {sorted(anchor_families)} contradicts the candidate "
            f"{sorted(candidate_families)}")

    # -- explicit location --------------------------------------------------
    anchor_locations = set(anchors.locations())
    candidate_locations = {item.definition for item in constraints if isinstance(item, LocationConstraint)}
    if anchor_locations:
        if not candidate_locations:
            add("location", "ANCHOR_MISSING", "the query states a location but the candidate omits it")
        elif not (anchor_locations & candidate_locations):
            add("location", "ANCHOR_CONFLICT",
                f"the query location {sorted(anchor_locations)} contradicts the candidate "
                f"{sorted(candidate_locations)}")

    return tuple(differences)


# -- Candidate A vs Candidate B ---------------------------------------------


def _count_states(constraint) -> tuple[tuple[int, int], ...]:
    if constraint.states:
        return tuple(sorted((state.balls, state.strikes) for state in constraint.states))
    strikes = constraint.strikes if constraint.strikes is not None else 0
    return tuple(sorted((ball, strikes) for ball in (constraint.balls or (0, 1, 2, 3))))


def _fingerprint(candidate: SemanticCandidate) -> dict[tuple, tuple[tuple, ...]]:
    groups: dict[tuple, list[tuple]] = {}
    for item in candidate.constraints:
        is_default = item.origin in ("SYSTEM_INFERRED", "SYSTEM_DEFAULT")
        if item.kind == "NUMERIC":
            key = ("NUMERIC", item.metric)
            value = (item.operator, _round(item.value or 0.0), item.unit or "mph")
        elif item.kind == "PITCH_TYPE":
            key, value = ("PITCH_TYPE",), (item.family,)
        elif item.kind == "LOCATION":
            key, value = ("LOCATION",), (item.definition,)
        elif item.kind == "COUNT":
            key, value = ("COUNT",), _count_states(item)
        elif item.kind == "QUALIFICATION":
            key, value = ("QUALIFICATION",), (item.min_batted_balls,)
        elif item.kind == "POPULATION":
            key = ("POPULATION",)
            game_types = tuple(item.game_types) or tuple(DEFAULT_GAME_TYPES)
            value = (game_types, item.event_population or DEFAULT_EVENT_POPULATION)
            # The documented default population is not a material statement even when a
            # model labels it USER_EXPLICIT; omitting it must not trigger a clarification.
            is_default = is_default or value == (tuple(DEFAULT_GAME_TYPES), DEFAULT_EVENT_POPULATION)
        elif item.kind == "RANKING":
            key = ("RANKING",)
            value = (item.metric_key, item.aggregation or "AVG", item.direction or "DESC",
                     item.limit or DEFAULT_RANKING_LIMIT)
        else:  # pragma: no cover - closed schema
            continue
        groups.setdefault(key, []).append((value, is_default))
    return {key: tuple(sorted(values, key=repr)) for key, values in groups.items()}


def _values(entries: tuple[tuple, ...]) -> tuple:
    return tuple(value for value, _ in entries)


class SemanticReconciler:
    """Compares two independent candidates and decides whether execution is safe."""

    def compare(self, extractor: SemanticCandidate, reviewer: SemanticCandidate,
                anchors: LexicalAnchors) -> SemanticReviewResult:
        a = _fingerprint(extractor)
        b = _fingerprint(reviewer)
        differences: list[MaterialDifference] = []
        for key in sorted(set(a) | set(b), key=repr):
            entries_a = a.get(key, ())
            entries_b = b.get(key, ())
            values_a, values_b = _values(entries_a), _values(entries_b)
            if values_a == values_b:
                continue
            if not values_a and all(default for _, default in entries_b):
                continue
            if not values_b and all(default for _, default in entries_a):
                continue
            differences.append(MaterialDifference(
                dimension=":".join(str(part) for part in key), code="MATERIAL_DIFFERENCE",
                detail=f"extractor={values_a!r} reviewer={values_b!r}"))

        ambiguities = []
        if anchors.location_ambiguity:
            ambiguities.append("location.upper_edge")
        for candidate in (extractor, reviewer):
            for ambiguity in candidate.ambiguities:
                if ambiguity.kind not in ambiguities:
                    ambiguities.append(ambiguity.kind)
        # Only the ambiguity kinds product/domain policy marks as material block
        # execution. Other model-reported ambiguities (for example a spurious
        # 'temporal.year') are informational and deterministic code already owns
        # dates, so they must not force a clarification.
        material_ambiguities = [kind for kind in ambiguities if kind.startswith("location")]

        if differences and not (
                material_ambiguities
                and all(item.dimension.startswith("LOCATION") for item in differences)):
            status: AgreementStatus = "MATERIAL_DISAGREEMENT"
        elif material_ambiguities:
            status = "AMBIGUOUS"
        else:
            status = "AGREE"
        return SemanticReviewResult(
            agreement_status=status,
            extractor_candidate=extractor,
            reviewer_candidate=reviewer,
            canonical_candidate=extractor if status == "AGREE" else None,
            material_differences=tuple(differences),
            ambiguities=tuple(ambiguities),
        )
