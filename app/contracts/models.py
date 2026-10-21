"""Core contracts: fixed envelope + flexible payload + explicit references."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import ConfigDict, Field
from app.contracts.immutability import GovernableModel as BaseModel
from app.contracts.evidence_origin import OriginLineage, PropositionOrigins
from app.contracts.evidence_grounding import SemanticGroundingReceipt


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ReferenceType(str, Enum):
    USER_MESSAGE = "USER_MESSAGE"
    USER_SPAN = "USER_SPAN"
    CONVERSATION_MESSAGE = "CONVERSATION_MESSAGE"
    ARTIFACT = "ARTIFACT"
    ARTIFACT_FIELD = "ARTIFACT_FIELD"
    ARTIFACT_EXPORT = "ARTIFACT_EXPORT"
    WEB_EVIDENCE = "WEB_EVIDENCE"
    KNOWLEDGE_ITEM = "KNOWLEDGE_ITEM"
    SQL = "SQL"
    DERIVED_COMPUTATION = "DERIVED_COMPUTATION"
    TOOL_REQUEST = "TOOL_REQUEST"
    TOOL_RESULT = "TOOL_RESULT"
    CLARIFICATION_ANSWER = "CLARIFICATION_ANSWER"
    ASSUMPTION = "ASSUMPTION"
    GOAL = "GOAL"
    NEED = "NEED"
    CLAIM = "CLAIM"


class EpistemicStatus(str, Enum):
    """How a statement may be presented in the final answer.

    See ADR 0014: a hypothesis may guide action and may be shown as a labelled
    hypothesis, but it can never be upgraded to ``SUPPORTED`` without evidence.
    """

    SUPPORTED = "SUPPORTED"      # backed by a specific, referenced piece of evidence
    INFERRED = "INFERRED"        # derived from supported facts; the derivation is ours
    BACKGROUND = "BACKGROUND"    # the model's own knowledge, clearly labelled, no citation
    HYPOTHESIS = "HYPOTHESIS"    # a candidate explanation that is offered as such
    UNKNOWN = "UNKNOWN"          # the system explicitly cannot say


class IdentityLevel(str, Enum):
    """How far an entity mention has been resolved (ADR 0015)."""

    CANDIDATE = "CANDIDATE"      # plausible identity from a prior/user/context
    RESOLVED = "RESOLVED"        # confirmed by at least one evidence source
    CANONICAL = "CANONICAL"      # carries a canonical database id


class RequirementAuthority(str, Enum):
    """Whether a Requirement may define requested factual truth (M2.12R3).

    The mere existence of a Requirement object is not proof of authority. A Requirement is
    authoritative only when it is (a) directly grounded in the bound user message, or (b)
    deterministically derived from one or more authoritative parent Requirements with
    explicit provenance. ``source=inferred`` is not, by itself, authority.
    """

    AUTHORITATIVE = "AUTHORITATIVE"
    UNPROVEN = "UNPROVEN"       # planning inference / hypothesis: never requested truth


class RequirementCompletion(str, Enum):
    """The semantic condition that completes a UserRequirement (ADR 0016).

    This is deliberately independent of which capability or tool produced the
    evidence. A database artifact is one way to satisfy a requirement, never the
    definition of the requirement.
    """

    GROUNDED = "GROUNDED"        # any grounded artifact addresses the request
    VALUE = "VALUE"              # a specific value from a verified calculation
    SCOPE = "SCOPE"              # a time/population window pinned by grounded evidence
    IDENTITY = "IDENTITY"        # a subject identified to at least candidate level
    EXPLANATION = "EXPLANATION"  # explanatory answer; background is allowed if not high-risk


class Reference(BaseModel):
    ref_type: ReferenceType
    ref_id: str
    source: str = ""          # e.g. "user", "sql:batting_events", "https://..."
    span: str = ""            # raw evidence span / snippet
    metadata: dict[str, Any] = Field(default_factory=dict)


class Hypothesis(BaseModel):
    """A candidate explanation, entity, relation or retrieval direction (ADR 0014).

    A hypothesis may direct the next action and may be shown to the user as a
    hypothesis. It is never itself Verified Evidence and must not be emitted as a
    ``SUPPORTED`` claim.
    """

    id: str = Field(default_factory=lambda: new_id("hyp"))
    statement: str
    origin: str = "prior"              # prior | user | context | search_snippet | knowledge
    basis: str = ""                    # why the model proposes it
    confidence: str = "low"            # low | medium | high (a prior confidence, not verification)
    entities: list[str] = Field(default_factory=list)
    requirement_id: str | None = None
    ref: str = ""


# --------------------------------------------------------------------------- semantic


class EntityMention(BaseModel):
    mention: str
    type_hint: str = "unknown"   # player / team / league / concept / unknown
    ref: str = ""
    # True when the interpreter says the mention is carried from the previous turn
    # (a pronoun, an ellipsis, or an explicit "the same player").
    carried: bool = False


class EntityReference(BaseModel):
    """A provenance-bearing entity reference that survives every runtime boundary.

    F1 (ADR 0018): the surface form (what the user literally said, including a pronoun)
    and the identity (canonical id/name, namespace, resolution status) are distinct and
    both preserved. A resolved contextual reference must never regress to an unresolved
    surface mention at a semantic -> goal -> tool boundary, and an unverified candidate
    must never masquerade as a canonical id.
    """

    surface: str
    type_hint: str = "unknown"
    canonical_id: str | None = None
    canonical_name: str | None = None
    namespace: str | None = None          # e.g. "MLBAM"
    status: IdentityLevel = IdentityLevel.CANDIDATE
    identity_verified: bool = False       # canonical came from a verified resolution/context
    source: str = "user"                  # user | interpreter | context | resolution
    antecedent: str | None = None         # prior surface/name the reference points back to
    carried_from_goal: str | None = None  # goal whose context supplied the identity
    ambiguous: bool = False               # a contextual reference with >1 possible antecedent
    options: list[str] = Field(default_factory=list)  # candidate antecedents when ambiguous
    reference: Reference | None = None

    def is_contextual(self) -> bool:
        return self.carried_from_goal is not None or self.antecedent is not None

    def to_mention(self) -> dict[str, Any]:
        """Serialise for an entity_resolution / web ToolRequest input."""
        payload: dict[str, Any] = {
            "mention": self.surface,
            "type_hint": self.type_hint,
            "status": self.status.value,
        }
        if self.canonical_id is not None:
            payload["canonical_id"] = self.canonical_id
        if self.canonical_name:
            payload["canonical_name"] = self.canonical_name
        if self.namespace:
            payload["namespace"] = self.namespace
        if self.antecedent:
            payload["antecedent"] = self.antecedent
        if self.carried_from_goal:
            payload["carried_from_goal"] = self.carried_from_goal
        payload["identity_verified"] = self.identity_verified
        return payload


class TimeExpression(BaseModel):
    text: str
    kind: str = "unknown"        # season / date / range / relative / unknown
    year: int | None = None
    ref: str = ""


class EvidenceIntent(BaseModel):
    """Interpreter-proposed requested evidence role; never evidence or satisfaction."""

    mode: Literal["CONFIRMED_STATE", "REPORTED_ACCOUNT", "FIRSTHAND_ACCOUNT", "PREDICTION"] = "CONFIRMED_STATE"
    information_class: Literal["GENERIC", "INJURY", "TRANSACTION", "STATUS", "PERFORMANCE", "GAME", "INTERVIEW", "COMMUNITY", "NARRATIVE"] = "GENERIC"


MeaningOrigin = Literal["CURRENT_USER_MESSAGE", "DERIVED_CONTEXT", "ASSISTANT_CONTEXT"]


from app.contracts.operations import OperationContract
from app.contracts.reporting import ReportingContract


class AnalysisRequest(BaseModel):
    reporting_contract: ReportingContract | None = None
    operation_contract: OperationContract | None = None
    text: str
    objective: str = ""
    ref: str = ""
    user_span: str = ""
    meaning_origin: MeaningOrigin = "CURRENT_USER_MESSAGE"
    analytical_role: Literal["BATTER", "PITCHER"] | None = None
    relation_semantics: RelationSemantics | None = None
    evidence_intent: EvidenceIntent | None = None


class EvidenceRequest(BaseModel):
    text: str
    objective: str = ""
    ref: str = ""
    user_span: str = ""
    meaning_origin: MeaningOrigin = "CURRENT_USER_MESSAGE"
    relation_semantics: RelationSemantics | None = None
    evidence_intent: EvidenceIntent | None = None


class Constraint(BaseModel):
    kind: str                    # season / game_population / date_range / measure / qualification / entity
    value: Any = None
    source_ref: str = ""
    explicit: bool = True


class UserAssertion(BaseModel):
    text: str
    status: Literal["UNVERIFIED", "CONFIRMED"] = "UNVERIFIED"
    ref: str = ""
    user_span: str = ""
    meaning_origin: MeaningOrigin = "CURRENT_USER_MESSAGE"


class Ambiguity(BaseModel):
    description: str
    options: list[str] = Field(default_factory=list)
    material: bool = True


class SemanticInterpretation(BaseModel):
    raw_query: str
    language: str = "unknown"
    understanding: str = ""
    entities: list[EntityMention] = Field(default_factory=list)
    time_expressions: list[TimeExpression] = Field(default_factory=list)
    analysis_requests: list[AnalysisRequest] = Field(default_factory=list)
    evidence_requests: list[EvidenceRequest] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    population_hints: list[str] = Field(default_factory=list)
    ambiguities: list[Ambiguity] = Field(default_factory=list)
    user_assertions: list[UserAssertion] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    # F1 (ADR 0018): the deterministic, provenance-bearing merge of semantic mentions
    # with accepted conversation context. Requirement construction reads this before
    # falling back to the raw mentions.
    entity_references: list[EntityReference] = Field(default_factory=list)
    references: list[Reference] = Field(default_factory=list)
    structured_hints: dict[str, Any] = Field(default_factory=dict)
    turn_type: str = "INDEPENDENT_GOAL"


# ------------------------------------------------------------------------ requirements


class RequirementKind(str, Enum):
    ENTITY = "ENTITY"
    SEASON = "SEASON"
    TIME = "TIME"
    GAME_POPULATION = "GAME_POPULATION"
    MEASURE = "MEASURE"
    ANALYSIS = "ANALYSIS"
    EVIDENCE = "EVIDENCE"
    CONSTRAINT = "CONSTRAINT"


class RelationKind(str, Enum):
    """The canonical, capability-agnostic relationship a Requirement asks for.

    These values are routing/interpretation signals, not hard tool rules and not a new
    VerificationIntent subsystem. ``CAUSE``/``EFFECT``/``COMPARISON``/``STATUS`` etc. have
    exactly one definition here; capabilities consume it through :class:`RelationSemantics`.
    """

    IDENTITY = "IDENTITY"
    SCOPE = "SCOPE"
    VALUE = "VALUE"
    AGGREGATION = "AGGREGATION"
    COMPARISON = "COMPARISON"
    CAUSE = "CAUSE"
    EFFECT = "EFFECT"
    EXPLANATION = "EXPLANATION"
    QUOTE = "QUOTE"
    STATUS = "STATUS"
    FRESHNESS = "FRESHNESS"
    EVENT = "EVENT"
    FACT = "FACT"
    GENERAL = "GENERAL"


class RelationSemantics(BaseModel):
    """The normalized machine-usable interpretation of a Requirement's relationship.

    It belongs to the Requirement semantic contract (not to any one capability) and is
    consumed where relevant by Planner routing, local-analytics mapping, web query
    construction, ranking, span selection, candidate admission and verifier preparation.
    It is interpretation, never evidence: it can direct cognition but cannot by itself
    satisfy a factual obligation or bypass Artifact/Judge governance.
    """

    relation_kind: RelationKind = RelationKind.GENERAL
    # Canonical requested predicate for native structured records; an empty value
    # is unresolved, never permission to use any fact about the same subject.
    predicate: str = ""
    # Multi-label interpretation: a compound Requirement ("causes and effects") may carry more
    # than one relation meaning. ``relation_kind`` remains the primary label for consumers that
    # need one; ``relation_kinds`` is the open, non-exhaustive set. Neither is a tool command.
    relation_kinds: list[RelationKind] = Field(default_factory=list)
    subject_refs: list[str] = Field(default_factory=list)
    # Output units are distinct from the input population. POOLED is interpretation,
    # not aggregation authority: numerical pooling still needs operation_contract.
    output_granularity: Literal['PER_SUBJECT', 'POOLED', 'UNRESOLVED'] = 'UNRESOLVED'
    object_refs: list[str] = Field(default_factory=list)
    # Bound arguments of a directed factual relation. An empty side is the answer slot,
    # not an asserted fact: CAUSE asks what led to target; EFFECT asks what source led to.
    relation_source: str = ""
    relation_target: str = ""
    metric_refs: list[str] = Field(default_factory=list)
    time_scope: dict[str, Any] = Field(default_factory=dict)
    population: str | None = None
    comparison_direction: str = ""       # increase | decrease | difference | ""
    polarity: str = "AFFIRMED"            # AFFIRMED | NEGATED
    semantic_cues: list[str] = Field(default_factory=list)
    source_text: str = ""                 # the user phrase this interpretation came from


class TemporalScope(BaseModel):
    """A Requirement's resolved temporal scope (M2.5).

    The original wording is preserved separately on the Requirement; this is the
    deterministic, machine-usable window resolved once at normalization time against the
    Goal anchor date. It is descriptive (never a routing command) and is shared, not
    re-invented, by downstream capabilities.
    """

    kind: str = "OPEN"                 # SEASON | DATE | RANGE | RELATIVE | OPEN | UNRESOLVED
    original_text: str = ""
    start: str | None = None            # ISO date
    end: str | None = None              # ISO date
    season: int | None = None
    window_days: int | None = None
    anchor_date: str | None = None
    information_class: str = "GENERIC"
    # True only when the user stated the scope explicitly (a date, season, duration or
    # absolute range). An inferred domain default stays False so precision is never invented.
    confirmed: bool = False
    note: str = ""

    def is_window(self) -> bool:
        return self.kind in {"DATE", "RANGE", "RELATIVE"} and bool(self.start or self.end)


class ComparativeRelation(str, Enum):
    """Numeric relations with a deterministic, order-preserving scalar interpretation."""

    GT = "GT"
    LT = "LT"
    EQ = "EQ"


class ComparisonOperand(BaseModel):
    """References authoritative Requirements that define one complete analytical scope.

    No value or Artifact ID lives here. The referenced Requirements are re-bound by
    the existing conformance machinery; a model's description is never a scope proof.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_ids: tuple[str, ...]
    measure_requirement_id: str


class ComparisonContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lhs: ComparisonOperand
    rhs: ComparisonOperand
    relation: ComparativeRelation


class RequirementProvenance(BaseModel):
    """Backend-bound ancestry, distinct from correctness of semantic interpretation."""

    origin: Literal["CURRENT_USER_MESSAGE", "VALIDATED_CLARIFICATION", "DERIVED_CONTEXT", "ASSISTANT_CONTEXT"]
    message_id: str
    message_digest: str
    bound_message: str
    user_span: str
    parent_requirement_ids: list[str] = Field(default_factory=list)
    context_antecedent: str = ""
    clarification_target_digest: str = ""
    clarification_answer: str = ""


class UserRequirement(BaseModel):
    id: str = Field(default_factory=lambda: new_id("req"))
    kind: RequirementKind
    statement: str
    required: bool = True
    source: Literal["user", "inferred", "clarification"] = "user"
    references: list[Reference] = Field(default_factory=list)
    value: Any = None
    supersedes: str | None = None
    # Semantic completion condition; derived from `kind` when unset (ADR 0016).
    completion: RequirementCompletion | None = None
    # Whether a non-high-risk EXPLANATION requirement may be answered with clearly
    # labelled background knowledge when no grounded evidence is available.
    background_ok: bool = False
    min_identity: IdentityLevel | None = None
    # F1 (ADR 0018): the full contextual reference for an ENTITY requirement.
    entity_ref: EntityReference | None = None
    # Immutable user intent: the exact request that originated this Requirement. It is never
    # overwritten by the normalized ``statement``/``relation_semantics`` interpretation.
    original_intent: str = ""
    # Exact analytical source clause bound by RequirementBuilder. A normalized model
    # objective remains a planning view, not permission to invent restrictions.
    bound_source_clause: str = ""
    # Canonical interpretation of the required relationship (see RelationSemantics).
    relation_semantics: RelationSemantics | None = None
    # Only a trusted semantic/clarification binder may supply this governed structure.
    # Open-language decomposition never manufactures operand IDs or a numeric relation.
    comparison: ComparisonContract | None = None
    reporting_contract: ReportingContract | None = None
    operation_contract: OperationContract | None = None
    # Temporal scope: the user's original wording is preserved here and never overwritten by
    # the resolved machine-usable window.
    original_scope: dict[str, Any] = Field(default_factory=dict)
    resolved_scope: TemporalScope | None = None
    # M2.12R3 authority provenance. A derived/inferred Requirement may only define
    # requested truth when it carries a proven authoritative parent and a deterministic
    # derivation. These fields are additive; the original user text is never lost.
    parent_requirement_ids: list[str] = Field(default_factory=list)
    derivation_kind: str = ""
    derivation_proof: str = ""
    authority_status: RequirementAuthority = RequirementAuthority.AUTHORITATIVE
    provenance: RequirementProvenance | None = None
    evidence_intent: EvidenceIntent | None = None


class PropositionKind(str, Enum):
    """What kind of answer obligation a proposition represents (ADR 0021).

    A proposition is an answer slot, not necessarily a declarative fact supplied by the
    user. It supports explanatory, statistical, historical, comparative and multi-source
    questions without hardcoding any one of them.
    """

    IDENTITY = "IDENTITY"        # the subject must be identified
    SCOPE = "SCOPE"              # a time/population window must be pinned
    VALUE = "VALUE"              # a specific measure value must be verified
    FACT = "FACT"                # a concrete factual proposition
    EVENT = "EVENT"              # a verified event and its boundary
    COMPARISON = "COMPARISON"    # a computed change/difference must be demonstrated
    EXPLANATION = "EXPLANATION"  # an explanatory / role / usage obligation
    GROUNDED = "GROUNDED"        # a general grounded information need


class CoverageState(str, Enum):
    """Proposition-level coverage, computed from verified evidence (ADR 0021)."""

    SATISFIED = "SATISFIED"          # scope-compatible, verified support exists
    PARTIAL = "PARTIAL"              # some support, but a required aspect is missing
    UNSUPPORTED = "UNSUPPORTED"      # material was investigated but does not establish it
    CONTRADICTED = "CONTRADICTED"    # real evidence contradicts the proposition
    NOT_ATTEMPTED = "NOT_ATTEMPTED"  # no permitted path attempted this obligation
    BLOCKED = "BLOCKED"              # a prerequisite/source was unavailable


class TemporalMode(str, Enum):
    """What temporal relationship an answer obligation requires of its evidence.

    WSA-01: a mutable, present-state fact needs a current temporal basis; a historical
    question is answered by evidence about that historical period. Evidence publication
    time, retrieval time, event time and the requested as-of time are distinct and are
    never conflated (see :class:`TemporalExpectation`).
    """

    HISTORICAL = "HISTORICAL"          # a past season/date is requested
    CURRENT_STATE = "CURRENT_STATE"    # the state now (or within the resolved window)
    AS_OF = "AS_OF"                   # the state at one explicit date
    DATE_RANGE = "DATE_RANGE"         # a bounded window is requested
    UNCONSTRAINED = "UNCONSTRAINED"    # no temporal admission requirement


class TemporalExpectation(BaseModel):
    """The typed temporal basis an obligation demands from admissible evidence.

    Derived deterministically from the proposition's governed scope, never from a fresh
    natural-language cue list. ``required`` is True only when an evidence item must carry a
    trustworthy temporal basis to be admitted for a mutable/current-state obligation.
    """

    mode: TemporalMode = TemporalMode.UNCONSTRAINED
    season: int | None = None
    as_of: str | None = None            # ISO date the state must hold at
    start: str | None = None            # inclusive ISO window start
    end: str | None = None              # inclusive ISO window end
    anchor_date: str | None = None      # Goal anchor used to resolve relative wording
    information_class: str = "GENERIC"  # mutable class hint (INJURY/TRANSACTION/STATUS)
    required: bool = False


class PropositionRelation(str, Enum):
    """The logical relation one evidence passage bears to the *target proposition* (WSA-04).

    This is a distinct semantic dimension from assertion strength and temporal semantics.
    It is answered by the Evidence Semantic Assessor (or, fail-closed, by a bounded
    deterministic contradiction hint) - never by required-token overlap. The deterministic
    layer consumes only these typed states; it never parses arbitrary natural-language
    negation itself.
    """

    ENTAILS = "ENTAILS"          # the passage asserts the proposition holds
    CONTRADICTS = "CONTRADICTS"  # the passage asserts the proposition does not hold
    UNCERTAIN = "UNCERTAIN"      # on-topic, but the relation is not established either way
    IRRELEVANT = "IRRELEVANT"    # the passage does not address the proposition


class AssertionStrength(str, Enum):
    """How strongly an evidence passage asserts its proposition (WSA-02).

    Ordered weakest-to-strongest by :data:`ASSERTION_RANK`. ``DENIED`` carries negative
    polarity and is handled separately from strength.
    """

    ASSERTED = "ASSERTED"        # stated as fact by the source
    REPORTED = "REPORTED"        # attributed reporting (sources say / reportedly)
    PREDICTED = "PREDICTED"      # expected / projected / scheduled
    SPECULATIVE = "SPECULATIVE"  # could / might / may / possible / rumor
    DENIED = "DENIED"            # explicitly negated / refuted
    UNKNOWN = "UNKNOWN"          # the assessor could not decide


ASSERTION_RANK: dict["AssertionStrength", int] = {
    AssertionStrength.ASSERTED: 3,
    AssertionStrength.REPORTED: 2,
    AssertionStrength.PREDICTED: 1,
    AssertionStrength.SPECULATIVE: 1,
    AssertionStrength.UNKNOWN: 0,
    AssertionStrength.DENIED: -1,
}


class AssertionRequirement(str, Enum):
    """The minimum assertion strength a proposition demands of its evidence."""

    ASSERTED = "ASSERTED"                      # a confirmed factual state
    REPORTED = "REPORTED"                      # attributed reporting suffices
    SPECULATIVE_ALLOWED = "SPECULATIVE_ALLOWED"  # rumor/prediction question


ASSERTION_REQUIREMENT_RANK: dict["AssertionRequirement", int] = {
    AssertionRequirement.ASSERTED: 3,
    AssertionRequirement.REPORTED: 2,
    AssertionRequirement.SPECULATIVE_ALLOWED: 1,
}


class AssertionExpectation(BaseModel):
    """The typed modality an obligation demands from admissible evidence (WSA-02).

    ``minimum_strength`` is the weakest assertion that may support the proposition. The
    deterministic layer compares it with the assessed evidence strength; the LLM may
    propose the assessment but can never lower the requirement or self-certify support.
    """

    minimum_strength: AssertionRequirement = AssertionRequirement.ASSERTED
    allow_prediction: bool = False
    allow_reporting: bool = False


# ------------------------------------------------------- source role / authority (WSA-03)


class SourceClass(str, Enum):
    """What kind of organization/venue produced the material (WSA-03).

    Deliberately small and orthogonal to :class:`ContentRole`: the same domain hosts
    different roles, and authority is relational (source role x Requirement need), not a
    universal scalar trust score.
    """

    FIRST_PARTY = "FIRST_PARTY"      # the team/club/player the fact is about
    LEAGUE = "LEAGUE"                # the governing league/official body
    NEWS = "NEWS"                    # established news/reporting outlet
    SPECIALIST = "SPECIALIST"        # beat writer / trade / injury specialist
    AGGREGATOR = "AGGREGATOR"        # feed/link aggregator, no original reporting
    COMMUNITY = "COMMUNITY"          # forum, fan community, first-hand account
    REFERENCE = "REFERENCE"          # encyclopedia / curated reference
    STRUCTURED = "STRUCTURED"        # provider-native structured record endpoint
    UNKNOWN = "UNKNOWN"


class ContentRole(str, Enum):
    """In what capacity the material is speaking (WSA-03)."""

    PRIMARY_RECORD = "PRIMARY_RECORD"        # native transaction/roster/box record
    OFFICIAL_STATEMENT = "OFFICIAL_STATEMENT"  # first-party announcement
    REPORTING = "REPORTING"                  # attributed journalism
    ANALYSIS = "ANALYSIS"                    # editorial analysis
    OPINION = "OPINION"                      # opinion/commentary
    SPECULATION = "SPECULATION"              # rumor/speculation
    FIRSTHAND = "FIRSTHAND"                  # participant/community first-hand
    REFERENCE = "REFERENCE"                  # definition/reference material
    UNKNOWN = "UNKNOWN"


class EvidentialBasis(str, Enum):
    """Primary (first-hand record/statement), secondary (reporting/analysis), tertiary."""

    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"
    TERTIARY = "TERTIARY"
    UNKNOWN = "UNKNOWN"


class SourceRoleAssessment(BaseModel):
    """A typed proposal about what the source material *is* and how it is speaking.

    Proposed by the Evidence/Source assessor (one rich interpretation call) or proven
    deterministically from provider-native structured metadata. It never asserts truth; the
    deterministic authority gate compares it with the Requirement's authority expectation.
    """

    source_class: SourceClass = SourceClass.UNKNOWN
    content_role: ContentRole = ContentRole.UNKNOWN
    evidential_basis: EvidentialBasis = EvidentialBasis.UNKNOWN
    attributed_to: str = ""
    rationale: str = ""
    source: str = "deterministic"        # deterministic | llm

    def is_known(self) -> bool:
        return not (
            self.source_class == SourceClass.UNKNOWN
            and self.content_role == ContentRole.UNKNOWN
            and self.evidential_basis == EvidentialBasis.UNKNOWN
        )


class AuthorityNeed(str, Enum):
    """What authority a Requirement demands of its evidence (WSA-03).

    Relational, not a universal trust threshold. ``GENERAL`` has no authority expectation;
    the others fail closed when the source role is unresolved. The source-role compatibility
    is one orthogonal dimension alongside relation/assertion/temporal - never a scalar score.
    """

    GENERAL = "GENERAL"                      # any admissible source (no authority expectation)
    OFFICIAL_STATE = "OFFICIAL_STATE"        # first-party/structured record or credible reporting
    REPORTED_ACCOUNT = "REPORTED_ACCOUNT"    # reporting/analysis/commentary is the subject
    FIRSTHAND_ACCOUNT = "FIRSTHAND_ACCOUNT"  # community/first-hand account is the subject


class AuthorityExpectation(BaseModel):
    """The typed source-role basis an obligation demands of admissible evidence (WSA-03)."""

    need: AuthorityNeed = AuthorityNeed.GENERAL
    required: bool = False


class AnswerProposition(BaseModel):
    """An explicit answer obligation derived from a Goal (ADR 0021).

    It carries the subject, scope, required information/operation, mandatory status,
    dependencies and the coverage determined from verified evidence. It is the unit the
    Judge covers and the final answer must communicate.
    """

    id: str = Field(default_factory=lambda: new_id("prop"))
    goal_id: str = ""
    requirement_id: str | None = None
    # Reverse coverage links conserved when equivalent source-scope slots merge.
    # requirement_id remains the construction/evidence owner; this adds no authority.
    requirement_ids: list[str] = Field(default_factory=list)
    statement: str
    kind: PropositionKind = PropositionKind.GROUNDED
    subject: str = ""
    entity_id: str | None = None
    namespace: str | None = None
    time_scope: dict[str, Any] = Field(default_factory=dict)  # season / date_range
    population: str | None = None
    required_information: str = ""
    operation: str = ""
    mandatory: bool = True
    depends_on: list[str] = Field(default_factory=list)
    coverage: CoverageState = CoverageState.NOT_ATTEMPTED
    supporting_claim_ids: list[str] = Field(default_factory=list)
    contradicting_claim_ids: list[str] = Field(default_factory=list)
    supporting_artifact_ids: list[str] = Field(default_factory=list)
    evidence_finding_ids: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    note: str = ""
    # Canonical relationship interpretation carried from the originating Requirement. It is
    # additive metadata for downstream consumers; it never changes verification authority.
    relation_semantics: RelationSemantics | None = None
    # Copied from the owning Requirement, not from an LLM proposition spec.
    comparison: ComparisonContract | None = None
    # WSA-01/02: typed evidence expectations. They are derived deterministically from the
    # governed scope/relationship at proposition construction and frozen here. The LLM may
    # propose a semantic evidence *assessment*, but never these expectations and never the
    # satisfaction decision.
    information_class: str = ""
    temporal_expectation: TemporalExpectation | None = None
    assertion_expectation: AssertionExpectation | None = None
    # WSA-03: the typed source-role basis this proposition demands. Derived deterministically
    # from governed signals and frozen here; the assessor proposes the source role, never
    # this expectation and never the satisfaction decision.
    authority_expectation: AuthorityExpectation | None = None
    # Exact original-observation slot owned by reporting_contract; never a row guess.
    reporting_index: int | None = Field(default=None, ge=0)
    # Derived only from current Requirements; never supplied by a decomposition spec.
    subject_requirement_ids: list[str] = Field(default_factory=list)
    subject_granularity: Literal['PER_SUBJECT', 'POOLED', 'UNRESOLVED'] | None = None
    # Copied from the owning governed Requirement, never a Planner/proposition spec.
    evidence_intent: EvidenceIntent | None = None

    @property
    def owner_requirement_ids(self) -> list[str]:
        return list(dict.fromkeys([rid for rid in [self.requirement_id, *self.requirement_ids] if rid]))


class ClarificationDelta(BaseModel):
    """Backend-validated user change to exactly one analytical candidate/parent."""

    action: Literal["DISCHARGE_RESIDUE", "PRESERVE_RESIDUE", "REPLACE_INTERPRETATION"]
    requirement_id: str
    candidate_id: str
    source_span: str
    source_goal_id: str
    user_answer: str
    target_digest: str
    confirmed_residue: str = ""


class Goal(BaseModel):
    id: str = Field(default_factory=lambda: new_id("goal"))
    statement: str
    raw_query: str
    requirements: list[UserRequirement] = Field(default_factory=list)
    # ADR 0021: the explicit answer obligations the Goal must cover to be COMPLETE.
    propositions: list[AnswerProposition] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    parent_goal_id: str | None = None
    turn_type: str = "INDEPENDENT_GOAL"
    resolved_time_scope: dict[str, Any] = Field(default_factory=dict)
    # The Goal anchor for relative temporal expressions, fixed at Goal creation. A replan must
    # reuse this value so a relative window never silently moves while the Goal executes.
    anchor_date: str | None = None
    original_time_scope: dict[str, Any] = Field(default_factory=dict)
    # Operational semantic-resolution ledger, persisted with the Goal across replans.
    analytical_partition_clause: str = ""
    analytical_resolution_state: dict[str, Any] = Field(default_factory=dict)
    # Only Engine.apply_semantic_delta populates this after a validated user action.
    semantic_deltas: list[ClarificationDelta] = Field(default_factory=list)


# ----------------------------------------------------------------------------- planning


class NeedStatus(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    BLOCKED = "BLOCKED"
    SATISFIED = "SATISFIED"
    UNSATISFIED = "UNSATISFIED"
    CANCELLED = "CANCELLED"


class NeedCriticality(str, Enum):
    CORE = "CORE"
    SUPPORTING = "SUPPORTING"


class PlannerNeed(BaseModel):
    id: str
    objective: str
    capability: str
    depends_on: list[str] = Field(default_factory=list)
    input_requirements: list[str] = Field(default_factory=list)
    desired_output: str = ""
    required_evidence: str = ""
    constraints: dict[str, Any] = Field(default_factory=dict)
    criticality_hint: str = "supporting"
    criticality: NeedCriticality = NeedCriticality.SUPPORTING
    status: NeedStatus = NeedStatus.PENDING
    origin: Literal["planner", "repair", "supporting"] = "planner"
    repair_of: str | None = None
    reuse_of: str | None = None
    requires: list[str] = Field(default_factory=list)   # user requirement ids served


class InputBinding(BaseModel):
    need_id: str
    export_id: str
    export_type: str
    artifact_id: str
    requirement: str = ""
    source_need_id: str = ""


class BindingFailure(BaseModel):
    """A typed reason a declared/required input could not be bound.

    Binder contract codes (never collapsed into NO_RESULTS):
    ARTIFACT_NOT_ACCEPTED, EXPORT_TYPE_INCOMPATIBLE, EMPTY_ENTITY_SET,
    ENTITY_INCOMPATIBLE, SCOPE_INCOMPATIBLE, UNRESOLVED_REQUIRED_INPUT.
    """

    need_id: str
    dependency_id: str = ""
    code: str
    detail: str = ""
    channel: str = ""
    required: bool = True
    retryable: bool = False


class ToolRequest(BaseModel):
    id: str = Field(default_factory=lambda: new_id("tr"))
    need_id: str
    capability: str
    objective: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    bindings: list[InputBinding] = Field(default_factory=list)
    references: list[Reference] = Field(default_factory=list)
    max_fetches: int = 4


class ToolOutcome(BaseModel):
    status: Literal["SUCCESS", "NO_DATA", "FAILED", "BLOCKED"]
    code: str = "OK"
    message: str = ""
    retryable: bool = False
    need_id: str = ""
    tool_request_id: str = ""
    classification: str = ""


# -------------------------------------------------------------------------------- scope


class DateRange(BaseModel):
    start: date | None = None
    end: date | None = None


class Scope(BaseModel):
    entities: list[str] = Field(default_factory=list)
    entity_names: list[str] = Field(default_factory=list)
    season: int | None = None
    game_population: str | None = None
    game_types: list[str] = Field(default_factory=list)
    date_range: DateRange | None = None
    measure: str | None = None
    measures: list[str] = Field(default_factory=list)
    source: str | None = None
    source_coverage: str | None = None

    def is_empty(self) -> bool:
        return not any(
            [self.entities, self.entity_names, self.season, self.game_population,
             self.game_types, self.date_range, self.measure, self.measures, self.source]
        )


class ScopeGap(BaseModel):
    channel: str
    requested: str
    actual: str
    material: bool = True


# ---------------------------------------------------------------------------- artifact


class SelectionRule(BaseModel):
    """An explicit, auditable row-selection rule for F2 artifact projection."""

    mode: str = "TOP"                 # TOP | INDEX | ENTITY_ID
    by: str = ""                      # ordering measure/column the source is sorted by
    index: int | None = None
    entity_id: str | None = None
    detail: str = ""


class EnumerationContract(BaseModel):
    """F5 (ADR 0020): what population an analytical artifact enumerates.

    A global ranking/difference/extremum may only be derived from inputs whose
    completeness is COMPLETE, unless recorded proof shows truncation is safe.
    """

    population: str | None = None
    qualification: str | None = None
    ordering: list[str] = Field(default_factory=list)
    ordering_verified: bool = False
    limit: int | None = None
    returned_rows: int = 0
    # Exact number of groups when it was measured with a bounded COUNT pushdown.
    population_count: int | None = None
    completeness: Literal["COMPLETE", "TRUNCATED", "UNKNOWN"] = "UNKNOWN"
    identity_columns: list[str] = Field(default_factory=list)
    entity_kind: str | None = None        # e.g. "PLAYER" — verified identity domain
    entity_namespace: str | None = None   # e.g. "MLBAM"
    source: str | None = None
    source_coverage: str | None = None
    season: int | None = None
    date_range: DateRange | None = None
    note: str = ""

    def is_complete(self) -> bool:
        return self.completeness == "COMPLETE"


class GlobalOperation(BaseModel):
    """F5 (ADR 0020): structured semantics of a cross-artifact computation.

    The kind decides which compatibility rules apply. ``UNKNOWN`` fails closed when
    more than one input is combined into a global/derived claim.
    """

    kind: Literal["LOCAL", "EXTREMUM", "RANK", "DELTA", "JOIN", "UNION", "UNKNOWN"] = "UNKNOWN"
    identity: str = ""                   # required stable identity kind, e.g. "PLAYER"
    partition_by: str = ""               # SEASON | DATE_RANGE | SOURCE | "" (infer)
    dedup_key: list[str] = Field(default_factory=list)
    note: str = ""

    def is_global(self) -> bool:
        return self.kind in {"EXTREMUM", "RANK", "DELTA", "UNION", "UNKNOWN"}


class Export(BaseModel):
    id: str
    type: str
    payload: Any = None
    contract: dict[str, Any] = Field(default_factory=dict)


class Artifact(BaseModel):
    id: str = Field(default_factory=lambda: new_id("art"))
    kind: str
    structured_data: dict[str, Any] = Field(default_factory=dict)
    text_content: str = ""
    exports: list[Export] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    references: list[Reference] = Field(default_factory=list)
    requested_scope: Scope | None = None
    actual_scope: Scope | None = None
    # F5 (ADR 0020): population/qualification/ordering/limit/completeness of a result.
    enumeration: EnumerationContract | None = None
    lineage: list[str] = Field(default_factory=list)
    confidence: str = "ACCEPTABLE"
    status: Literal["ACCEPTED", "PARTIAL", "REJECTED"] = "ACCEPTED"
    need_id: str = ""
    created_at: datetime = Field(default_factory=utc_now)

    def export(self, export_type: str) -> Export | None:
        for item in self.exports:
            if item.type == export_type:
                return item
        return None


# ------------------------------------------------------------------ evidence (ADR 0022)


class VerificationStatus(str, Enum):
    """The decision a verifier reaches about one span for one proposition.

    These are deliberately separate from the Phase-4 ``EpistemicStatus`` labels: a span
    can be INSUFFICIENT or IRRELEVANT without becoming a claim, and a passage that
    contradicts a proposition is recorded rather than silently dropped.
    """

    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    INSUFFICIENT = "INSUFFICIENT"
    IRRELEVANT = "IRRELEVANT"


class RepresentationKind(str, Enum):
    """What a representation *is*, independent of where it came from.

    Kind is a content-access fact, never an authority signal (ADR 0031). A ``JSON``
    representation is not more trustworthy than an ``HTML`` one; trust belongs to the
    source identity and evidence policy.
    """

    RAW = "RAW"                  # acquired bytes/response body, undecoded
    HTML = "HTML"
    JSON = "JSON"
    XML = "XML"
    FEED_ITEM = "FEED_ITEM"
    TEXT = "TEXT"                # extracted plain text (the existing body_text path)
    DOM = "DOM"                  # browser-rendered markup
    STRUCTURED = "STRUCTURED"    # a native structured record view (JSON/XML fields)
    TRANSCRIPT = "TRANSCRIPT"
    TABLE = "TABLE"
    DERIVED = "DERIVED"          # a task-driven derived semantic view


class Representation(BaseModel):
    """A minimal, stable envelope around one concrete view of a content artifact.

    The content stays representation-specific. Missing metadata stays missing; no field is
    fabricated to complete a schema. ``derived_from`` plus ``derivation_method`` and
    ``derived_by`` form the lineage graph, and ``content_hash`` makes a derivation
    replayable without mutating its parent (ADR 0031).
    """

    id: str = Field(default_factory=lambda: new_id("rep"))
    kind: RepresentationKind = RepresentationKind.TEXT
    media_type: str = "text/plain"
    # Stable reference. For inline content this is ``inline:<content_hash>``; otherwise a
    # persisted content address. It is a handle, not a second copy of the payload.
    content_ref: str = ""
    content: str = ""                   # retained payload when practical (reproducibility)
    content_hash: str = ""
    acquisition_method: str = ""        # http | rss_item | browser | tavily_extract | derived
    derived_from: str | None = None     # parent representation id, when derived
    derivation_method: str = ""         # extractor / derivation version
    derived_by: str = "source"          # source | deterministic | llm
    # Native structure is preserved here rather than flattened into prose.
    structured_data: Any = None
    locator_type: str = "char_offsets"  # char_offsets | json_pointer | row_column | node
    provenance: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    def is_derived(self) -> bool:
        return self.derived_from is not None or self.derived_by in {"deterministic", "llm"}

    def is_source(self) -> bool:
        """True for an as-acquired representation, never a task-driven interpretation."""
        return not self.is_derived()


class RetrievedDocument(BaseModel):
    """A page that was actually fetched (not a search hit or a snippet).

    This model *is* the Web Research ContentArtifact (ADR 0031): the deliberately thin
    identity/provenance envelope for one acquired resource, carrying zero or more explicit
    ``representations``. No universal semantic schema (author, date, type, speaker) is
    required; those remain unavailable unless a source actually supplies them or a task
    explicitly derives them.
    """

    id: str = Field(default_factory=lambda: new_id("doc"))
    url: str = ""
    final_url: str = ""
    provider: str = ""                 # search engine / feed that produced the hit
    title: str = ""
    publication_date: str | None = None
    retrieved_at: str = ""
    retrieval_status: str = "OK"        # OK | FETCH_FAILED | POLICY_BLOCKED | NO_CONTENT
    error_code: str = ""
    content_hash: str = ""
    text_excerpt: str = ""              # bounded preview; body_text supports offset replay
    body_text: str = ""                 # extracted body, for exact offset replay
    source_quality: str = ""
    initial_url: str = ""
    canonical_url: str = ""
    retrieval_method: str = "http"
    fallback_steps: list[str] = Field(default_factory=list)
    source_type: str = ""
    source_language: str = ""
    representation_provider: str = ""
    response_status: int | None = None
    elapsed_ms: int = 0
    # Content-artifact extension (ADR 0031). ``representations`` may be empty for a legacy
    # record; ``primary_representation_id`` names the representation that ``body_text``
    # replays against. ``source_uri`` is the stable locator (defaults to canonical/final URL).
    representations: list[Representation] = Field(default_factory=list)
    primary_representation_id: str = ""
    source_uri: str = ""
    resolution_traces: list[dict[str, Any]] = Field(default_factory=list)

    def representation(self, representation_id: str) -> Representation | None:
        for item in self.representations:
            if item.id == representation_id:
                return item
        return None

    def primary_representation(self) -> Representation | None:
        if self.primary_representation_id:
            found = self.representation(self.primary_representation_id)
            if found is not None:
                return found
        return self.representations[0] if self.representations else None


class EvidenceSource(str, Enum):
    """Where a span's text actually came from.

    Only ``DOCUMENT_BODY`` text from an ``OK`` retrieved document is admissible for
    ``SUPPORTED``. A search-result title or snippet may guide discovery but must never
    impersonate a fetched page passage (Phase 5B closure, ADR 0022).
    """

    DOCUMENT_BODY = "DOCUMENT_BODY"
    SEARCH_TITLE = "SEARCH_TITLE"
    SEARCH_SNIPPET = "SEARCH_SNIPPET"
    STRUCTURED = "STRUCTURED"


class EvidenceSpan(BaseModel):
    """An inspectable passage (or structured fragment) of a retrieved document.

    ``source_type`` and ``retrieval_status`` declare the real provenance of the text so a
    title/snippet can never be silently promoted to body evidence. ``representation_id``
    binds the span to the concrete representation it was extracted from (ADR 0031). A
    structured payload uses ``locator_type='json_pointer'`` and a stable ``locator`` rather
    than being degraded into artificial prose offsets.
    """

    id: str = Field(default_factory=lambda: new_id("span"))
    document_id: str = ""
    representation_id: str = ""         # concrete representation this span replays against
    text: str = ""
    start: int | None = None
    end: int | None = None
    location: str = ""                  # e.g. "paragraph 4" / "sentence 12"
    source_type: EvidenceSource = EvidenceSource.DOCUMENT_BODY
    retrieval_status: str = "OK"        # OK | FETCH_FAILED | POLICY_BLOCKED | NO_CONTENT
    locator_type: str = "char_offsets"  # char_offsets | json_pointer | row_column | node
    locator: str = ""                   # e.g. "/results/0/homeRuns" for structured data
    entities: list[str] = Field(default_factory=list)
    relations: list[str] = Field(default_factory=list)
    time_scope: dict[str, Any] = Field(default_factory=dict)
    population: str | None = None
    proposition_ids: list[str] = Field(default_factory=list)
    verification: VerificationStatus = VerificationStatus.INSUFFICIENT
    establishes: str = ""
    # WSA-05: provider-supplied lineage for this span, not global document dependence.
    origin_lineage: OriginLineage = Field(default_factory=OriginLineage)

    def is_admissible(self) -> bool:
        """True only when this span is a real passage/record of a successfully fetched page.

        A search title or snippet is never admissible. A structured span is admissible only
        when it names the representation it was located in, so it cannot be fabricated from
        a bare string.
        """
        if not self.document_id or self.retrieval_status != "OK":
            return False
        if self.source_type == EvidenceSource.DOCUMENT_BODY:
            return True
        if self.source_type == EvidenceSource.STRUCTURED:
            return bool(self.representation_id and self.locator_type != "char_offsets")
        return False

    def offsets_are_consistent(self) -> bool:
        if self.start is None or self.end is None:
            return False
        return self.start >= 0 and self.end >= self.start and (self.end - self.start) == len(self.text)


class EvidenceFinding(BaseModel):
    """A proposition-specific verification decision over one document/span (ADR 0022).

    ``deterministic_status`` preserves the hard-constraint decision separately from the
    final ``status``; an LLM refinement may never upgrade a finding whose deterministic
    hard gate failed (Phase 5B closure, ADR 0022).
    """

    id: str = Field(default_factory=lambda: new_id("ef"))
    proposition_id: str = ""
    document_id: str = ""
    span_id: str = ""
    span_hash: str = ""
    proposition_signature: str = ""
    status: VerificationStatus = VerificationStatus.INSUFFICIENT
    deterministic_status: VerificationStatus | None = None
    llm_status: VerificationStatus | None = None
    hard_conflict: bool = False
    hard_reasons: list[str] = Field(default_factory=list)
    admissible: bool = False
    what_it_establishes: str = ""
    verifier: str = "deterministic"      # deterministic | llm
    checks: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    source_url: str = ""
    retrieved_at: str = ""
    content_hash: str = ""
    # WSA-01/02: the typed semantic assessment of this span and the deterministic
    # compatibility decision against the proposition's typed expectations. The assessment is
    # a proposal (LLM or deterministic fallback); ``semantic_compatibility`` is the
    # authority gate. A soft incompatibility is never a hard conflict, so the finding stays
    # resolvable and the Judge may inspect already-retrieved context.
    semantic_assessment: dict[str, Any] = Field(default_factory=dict)
    relation_state: str = ""
    temporal_state: str = ""
    assertion_state: str = ""
    authority_state: str = ""
    semantic_compatibility: str = ""
    semantic_reasons: list[str] = Field(default_factory=list)


class EvidenceAssessment(BaseModel):
    """A typed proposal about what one evidence span actually asserts (WSA-01/02).

    The LLM (or the deterministic fallback when the LLM is unavailable) describes meaning -
    proposition relation, assertion strength, temporal relevance and event/state time. It is
    never a support decision:
    :func:`app.verification.evidence_semantics.assess_compatibility` compares it with the
    proposition's typed expectations and the deterministic hard gates.
    """

    subject_match: bool = False
    # WSA-03: what kind of source material this is. Separate from the evidence's meaning and
    # from the Requirement's authority expectation; combined only by the deterministic gate.
    source_role: SourceRoleAssessment = Field(default_factory=SourceRoleAssessment)
    # WSA-04: the typed relation to the target proposition is the primary meaning signal.
    # ``polarity`` is retained as a derived compatibility view of that relation; it is never
    # the authority and a deterministic fallback must not set it to AFFIRMED by default.
    relation: PropositionRelation = PropositionRelation.UNCERTAIN
    polarity: Literal["AFFIRMED", "NEGATED", "UNKNOWN"] = "UNKNOWN"
    assertion_strength: AssertionStrength = AssertionStrength.UNKNOWN
    temporal_relevance: Literal["CURRENT_STATE", "HISTORICAL", "UNCONSTRAINED", "UNKNOWN"] = "UNKNOWN"
    event_or_state_time: str = ""       # ISO date when the evidence establishes one
    asserted_fact: str = ""
    attribution: str = ""
    caveats: list[str] = Field(default_factory=list)
    source: str = "deterministic"        # deterministic | llm
    hedge_detected: bool = False         # the deterministic fallback saw hedging/attribution
    rationale: str = ""
    # WSA-09: authority-increasing semantic proposals must identify their acquired-source
    # basis. The fallback/deterministic assessment never needs receipts (it cannot escalate).
    grounding: list[SemanticGroundingReceipt] = Field(default_factory=list)


class SourceInspection(BaseModel):
    """A bounded, provenance-preserving Judge inspection of already-retrieved material.

    It references persisted evidence only: no new fetch or search is authorized by this
    type. ``no_new_fetch`` is always True by construction (see
    :mod:`app.verification.source_inspection`). Raw source text is untrusted data and is
    carried in delimited fields; it can never change the Requirement or the hard gates.
    """

    id: str = Field(default_factory=lambda: new_id("insp"))
    finding_id: str = ""
    proposition_id: str = ""
    document_id: str = ""
    representation_id: str = ""
    span_id: str = ""
    source_url: str = ""
    content_hash: str = ""
    publication_date: str | None = None
    retrieved_at: str = ""
    level: int = 2                       # 2 = neighbors/section, 3 = bounded representation
    trigger: str = ""
    section: str = ""
    span_text: str = ""
    before: str = ""
    after: str = ""
    context_chars: int = 0
    representation_loaded: bool = False
    no_new_fetch: bool = True
    source: str = "deterministic"        # deterministic | llm
    resolved_status: str = ""            # SUPPORTED | CONTRADICTED | INSUFFICIENT
    rationale: str = ""
    assessment: dict[str, Any] = Field(default_factory=dict)
# ------------------------------------------------------------------------ verification


class RequirementStatus(str, Enum):
    SATISFIED = "SATISFIED"
    PARTIAL = "PARTIAL"
    UNSATISFIED = "UNSATISFIED"
    BLOCKED = "BLOCKED"


class ArtifactPropositionSupport(BaseModel):
    """Judge-inspected native record fragment, not a Claim or coverage conclusion."""

    proposition_id: str
    record_index: int
    locator: str
    predicate: str
    subject_id: str
    observed_at: str
    response_hash: str


class ArtifactAssessment(BaseModel):
    """Narrow data-quality/provenance/admissibility result owned by the holistic Judge."""

    artifact_id: str
    source_kind: str
    status: Literal["ELIGIBLE", "QUALIFIED", "REJECTED", "LEGACY_UNNORMALIZED"]
    eligible_proposition_ids: list[str] = Field(default_factory=list)
    record_support: list[ArtifactPropositionSupport] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    inspection_ids: list[str] = Field(default_factory=list)
    # Derived artifacts: the governed operations proven for this result's output fields.
    # Empty for every non-derived assessment. This is admissibility data, never a Claim.
    derived_operations: list[str] = Field(default_factory=list)
    # Exact operand fields (columns/cells) the proven output actually depends on.
    derived_operand_fields: list[str] = Field(default_factory=list)
    derived_proofs: list[dict[str, Any]] = Field(default_factory=list)
    derived_requirement_ids: list[str] = Field(default_factory=list)
    reporting_proofs: list[dict[str, Any]] = Field(default_factory=list)
    subject_proofs: list[dict[str, Any]] = Field(default_factory=list)


class RequirementAssessment(BaseModel):
    """Judge-owned Requirement conclusion, normalized from all mandatory propositions."""

    requirement_id: str
    status: RequirementStatus
    authority_status: RequirementAuthority = RequirementAuthority.UNPROVEN
    coverage: CoverageState = CoverageState.NOT_ATTEMPTED
    support_artifact_ids: list[str] = Field(default_factory=list)
    supporting_exports: list[str] = Field(default_factory=list)
    support_refs: list[Reference] = Field(default_factory=list)
    gaps: list[ScopeGap] = Field(default_factory=list)
    coverage_gaps: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    inspection_ids: list[str] = Field(default_factory=list)
    note: str = ""


class GoalStatus(str, Enum):
    COMPLETE = "COMPLETE"
    LIMITED = "LIMITED"
    FAILED = "FAILED"
    WAITING_FOR_USER = "WAITING_FOR_USER"


class ComparisonCellSupport(BaseModel):
    """Typed location of one operand in an admitted scalar result (row 0)."""

    artifact_id: str
    requirement_ids: tuple[str, ...]
    row_index: int
    column: str
    measure_key: str
    value: str


class ComparisonClaimSupport(BaseModel):
    relation: ComparativeRelation
    lhs: ComparisonCellSupport
    rhs: ComparisonCellSupport


class Claim(BaseModel):
    answer_values: list[dict[str, Any]] = Field(default_factory=list)
    comparison_support: ComparisonClaimSupport | None = None
    id: str = Field(default_factory=lambda: new_id("claim"))
    text: str
    support_refs: list[Reference] = Field(default_factory=list)
    confidence: str = "ACCEPTABLE"
    scope: Scope | None = None
    # ADR 0014/0017: how the claim may be presented. SUPPORTED requires support_refs.
    epistemic: EpistemicStatus = EpistemicStatus.SUPPORTED
    requirement_ids: list[str] = Field(default_factory=list)
    # ADR 0021/0022: the answer proposition(s) this claim supports and the span decision.
    proposition_ids: list[str] = Field(default_factory=list)
    verification: VerificationStatus | None = None
    caveat: str = ""


class Clarification(BaseModel):
    question: str
    options: list[str] = Field(default_factory=list)
    goal_id: str
    requirement_id: str | None = None
    need_id: str | None = None
    # Internal backend ownership: never serialized by clarification_view.
    candidate_id: str | None = None
    source_span: str | None = None
    target_digest: str | None = None
    # Backend-provided wording actually quoted in the selected user option.
    confirmed_residue: str = ""
    option_actions: list[Literal["DISCHARGE_RESIDUE", "PRESERVE_RESIDUE", "REPLACE_INTERPRETATION"]] = Field(default_factory=list)
    # Backend-produced typed alternatives, parallel to options; never exposed as
    # a client-editable semantic payload. Only a selected option authorizes one.
    option_replacements: list[dict[str, Any] | None] = Field(default_factory=list)
    confirmation_requested: bool = False


class ConsistencyStatus(str, Enum):
    CONSISTENT = "CONSISTENT"
    DOWNGRADED = "DOWNGRADED"
    UNCERTAIN = "UNCERTAIN"


class StatusConsistency(BaseModel):
    """Auditable result of checking GoalStatus against the rendered answer (ADR 0023)."""

    status: ConsistencyStatus = ConsistencyStatus.CONSISTENT
    original_goal_status: GoalStatus | None = None
    final_goal_status: GoalStatus | None = None
    retracted_proposition_ids: list[str] = Field(default_factory=list)
    method: str = "deterministic"         # deterministic | llm
    rationale: str = ""


class QualifiedEvidence(BaseModel):
    """Relevant evidence the Judge judged useful but not sufficient to assert (WSA-03/present).

    It carries the exact span, provenance, the typed semantic/authority state and a precise
    structural limitation. It is never a Claim and never affects Requirement coverage or
    GoalStatus; the Composer may phrase it but cannot promote or erase the limitation.
    """

    id: str = Field(default_factory=lambda: new_id("qual"))
    proposition_id: str = ""
    requirement_id: str = ""
    finding_id: str = ""
    span_id: str = ""
    document_id: str = ""
    source_url: str = ""
    source_title: str = ""
    publication_date: str | None = None
    retrieved_at: str = ""
    span_text: str = ""
    relation: str = ""
    assertion_strength: str = ""
    temporal_state: str = ""
    authority_need: str = ""
    source_role: dict[str, Any] = Field(default_factory=dict)
    disposition: str = "QUALIFIED_VISIBLE"    # QUALIFIED_VISIBLE (only surfaced disposition)
    kind: str = ""                             # AUTHORITY | SEMANTIC | ASSERTION | TEMPORAL | CONTRADICTED | PARTIAL_OPERAND
    limitation: str = ""
    conflict_group: str = ""


class AnswerProjection(BaseModel):
    goal_id: str
    goal_status: GoalStatus
    claims: list[Claim] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    unresolved_need_ids: list[str] = Field(default_factory=list)
    clarification: Clarification | None = None
    supporting_artifact_ids: list[str] = Field(default_factory=list)
    # ADR 0014/0017: allow a clearly-labelled, non-high-risk background explanation.
    background_ok: bool = False
    background_topic: str = ""
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    # ADR 0021: the answer obligations (a refinement of the Requirement graph) whose
    # coverage is the sole authority behind ``goal_status``. Coverage lives on each
    # proposition; there is no parallel completion structure.
    propositions: list[AnswerProposition] = Field(default_factory=list)
    requirement_assessments: list[RequirementAssessment] = Field(default_factory=list)
    artifact_assessments: list[ArtifactAssessment] = Field(default_factory=list)
    # Legacy deserialization only. New presentation consistency lives on TurnResult;
    # no post-Judge writer may attach it to this governed snapshot.
    consistency: StatusConsistency | None = None
    # WSA Judge drill-down: bounded inspections of already-retrieved material that resolved
    # or preserved an otherwise-ambiguous finding. Provenance stays typed; no new fetch.
    source_inspections: list[SourceInspection] = Field(default_factory=list)
    # WSA-03/presentation: relevant evidence the Judge judged useful but *not* sufficient to
    # assert. It is user-visible only with its structural limitation and never becomes a
    # Claim/SATISFIED/COMPLETE. Presentation never launders authority.
    qualified_evidence: list["QualifiedEvidence"] = Field(default_factory=list)
    # WSA-05: response/evidence-local governance. Never a Claim or coverage predicate.
    evidence_origins: list[PropositionOrigins] = Field(default_factory=list)


# ------------------------------------------------------------------------ conversation


class TurnType(str, Enum):
    INDEPENDENT_GOAL = "INDEPENDENT_GOAL"
    FOLLOW_UP = "FOLLOW_UP"
    CLARIFICATION_ANSWER = "CLARIFICATION_ANSWER"
    CONSTRAINT_REVISION = "CONSTRAINT_REVISION"


class ConversationMessage(BaseModel):
    role: Literal["user", "assistant"]
    text: str
    at: datetime = Field(default_factory=utc_now)
    goal_id: str | None = None


class ConversationState(BaseModel):
    session_id: str
    messages: list[ConversationMessage] = Field(default_factory=list)
    active_goal_id: str | None = None
    goals: list[Goal] = Field(default_factory=list)
    pending_clarification: Clarification | None = None
    recent_entities: list[dict[str, Any]] = Field(default_factory=list)
    accepted_context: dict[str, Any] = Field(default_factory=dict)

    def goal(self, goal_id: str) -> Goal | None:
        for goal in self.goals:
            if goal.id == goal_id:
                return goal
        return None

    def active_goal(self) -> Goal | None:
        return self.goal(self.active_goal_id) if self.active_goal_id else None
