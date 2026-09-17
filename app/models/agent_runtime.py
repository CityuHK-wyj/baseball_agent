"""Runtime models for the LLM-first conversational agent.

Cognition (understanding, planning, research, composing) is expressed with a mix of
free-form text and *hints*. Hints are never trusted executable identifiers: they are
compiled later by the strict action boundary. Conversation state is intentionally a
mutable in-memory object owned by the application service, not part of the frozen
artifact graph.
"""

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.artifacts import ArtifactContract

# Terminal / lifecycle states. ``WAITING_FOR_USER`` is a first-class state, never FAILED.
RunStatus = Literal["PENDING", "RUNNING", "WAITING_FOR_USER", "COMPLETE", "LIMITED", "FAILED"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# -- cognition outputs -------------------------------------------------------


class ClarificationDecision(ArtifactContract):
    """A question the agent genuinely needs the user to answer to continue."""

    question: str = ""
    reason: str = ""
    options: tuple[str, ...] = ()
    kind: str = "GENERAL"

    @property
    def required(self) -> bool:
        return bool(self.question.strip())


class LocalMetricHint(ArtifactContract):
    """Free-form analytical intent the Planner wants executed locally.

    The values are *hints*. The strict SQL compiler validates the metric against the
    trusted registry and drops/repairs anything outside the closed vocabulary. The LLM
    never emits SQL or physical identifiers.
    """

    metric: str = ""
    aggregation: str = "AVG"
    direction: str = "DESC"
    limit: int = 10
    entity_names: tuple[str, ...] = ()
    entity_ids: tuple[str, ...] = ()
    start: str | None = None
    end: str | None = None
    pitch_family: str | None = None
    location_definition: str | None = None
    game_types: tuple[str, ...] = ()
    event_population: str | None = None
    min_batted_balls: int | None = None
    note: str = ""


class CognitionPlan(ArtifactContract):
    """One open-world plan. Free-form understanding plus actionable hints."""

    user_goal: str = ""
    understanding: str = ""
    analysis_strategy: str = ""
    assumptions: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    time_hints: tuple[str, ...] = ()
    needs_knowledge: bool = False
    knowledge_queries: tuple[str, ...] = ()
    research_queries: tuple[str, ...] = ()
    needs_batting_stats: bool = False
    batting_year: int | None = None
    batting_team: str | None = None
    batting_entity_names: tuple[str, ...] = ()
    batting_metrics: tuple[str, ...] = ()
    needs_pitching_stats: bool = False
    pitching_year: int | None = None
    pitching_entity_names: tuple[str, ...] = ()
    local_metrics: tuple[LocalMetricHint, ...] = ()
    clarification: ClarificationDecision | None = None
    direct_answer: str = ""
    source: str = "llm"

    @property
    def has_work(self) -> bool:
        return bool(self.needs_knowledge or self.knowledge_queries or self.research_queries
                    or self.needs_batting_stats or self.local_metrics)


class EvidenceItem(ArtifactContract):
    """One piece of usable evidence for the current turn, with provenance."""

    kind: Literal["KNOWLEDGE", "WEB", "BATTING_STATS", "PITCHING_STATS",
                  "LOCAL_ANALYTICS", "NOTE"]
    summary: str
    source: str = ""
    reference: str = ""
    text: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    retrieved_at: datetime = Field(default_factory=utcnow)
    accepted: bool = True


class AgentTrace(ArtifactContract):
    """Structured, reviewable runtime trace. Never hidden model chain-of-thought."""

    raw_query: str = ""
    understanding: str = ""
    plan: CognitionPlan | None = None
    steps: tuple[str, ...] = ()
    tool_calls: tuple[str, ...] = ()
    sql_requests: tuple[str, ...] = ()
    replans: int = 0
    status: str = ""
    evidence: tuple[str, ...] = ()
    answer: str = ""


# -- conversation state ------------------------------------------------------


class AgentMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "agent", "system"]
    text: str
    created_at: datetime = Field(default_factory=utcnow)


class PendingClarification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clarification_id: str
    question: str
    options: tuple[str, ...] = ()
    reason: str = ""
    kind: str = "GENERAL"


class Conversation(BaseModel):
    """Mutable in-memory conversation session."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    conversation_id: str
    messages: list[AgentMessage] = Field(default_factory=list)
    status: RunStatus = "PENDING"
    pending_clarification: PendingClarification | None = None
    recent_entities: list[str] = Field(default_factory=list)
    accepted_context: list[str] = Field(default_factory=list)
    last_goal: str = ""
    last_understanding: str = ""
    current_run_id: str | None = None
    turns: int = 0
