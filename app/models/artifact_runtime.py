"""Core information-transfer contracts for the artifact runtime.

Architectural principle: **flexible cognition, composable evidence, deterministic
actions.**

Every cross-component message is a stable *envelope* (fixed fields) carrying a
flexible *payload* (structured values plus bounded free-form text), explicit
*references* back to the original information, and *provenance*. Nothing here is
executable: free-form text is never compiled into an action. Actions converge only
through the strict deterministic boundary (see ``app.runtime.ir_compiler`` and
``app.validation.sql_guard``).

The models are deliberately permissive about *semantic content* (``objective`` and
``text`` are free-form) and strict about *transfer structure* (ids, references,
status, scope). That is what lets new, unseen questions flow through the same runtime
without phrase-specific production branches.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.contracts import TimeRange


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _Envelope(BaseModel):
    """Immutable base for cross-component transfer objects."""

    model_config = ConfigDict(frozen=True, extra="forbid")


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------

# Stable reference kinds. A reference points *back* to original information instead
# of repeating a lossy summary. The set is intentionally explicit so a planner can
# rely on the transfer structure, while the target may be any free-form object.
RefType = Literal[
    "USER_MESSAGE",
    "USER_SPAN",
    "CONVERSATION_MESSAGE",
    "ARTIFACT",
    "ARTIFACT_FIELD",
    "ARTIFACT_ROW_SET",
    "ARTIFACT_EXPORT",
    "WEB_EVIDENCE_SPAN",
    "KNOWLEDGE_ENTRY",
    "CANDIDATE_KNOWLEDGE",
    "SQL_RESULT",
    "DERIVED_COMPUTATION",
    "PLANNER_ASSUMPTION",
    "CLARIFICATION_ANSWER",
    "TOOL_REQUEST",
    "TOOL_RESULT",
    "TOOL_ATTEMPT",
    "CLAIM",
    "GOAL",
    "NEED",
    "OBLIGATION",
    "SCOPE_VERIFICATION",
    "EXECUTION_RECEIPT",
]


class Reference(_Envelope):
    """A stable pointer to original information.

    ``target_id`` identifies the target object; ``selector`` narrows it (a field name,
    a row set, a character span) without copying the payload.
    """

    ref_id: str
    ref_type: RefType
    target_id: str
    selector: str = ""
    provenance: str = ""
    label: str = ""


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------

PopulationKind = str  # "players" / "teams" / "league" / "game" / free-form


class Scope(_Envelope):
    """What a Need requests or an Artifact actually covers.

    ``requested_scope`` and ``actual_scope`` are distinct objects. An artifact whose
    actual scope differs from the requested scope can still be useful, but it must not
    silently satisfy the Need: the difference is a *coverage gap* surfaced to the
    planner and judge.
    """

    entities: tuple[str, ...] = ()
    canonical_entities: tuple[str, ...] = ()
    population: PopulationKind = ""
    membership_basis: str = ""  # official_roster / active_roster / observed_participants
    time_range: TimeRange | None = None
    seasons: tuple[int, ...] = ()
    game_types: tuple[str, ...] = ()
    metric: str = ""
    measurement: str = ""
    unit: str = ""
    aggregation: str = ""
    qualification: str = ""
    event_population: str = ""
    source_coverage: tuple[str, ...] = ()
    as_of: str = ""
    note: str = ""

    def temporal_key(self) -> tuple[str, str] | None:
        if self.time_range is None:
            return None
        return (self.time_range.start.isoformat(), self.time_range.end.isoformat())


# ---------------------------------------------------------------------------
# Artifacts and exports
# ---------------------------------------------------------------------------

# Canonical, reusable export types. This is a *documented* vocabulary, not a closed
# enum: a tool may emit another general export type. Planners compose on the string
# capability name, so unknown exports remain usable without a code change.
KNOWN_EXPORT_TYPES: frozenset[str] = frozenset({
    "PLAYER_ID_SET",
    "TEAM_ROSTER",
    "TEAM_ID",
    "DATE_RANGE",
    "ENTITY_MAPPING",
    "STATISTICAL_RESULT",
    "RANKED_ENTITY_SET",
    "WEB_EVIDENCE",
    "TRANSACTION_DATE",
    "EVENT_SET",
    "DERIVED_MEASURE",
    "KNOWLEDGE_CANDIDATE",
    "SEARCH_QUERY",
    "ANOMALY_SIGNAL",
})

ArtifactStatus = Literal["OK", "EMPTY", "PARTIAL", "REJECTED", "INVALID"]


class EvidenceSource(_Envelope):
    """Provenance of one piece of evidence, including web grounding metadata."""

    source: str = ""
    source_kind: str = ""
    reference: str = ""
    title: str = ""
    url: str = ""
    retrieved_at: datetime = Field(default_factory=utcnow)
    published_at: datetime | None = None
    authority: str = ""


# Scope dimensions are explicit so verification can be contextual and per-dimension
# instead of a single opaque score. Unknown stays UNKNOWN; it never becomes coverage.
ScopeDimension = Literal[
    "entity", "population", "membership", "time", "season", "game_type",
    "event_population", "measure", "aggregation", "qualification", "source_coverage",
]
ScopeVerificationStatus = Literal["VERIFIED", "PARTIAL", "MISMATCH", "UNKNOWN"]


class ScopeVerification(_Envelope):
    """Deterministic, dimension-level proof of what evidence actually establishes.

    This is deliberately *separate* from a Tool's declared scope. A Tool can declare any
    scope it likes; only a verifier observing execution/provider evidence can raise a
    dimension to ``VERIFIED``. ``UNKNOWN`` is never treated as full coverage.
    """

    dimension: ScopeDimension
    status: ScopeVerificationStatus
    requested: str = ""
    observed: str = ""
    evidence_refs: tuple[str, ...] = ()
    verifier: str = ""
    source_snapshot: str = ""
    limitations: tuple[str, ...] = ()
    verified_at: datetime = Field(default_factory=utcnow)

    @property
    def hard_mismatch(self) -> bool:
        return self.status == "MISMATCH"


class ExportContract(_Envelope):
    """Typed/versioned contract for one Artifact export.

    ``ArtifactExport.value: Any`` alone is not a semantic contract. The contract records
    schema/version, entity namespace, role, grain, unit, cardinality and column schema so a
    downstream consumer can validate compatibility instead of guessing by convention.
    """

    schema_name: str = ""
    version: int = 1
    entity_namespace: str = ""  # MLBAM / TEAM_ID / NONE ...
    role: str = ""  # e.g. PLAYER_ID_SET / TABLE / SCALAR / EVIDENCE
    grain: str = ""
    unit: str = ""
    cardinality: str = "UNKNOWN"  # SCALAR / SET / TABLE
    column_schema: tuple[tuple[str, str], ...] = ()
    scope: Scope | None = None
    required: bool = False


class ArtifactExport(_Envelope):
    """A reusable machine-consumable output of an Artifact.

    ``value`` carries the structured payload (a list of ids, a mapping, a number).
    ``text`` carries bounded free-form content. ``references`` point back to the
    evidence that produced it. ``contract`` is the typed/versioned compatibility promise.
    """

    export_id: str
    export_type: str
    value: Any = None
    text: str = ""
    provenance: str = ""
    references: tuple[str, ...] = ()
    confidence: float = 0.5
    metadata: dict[str, Any] = Field(default_factory=dict)
    contract: ExportContract | None = None
    derived_from: tuple[str, ...] = ()  # exact upstream export ids, not just artifact ids
    accepted: bool = True


class RuntimeArtifact(_Envelope):
    """One produced result that other tools can consume.

    Artifacts carry their own ``requested_scope``/``actual_scope`` so scope mismatch is
    visible to the planner and judge, plus ``exports`` so any useful output can become
    another tool's input without a query-specific path.
    """

    artifact_id: str
    kind: str
    structured_data: dict[str, Any] = Field(default_factory=dict)
    text_content: str = ""
    exports: tuple[ArtifactExport, ...] = ()
    provenance: tuple[EvidenceSource, ...] = ()
    references: tuple[str, ...] = ()
    requested_scope: Scope | None = None
    actual_scope: Scope | None = None  # the Tool's DECLARED scope (never trusted alone)
    scope_verifications: tuple[ScopeVerification, ...] = ()
    lineage: tuple[str, ...] = ()
    confidence: float = 0.5
    status: ArtifactStatus = "OK"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)

    @property
    def declared_scope(self) -> Scope | None:
        """What the producing Tool/provider claims it returned."""
        return self.actual_scope

    def verification(self, dimension: str) -> ScopeVerification | None:
        for item in self.scope_verifications:
            if item.dimension == dimension:
                return item
        return None

    def dimension_status(self, dimension: str) -> str:
        item = self.verification(dimension)
        return item.status if item is not None else "UNKNOWN"

    def has_hard_mismatch(self) -> bool:
        return any(item.hard_mismatch for item in self.scope_verifications)

    def export(self, export_type: str) -> ArtifactExport | None:
        for item in self.exports:
            if item.export_type == export_type:
                return item
        return None

    def exports_of(self, *export_types: str) -> tuple[ArtifactExport, ...]:
        wanted = set(export_types)
        return tuple(item for item in self.exports if item.export_type in wanted)


# ---------------------------------------------------------------------------
# Goal / Need
# ---------------------------------------------------------------------------

NeedStatus = Literal[
    "OPEN", "IN_PROGRESS", "SATISFIED", "PARTIAL", "BLOCKED", "FAILED"]
Criticality = Literal["CORE", "OPTIONAL"]

# Structured, durable taxonomy for every attempted action. No attempted action may
# disappear: even a failure with no Artifact is a first-class outcome.
ToolOutcomeCode = Literal[
    "SUCCESS",
    "EMPTY_RESULT",
    "UNSUPPORTED_CAPABILITY",
    "INPUT_UNRESOLVED",
    "INPUT_INCOMPATIBLE",
    "INVALID_IR",
    "UNKNOWN_FIELD",
    "SCOPE_MISMATCH",
    "COVERAGE_UNAVAILABLE",
    "SOURCE_TRANSIENT",
    "POLICY_BLOCKED",
    "MODEL_UNAVAILABLE",
    "INTERNAL_FAILURE",
    "INTERRUPTED",
    "UNCERTAIN",
]
AttemptStatus = Literal["SUCCEEDED", "FAILED", "INTERRUPTED", "UNCERTAIN"]


class ToolAttempt(BaseModel):
    """Durable record of one attempted action. Written before *and* after execution."""

    model_config = ConfigDict(extra="forbid")

    attempt_id: str
    request_id: str = ""
    need_id: str = ""
    capability: str = ""
    status: AttemptStatus = "FAILED"
    outcome_code: ToolOutcomeCode = "INTERNAL_FAILURE"
    detail: str = ""
    artifact_ids: tuple[str, ...] = ()
    binding_ids: tuple[str, ...] = ()
    retryable: bool = False
    external_effect_possible: bool = False
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None


class InputBinding(_Envelope):
    """Explicit binding of one downstream input to an exact upstream export.

    Prevents ambient export injection: a downstream action must name the exact
    Artifact/export it consumes, and the binding records which Need produced it.
    """

    binding_id: str
    name: str
    export_type: str
    source_need_id: str = ""
    source_artifact_id: str = ""
    source_export_id: str = ""
    ref_id: str = ""
    required: bool = True


class Need(BaseModel):
    """Information still required to satisfy the Goal.

    ``objective`` and ``expected_information`` are intentionally free-form. The planner
    may create Needs dynamically at runtime; dependencies are expressed through
    ``depends_on`` and ``input_bindings`` rather than a fixed workflow.
    """

    model_config = ConfigDict(extra="forbid")

    need_id: str
    revision: int = 1
    objective: str = ""
    expected_information: str = ""
    required_scope: Scope | None = None
    preferred_capabilities: tuple[str, ...] = ()
    proposed_capability: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    input_refs: tuple[str, ...] = ()
    input_bindings: tuple[InputBinding, ...] = ()
    depends_on: tuple[str, ...] = ()
    status: NeedStatus = "OPEN"
    criticality: Criticality = "CORE"
    supporting: bool = False
    satisfies_obligations: tuple[str, ...] = ()
    route_of: str = ""  # alternative route: the original Need this substitutes
    equivalence_note: str = ""
    linked_artifacts: tuple[str, ...] = ()
    unsatisfied_inputs: tuple[str, ...] = ()
    attempts: tuple[ToolAttempt, ...] = ()
    note: str = ""


class Goal(BaseModel):
    """The user's desired outcome plus the explicit constraints they stated.

    Explicit user information is preserved *by reference* (``constraint_refs``) so a
    tool that cannot support it reports a gap instead of silently dropping it.
    ``obligations`` is the frozen baseline the Planner's Needs are evaluated against.
    """

    model_config = ConfigDict(extra="forbid")

    goal_id: str
    revision: int = 1
    parent_goal_id: str = ""
    statement: str = ""
    scope: Scope | None = None
    obligations: tuple["UserObligation", ...] = ()
    inherited_obligations: tuple[str, ...] = ()
    changed_obligations: tuple[str, ...] = ()
    constraint_refs: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    ambiguity_notes: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    clarification_refs: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=utcnow)


# Kinds of explicit user obligation. Deliberately coarse: semantic flexibility stays in
# the Planner; these are only the baseline facts COMPLETE must be shown to cover.
ObligationKind = Literal[
    "ENTITY", "POPULATION", "MEMBERSHIP", "TIME", "SEASON", "GAME_TYPE",
    "METRIC", "QUALIFICATION", "RANKING", "GROUPING", "COMPARISON",
    "CLAIM_TYPE", "EXPLANATION",
]
ObligationStatus = Literal["OPEN", "COVERED", "PARTIAL", "MISSING"]


class UserObligation(BaseModel):
    """Immutable, frozen representation of an explicit user requirement.

    Derived from the user message / confirmed clarification and referenced back to its
    source span. Planner Needs are checked against this set; the Planner cannot lower or
    delete an obligation to reach COMPLETE.
    """

    model_config = ConfigDict(extra="forbid")

    obligation_id: str
    kind: ObligationKind
    description: str
    value: str = ""
    source_ref: str = ""
    origin: str = "USER_EXPLICIT"  # or USER_CONFIRMED
    status: ObligationStatus = "OPEN"


Goal.model_rebuild()


# ---------------------------------------------------------------------------
# Coverage / sufficiency
# ---------------------------------------------------------------------------

CoverageVerdict = Literal["SATISFIED", "PARTIAL", "UNSATISFIED", "IRRELEVANT"]
CoverageChannel = Literal["entity", "temporal", "population", "measure", "quality"]


class CoverageAssessment(_Envelope):
    """Whether evidence actually advances a Need for a Goal.

    Deterministic scope comparison supplies the channel scores; an optional judge may
    refine them. Completion depends on Goal coverage, never on "evidence exists".
    """

    assessment_id: str
    goal_id: str
    need_id: str = ""
    entity_coverage: float = 0.0
    temporal_coverage: float = 0.0
    population_coverage: float = 0.0
    measure_coverage: float = 0.0
    evidence_quality: float = 0.0
    supported_claims: tuple[str, ...] = ()
    missing_needs: tuple[str, ...] = ()
    core_goal_supported: bool = False
    verdict: CoverageVerdict = "UNSATISFIED"
    reasons: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()
    dimension_statuses: dict[str, str] = Field(default_factory=dict)
    judge_outcome: str = "UNAVAILABLE"
    assessment_available: bool = False
    independent: bool = True
    verified_artifact_ids: tuple[str, ...] = ()
    supporting_artifact_ids: tuple[str, ...] = ()


JudgeVerdict = Literal["SATISFIED", "PARTIAL", "UNSATISFIED", "IRRELEVANT",
                       "UNAVAILABLE"]


class JudgeAssessment(_Envelope):
    """Independent contextual assessment of apparently sufficient evidence.

    Separate from deterministic verification. The Judge may downgrade an apparently
    matching candidate, but may never override a deterministic hard mismatch, and a Judge
    outage is an explicit ``UNAVAILABLE`` condition rather than automatic success.
    """

    assessment_id: str
    goal_id: str
    need_id: str = ""
    outcome: JudgeVerdict = "UNAVAILABLE"
    helpful: bool = False
    interpretation_justified: bool = False
    claim_type_supported: bool = False
    joint_support: tuple[str, ...] = ()
    missing_information: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    judge: str = ""
    independent: bool = True


# ---------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------


# Evidence requirements differ by claim type: a statistical change alone must not support
# a causal explanation.
ClaimType = Literal[
    "OBSERVED_FACT", "DERIVED_CALCULATION", "COMPARISON", "REPORTED_EXPLANATION",
    "HYPOTHESIS", "CAUSAL_CLAIM",
]


class Claim(_Envelope):
    """One answerable statement with explicit support references.

    Prevents fluent unsupported synthesis: every important claim can be traced through
    derived Artifacts back to original evidence, and its claim type constrains what
    evidence counts as support.
    """

    claim_id: str
    text: str
    claim_type: ClaimType = "OBSERVED_FACT"
    support_refs: tuple[str, ...] = ()
    confidence: float = 0.5
    scope: Scope | None = None


# ---------------------------------------------------------------------------
# Tool contracts and requests
# ---------------------------------------------------------------------------


class ToolRequest(_Envelope):
    """A planner-issued request. Free-form objective plus Artifact references.

    The planner prefers passing references over copying large payloads.
    """

    request_id: str
    objective: str = ""
    capability: str = ""
    input_refs: tuple[str, ...] = ()
    structured_inputs: dict[str, Any] = Field(default_factory=dict)
    expected_outputs: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    references: tuple[str, ...] = ()


class ToolCapabilityContract(_Envelope):
    """What a tool accepts and produces, expressed in export-type capability names.

    Truthfulness matters: a tool must be able to declare important restrictions so the
    Planner never proposes a structurally impossible action, and so the Tool can reject
    unsupported inputs defensively. Fields default to the *most permissive* value only
    when the adapter genuinely supports the general case.
    """

    name: str
    accepts: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    description: str = ""
    cost: int = 1
    # Truthful restriction vocabulary.
    temporal_modes: tuple[str, ...] = ("ARBITRARY",)  # CURRENT_ONLY / SEASON / ARBITRARY_DATE_RANGE / HISTORICAL
    population_modes: tuple[str, ...] = ()  # active_roster / official_roster / observed_participants
    game_types: tuple[str, ...] = ()  # empty = provider does not distinguish game types
    entity_namespace: str = ""  # MLBAM / TEAM_ID / NONE
    supported_measures: tuple[str, ...] = ()
    required_bindings: tuple[str, ...] = ()
    required_inputs: tuple[str, ...] = ()
    availability: str = "AVAILABLE"  # AVAILABLE / CONFIG_REQUIRED / UNAVAILABLE
    authority: str = "DERIVED"  # AUTHORITATIVE / DERIVED / OBSERVED / NONE
    max_cost: int = 1

    def supports_temporal(self, mode: str) -> bool:
        return mode in self.temporal_modes or "ARBITRARY" in self.temporal_modes


# ---------------------------------------------------------------------------
# Envelope and planner trace
# ---------------------------------------------------------------------------

EnvelopeKind = Literal[
    "USER", "PLANNER", "TOOL_REQUEST", "TOOL_RESULT", "JUDGE", "RESPONSE",
    "CLARIFICATION", "SYSTEM"]


class MessageEnvelope(_Envelope):
    """Stable cross-component transfer envelope."""

    envelope_id: str
    kind: EnvelopeKind
    objective: str = ""
    structured: dict[str, Any] = Field(default_factory=dict)
    text: str = ""
    references: tuple[Reference, ...] = ()
    provenance: tuple[EvidenceSource, ...] = ()
    confidence: float = 0.5
    status: str = "OK"
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlannerDecision(_Envelope):
    """One reviewable planner iteration. Never hidden chain-of-thought."""

    decision_id: str
    iteration: int
    action: Literal["PLAN", "REQUEST_TOOL", "ADD_NEED", "ASSESS", "CLARIFY", "FINISH",
                    "STOP"] = "PLAN"
    need_id: str = ""
    request: ToolRequest | None = None
    artifact_refs: tuple[str, ...] = ()
    rationale: str = ""


class SemanticBrief(_Envelope):
    """The semantic layer's permissive output.

    This is a *helper*, not a runtime gate: it does not require a fully canonical
    semantic object before the planner can run. ``constraints`` preserves explicit user
    information; ``references`` tie each back to the user's wording.
    """

    brief_id: str
    goal_statement: str = ""
    understanding: str = ""
    entities: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    constraint_refs: tuple[str, ...] = ()
    ambiguities: tuple[str, ...] = ()
    analysis_hints: tuple[str, ...] = ()
    research_queries: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    proposed_scope: Scope | None = None
    assumptions: tuple[str, ...] = ()
    clarification_question: str = ""
    clarification_options: tuple[str, ...] = ()
    source: str = "llm"


class RuntimeEvent(_Envelope):
    """One append-only, redacted runtime event.

    Every important transition is reconstructable from this journal: goal interpreted /
    revised, need created / revised, binding selected, action admitted / rejected, IR
    validated / rejected, compile outcome, execution started / outcome, artifact
    registered, scope verified, judge assessment, state transition, replan, claim
    accepted / rejected, terminal decision, checkpoint, persistence error.

    Never contains hidden chain-of-thought, prompts, credentials or unrestricted
    payloads: ``detail`` is a bounded safe summary.
    """

    event_id: str
    event_type: str
    conversation_id: str = ""
    run_id: str = ""
    turn: int = 0
    goal_id: str = ""
    goal_revision: int = 0
    need_id: str = ""
    need_revision: int = 0
    request_id: str = ""
    attempt_id: str = ""
    parent_refs: tuple[str, ...] = ()
    detail: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)


class RuntimeTrace(_Envelope):
    """Structured, reviewable trace of the artifact runtime. Never hidden CoT."""

    raw_query: str = ""
    goal: Goal | None = None
    needs: tuple[Need, ...] = ()
    decisions: tuple[PlannerDecision, ...] = ()
    artifact_summaries: tuple[str, ...] = ()
    assessments: tuple[CoverageAssessment, ...] = ()
    coverage: dict[str, Any] = Field(default_factory=dict)
    claims: tuple[Claim, ...] = ()
    events: tuple[RuntimeEvent, ...] = ()
    attempts: tuple[ToolAttempt, ...] = ()
    obligation_coverage: dict[str, str] = Field(default_factory=dict)
    steps: tuple[str, ...] = ()
    tool_calls: tuple[str, ...] = ()
    sql_statements: tuple[str, ...] = ()
    status: str = ""
    answer: str = ""
