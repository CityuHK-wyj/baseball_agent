"""Constrained semantic candidate: the only meaning an extractor may propose.

Natural language is turned into a *candidate* of closed, typed fields. The candidate is
not executable semantics: it never names a physical column, never writes SQL, never
selects a source and never decides policy. The deterministic ``SemanticValidator``
either converts a candidate into the canonical typed domain constraints or rejects it /
routes it to clarification.

Every proposed constraint carries provenance back to the user's query (an evidence
string and, when available, source offsets). That is what makes numeric cross-binding,
contradiction detection and auditability possible.
"""

from typing import Literal

from pydantic import Field

from app.models.contracts import Contract, CountState, Name
from app.models.understanding import MAX_FREE_TEXT

Operator = Literal["EQ", "GT", "GTE", "LT", "LTE"]
Aggregation = Literal["AVG", "MAX", "MIN", "SUM"]
Direction = Literal["ASC", "DESC"]
CandidateKind = Literal[
    "NUMERIC", "PITCH_TYPE", "LOCATION", "COUNT", "QUALIFICATION", "POPULATION", "RANKING"]
GameType = Literal["REGULAR_SEASON", "POSTSEASON", "SPRING_TRAINING", "EXHIBITION"]
EventPopulation = Literal["BATTED_BALL", "MEASURED_CONTACT", "ALL_PITCHES"]
ConstraintOrigin = Literal[
    "USER_EXPLICIT", "USER_CONFIRMED", "CONTEXT_INFERRED", "SYSTEM_INFERRED", "SYSTEM_DEFAULT"]


class EvidenceSpan(Contract):
    """The query text a proposal is grounded in, with optional source offsets."""

    text: str = ""
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)


class CandidateConstraint(Contract):
    """One proposed semantic constraint of a closed kind, with provenance.

    Only the fields relevant to ``kind`` are meaningful; the validator rejects a
    candidate whose kind is missing its required fields rather than guessing.
    """

    kind: CandidateKind
    evidence: EvidenceSpan = EvidenceSpan()
    origin: ConstraintOrigin = "USER_EXPLICIT"

    # NUMERIC
    metric: Name | None = None
    operator: Operator | None = None
    value: float | None = None
    unit: Name | None = None

    # PITCH_TYPE / LOCATION
    family: Name | None = None
    definition: Name | None = None

    # COUNT (exact states are authoritative; uniform strikes is the compact form)
    states: tuple[CountState, ...] = ()
    strikes: int | None = Field(default=None, ge=0, le=2)
    balls: tuple[int, ...] = ()

    # QUALIFICATION
    min_batted_balls: int | None = Field(default=None, gt=0)

    # POPULATION
    game_types: tuple[GameType, ...] = ()
    event_population: EventPopulation | None = None

    # RANKING
    metric_key: Name | None = None
    aggregation: Aggregation | None = None
    direction: Direction | None = None
    limit: int | None = Field(default=None, ge=1)


class SemanticAmbiguity(Contract):
    """A material ambiguity the extractor refuses to resolve on the user's behalf."""

    kind: Name
    evidence: EvidenceSpan = EvidenceSpan()
    question: str = ""
    candidates: tuple[Name, ...] = ()


class SemanticCandidate(Contract):
    """Extractor output.

    The *typed* constraint surface stays closed (extras are forbidden so a model cannot
    smuggle executable fields). Alongside it, the v0.2 open-world runtime allows free-form
    interpretation: an explicit user goal, a semantic brief, planner guidance, an
    analysis strategy, unresolved concepts, entity mentions and search hints. These are
    interpretation aids, never executable semantics and never physical identifiers.
    """

    constraints: tuple[CandidateConstraint, ...] = ()
    ambiguities: tuple[SemanticAmbiguity, ...] = ()
    extractor: str = "deterministic"
    model: str = ""

    # -- open-world interpretation (free-form) ------------------------------
    user_goal: str = Field(default="", max_length=MAX_FREE_TEXT)
    semantic_brief: str = Field(default="", max_length=MAX_FREE_TEXT)
    planner_notes: str = Field(default="", max_length=MAX_FREE_TEXT)
    analysis_strategy: str = Field(default="", max_length=MAX_FREE_TEXT)
    entity_mentions: tuple[str, ...] = ()
    unresolved_concepts: tuple[str, ...] = ()
    search_hints: tuple[str, ...] = ()
    interpretations: tuple[str, ...] = ()


class SemanticProvenance(Contract):
    """Recorded origin of one canonical constraint, analogous to artifact provenance."""

    kind: Name
    key: Name
    evidence_text: str = ""
    evidence_start: int | None = None
    evidence_end: int | None = None
    origin: ConstraintOrigin = "USER_EXPLICIT"
