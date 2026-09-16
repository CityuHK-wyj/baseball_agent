"""Validated domain definitions and separate runtime projections."""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator


Name = Annotated[str, Field(min_length=1, pattern=r"\S")]

# Where a constraint came from. USER_EXPLICIT is stated by the user; USER_CONFIRMED is
# accepted from a clarification; CONTEXT_INFERRED/SYSTEM_INFERRED are deduced;
# SYSTEM_DEFAULT is a built-in fallback.
ConstraintOrigin = Literal[
    "USER_EXPLICIT", "USER_CONFIRMED", "CONTEXT_INFERRED", "SYSTEM_INFERRED", "SYSTEM_DEFAULT"]

# How strongly a constraint binds. Precedence: SYSTEM_POLICY > USER_CONSTRAINT >
# USER_PREFERENCE > INFERRED_DEFAULT. A user preference may be revised with consent;
# a system policy may never be overridden by the user.
ConstraintAuthority = Literal["SYSTEM_POLICY", "USER_CONSTRAINT", "USER_PREFERENCE", "INFERRED_DEFAULT"]

AUTHORITY_PRECEDENCE: dict[str, int] = {
    "SYSTEM_POLICY": 3, "USER_CONSTRAINT": 2, "USER_PREFERENCE": 1, "INFERRED_DEFAULT": 0}

_ORIGIN_AUTHORITY: dict[str, str] = {
    "USER_EXPLICIT": "USER_CONSTRAINT",
    "USER_CONFIRMED": "USER_CONSTRAINT",
    "CONTEXT_INFERRED": "INFERRED_DEFAULT",
    "SYSTEM_INFERRED": "INFERRED_DEFAULT",
    "SYSTEM_DEFAULT": "INFERRED_DEFAULT",
}


def default_authority(origin: str) -> str:
    return _ORIGIN_AUTHORITY.get(origin, "USER_CONSTRAINT")


class Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)


class Entity(Contract):
    namespace: Name
    entity_type: Literal["PLAYER", "TEAM", "LEAGUE"]
    identifier: Name


class TimeRange(Contract):
    start: date
    end: date

    @model_validator(mode="after")
    def ordered(self):
        if self.end < self.start:
            raise ValueError("Time range ends before it starts")
        return self


class _Constraint(Contract):
    @model_validator(mode="before")
    @classmethod
    def _fill_authority(cls, data):
        if isinstance(data, dict):
            values = dict(data)
            if not values.get("authority"):
                values["authority"] = default_authority(values.get("origin", "USER_CONFIRMED"))
            return values
        return data


class NumericConstraint(_Constraint):
    kind: Literal["NUMERIC"] = "NUMERIC"
    key: Name
    operator: Literal["EQ", "GT", "GTE", "LT", "LTE"]
    value: FiniteFloat
    unit: Name
    origin: ConstraintOrigin = "USER_CONFIRMED"
    authority: ConstraintAuthority = "USER_CONSTRAINT"


class CategoryConstraint(_Constraint):
    kind: Literal["CATEGORY"] = "CATEGORY"
    key: Name
    values: tuple[Name, ...] = Field(min_length=1)
    origin: ConstraintOrigin = "USER_CONFIRMED"
    authority: ConstraintAuthority = "USER_CONSTRAINT"


class CountConstraint(_Constraint):
    """A typed two-strike (or other count) situation.

    ``strikes`` is the required strike count and ``balls`` is the set of allowed ball
    counts. The target "after reaching two strikes" is represented as ``strikes=2``,
    never as an opaque "two strikes" string once it reaches execution planning.
    """

    kind: Literal["COUNT"] = "COUNT"
    key: Name = "count"
    strikes: int = Field(ge=0, le=2)
    balls: tuple[int, ...] = Field(default=(0, 1, 2, 3))
    origin: ConstraintOrigin = "USER_CONFIRMED"
    authority: ConstraintAuthority = "USER_CONSTRAINT"

    @model_validator(mode="after")
    def balls_in_range(self):
        if any(ball < 0 or ball > 3 for ball in self.balls):
            raise ValueError("Ball counts must be between 0 and 3")
        return self


class PitchTypeConstraint(_Constraint):
    """A typed pitch-family constraint.

    ``family`` is the semantic family (for example "fastball"). The provider-specific
    pitch-code set is resolved by the semantic/source mapping layer, never silently
    assumed here.
    """

    kind: Literal["PITCH_TYPE"] = "PITCH_TYPE"
    key: Name = "pitch_type"
    family: Name
    origin: ConstraintOrigin = "USER_CONFIRMED"
    authority: ConstraintAuthority = "USER_CONSTRAINT"


class LocationConstraint(_Constraint):
    """A typed pitch-location constraint.

    ``definition`` names an explicit semantic definition (for example
    ``BATTER_RELATIVE_UPPER_EDGE`` or ``ZONE_UPPER_THIRD``). The physical fields needed
    for each definition live in the SchemaRegistry / SourceMapping layer; the Planner
    never hard-codes physical columns and never silently redefines the user's request.
    """

    kind: Literal["LOCATION"] = "LOCATION"
    key: Name = "pitch_location"
    definition: Name
    origin: ConstraintOrigin = "USER_CONFIRMED"
    authority: ConstraintAuthority = "USER_CONSTRAINT"


class RankingConstraint(_Constraint):
    """Explicit ranking intent: metric, aggregation, direction and limit.

    ``aggregation`` is explicit so the SQL builder never silently decides whether a
    leaderboard ranks by average or maximum exit velocity. Qualification/minimum sample
    is a separate concern (``ArtifactRequirement.qualification_rule`` /
    ``sample_adequacy_rule``), never folded into the metric.
    """

    kind: Literal["RANKING"] = "RANKING"
    key: Name = "ranking"
    metric_key: Name
    aggregation: Literal["AVG", "MAX", "MIN", "SUM"] = "AVG"
    direction: Literal["ASC", "DESC"] = "DESC"
    limit: int = Field(ge=1)
    origin: ConstraintOrigin = "USER_CONFIRMED"
    authority: ConstraintAuthority = "USER_CONSTRAINT"


GameType = Literal["REGULAR_SEASON", "POSTSEASON", "SPRING_TRAINING", "EXHIBITION"]
EventPopulation = Literal["BATTED_BALL", "MEASURED_CONTACT", "ALL_PITCHES"]

# The explicit v0.1 analytical population contract. The parser emits this constraint
# for every analytical query (explicit or defaulted) so the analyzed population is
# always visible on the objective, requirement and artifact instead of implicit in SQL.
DEFAULT_GAME_TYPES: tuple[str, ...] = ("REGULAR_SEASON",)
DEFAULT_EVENT_POPULATION: str = "BATTED_BALL"
# A documented ranking qualification when the user does not request one. The semantic
# layer owns this default so the physical adapter never substitutes a hidden value.
DEFAULT_MIN_BATTED_BALLS = 3


class PopulationConstraint(_Constraint):
    """Explicit analytical population: game types and event grain.

    ``game_types`` distinguishes regular season, postseason, Spring Training and
    exhibition games; ``event_population`` distinguishes fair batted-ball events from
    measured contact (which may include fouls) and from all qualifying pitches. The
    physical adapter must apply both, and must fail closed when a source cannot.
    """

    kind: Literal["POPULATION"] = "POPULATION"
    key: Name = "population"
    game_types: tuple[GameType, ...] = Field(default=DEFAULT_GAME_TYPES, min_length=1)
    event_population: EventPopulation = DEFAULT_EVENT_POPULATION
    origin: ConstraintOrigin = "SYSTEM_INFERRED"
    authority: ConstraintAuthority = "INFERRED_DEFAULT"


class QualificationConstraint(_Constraint):
    """Explicit user qualification/sample threshold, before it is frozen on a Requirement.

    This is eligibility for the ranking population, not metric aggregation and not
    sample adequacy. The Requirement Decomposer freezes it into ``QualificationRule``;
    the physical adapter reads that frozen rule and never substitutes its own default
    when the user supplied a value.
    """

    kind: Literal["QUALIFICATION"] = "QUALIFICATION"
    key: Name = "qualification"
    min_batted_balls: int | None = Field(default=None, ge=0)
    min_pitches: int | None = Field(default=None, ge=0)
    origin: ConstraintOrigin = "USER_EXPLICIT"
    authority: ConstraintAuthority = "USER_CONSTRAINT"


Constraint = Annotated[
    NumericConstraint | CategoryConstraint | CountConstraint | PitchTypeConstraint |
    LocationConstraint | RankingConstraint | PopulationConstraint |
    QualificationConstraint,
    Field(discriminator="kind")]


class ArtifactDescriptor(Contract):
    artifact_type: Literal["TABLE", "EVIDENCE", "FEATURE"]
    entities: tuple[Entity, ...] = ()
    data_keys: tuple[Name, ...] = Field(min_length=1)
    optional_data_keys: tuple[Name, ...] = ()
    time_range: TimeRange | None = None
    constraints: tuple[Constraint, ...] = ()
    granularity: Name
    population_scope: Name


class AnalysisObjective(Contract):
    objective_id: Name
    raw_query: Name
    description: Name
    objective_type: Literal["PERFORMANCE", "INJURY", "VALUE", "STRATEGY", "CONTEXT"] = "PERFORMANCE"
    subtype: str = ""
    entities: tuple[Entity, ...] = ()
    constraints: tuple[Constraint, ...] = ()
    base_priority: Literal["LOW", "MEDIUM", "HIGH"] = "MEDIUM"


class QualificationRule(Contract):
    """Eligibility for a population or ranking. Distinct from sample adequacy."""

    kind: Literal["ALL_PLAYERS", "MLB_QUALIFIED", "CUSTOM"] = "ALL_PLAYERS"
    min_plate_appearances: int | None = Field(default=None, ge=0)
    min_batters_faced: int | None = Field(default=None, ge=0)
    min_batted_balls: int | None = Field(default=None, ge=0)
    min_pitches: int | None = Field(default=None, ge=0)
    custom_expression: str = ""


class SampleAdequacyRule(Contract):
    """Whether the available sample can support the conclusion being drawn."""

    min_sample: int | None = Field(default=None, ge=0)
    sample_unit: Literal["PITCH", "BATTED_BALL", "PLATE_APPEARANCE", "GAME", "SEASON"] = "BATTED_BALL"
    note: str = ""


class ArtifactRequirement(Contract):
    requirement_id: Name
    objective_ref: Name
    description: Name
    descriptor: ArtifactDescriptor
    origin: Literal["INITIAL", "PLANNER_ADDED"] = "INITIAL"
    base_criticality: Literal["CORE", "OPTIONAL"] = "CORE"
    parent_ref: Name | None = None
    # Contextual framing, not a property of any Artifact: the Judge uses it to
    # reinterpret soft signals for this requirement.
    evidence_purpose: Literal["EXISTENCE", "DESCRIPTIVE", "INFERENTIAL"] = "DESCRIPTIVE"
    min_row_count: int | None = Field(default=None, ge=0)
    qualification_rule: QualificationRule | None = None
    sample_adequacy_rule: SampleAdequacyRule | None = None


class RequirementState(Contract):
    requirement_ref: Name
    status: Literal["PENDING", "PARTIAL", "SATISFIED", "UNSATISFIED"] = "PENDING"
    assessment_refs: tuple[Name, ...] = ()
    artifact_refs: tuple[Name, ...] = ()
    unresolved_data_keys: tuple[Name, ...] = ()
    optional_gaps: tuple[Name, ...] = ()
    limitation_refs: tuple[Name, ...] = ()
    blocking_refs: tuple[Name, ...] = ()
    recoverable: bool = True
    version: int = Field(default=0, ge=0)


class ObjectiveState(Contract):
    objective_ref: Name
    status: Literal["PENDING", "IN_PROGRESS", "COMPLETE", "LIMITED", "FAILED"] = "PENDING"
    requirement_refs: tuple[Name, ...]
    limitations: tuple[Name, ...] = ()
    optional_gaps: tuple[Name, ...] = ()
    replan_count: int = Field(default=0, ge=0)
    version: int = Field(default=0, ge=0)


class LeagueStateSnapshot(Contract):
    """Authoritative season progress, independent of local ingestion coverage."""

    season: int
    as_of: date
    games_played: int = Field(ge=0)
    games_scheduled: int = Field(gt=0)
    local_coverage_end: date | None = None
    source: Name = "official"

    @model_validator(mode="after")
    def played_not_above_scheduled(self):
        if self.games_played > self.games_scheduled:
            raise ValueError("games_played cannot exceed games_scheduled")
        return self

    @property
    def official_progress(self) -> float:
        return self.games_played / self.games_scheduled

    @property
    def local_lag_days(self) -> int | None:
        if self.local_coverage_end is None:
            return None
        return max((self.as_of - self.local_coverage_end).days, 0)

    @property
    def local_behind_official(self) -> bool:
        if self.local_coverage_end is None:
            return True
        return self.local_coverage_end < self.as_of
