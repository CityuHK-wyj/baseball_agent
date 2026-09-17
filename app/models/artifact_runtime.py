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
    "CLAIM",
    "GOAL",
    "NEED",
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
    population: PopulationKind = ""
    time_range: TimeRange | None = None
    seasons: tuple[int, ...] = ()
    game_types: tuple[str, ...] = ()
    metric: str = ""
    event_population: str = ""
    source_coverage: tuple[str, ...] = ()
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


class ArtifactExport(_Envelope):
    """A reusable machine-consumable output of an Artifact.

    ``value`` carries the structured payload (a list of ids, a mapping, a number).
    ``text`` carries bounded free-form content. ``references`` point back to the
    evidence that produced it.
    """

    export_id: str
    export_type: str
    value: Any = None
    text: str = ""
    provenance: str = ""
    references: tuple[str, ...] = ()
    confidence: float = 0.5
    metadata: dict[str, Any] = Field(default_factory=dict)


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
    actual_scope: Scope | None = None
    lineage: tuple[str, ...] = ()
    confidence: float = 0.5
    status: ArtifactStatus = "OK"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)

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


class Need(BaseModel):
    """Information still required to satisfy the Goal.

    ``objective`` and ``expected_information`` are intentionally free-form. The planner
    may create Needs dynamically at runtime; dependencies are expressed through
    ``depends_on`` and ``input_refs`` rather than a fixed workflow.
    """

    model_config = ConfigDict(extra="forbid")

    need_id: str
    objective: str = ""
    expected_information: str = ""
    required_scope: Scope | None = None
    preferred_capabilities: tuple[str, ...] = ()
    proposed_capability: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    input_refs: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    status: NeedStatus = "OPEN"
    criticality: Criticality = "CORE"
    linked_artifacts: tuple[str, ...] = ()
    unsatisfied_inputs: tuple[str, ...] = ()
    note: str = ""


class Goal(BaseModel):
    """The user's desired outcome plus the explicit constraints they stated.

    Explicit user information is preserved *by reference* (``constraint_refs``) so a
    tool that cannot support it reports a gap instead of silently dropping it.
    """

    model_config = ConfigDict(extra="forbid")

    goal_id: str
    statement: str = ""
    scope: Scope | None = None
    constraint_refs: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    ambiguity_notes: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=utcnow)


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


# ---------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------


class Claim(_Envelope):
    """One answerable statement with explicit support references.

    Prevents fluent unsupported synthesis: every important claim can be traced through
    derived Artifacts back to original evidence.
    """

    claim_id: str
    text: str
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
    """What a tool accepts and produces, expressed in export-type capability names."""

    name: str
    accepts: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    description: str = ""
    cost: int = 1


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
    steps: tuple[str, ...] = ()
    tool_calls: tuple[str, ...] = ()
    sql_statements: tuple[str, ...] = ()
    status: str = ""
    answer: str = ""
