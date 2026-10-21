"""Public wire contract. Internal Goal/Artifact models never cross the HTTP boundary."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class ConversationCreated(BaseModel):
    conversation_id: str


class SendMessage(BaseModel):
    text: str = Field(min_length=1, max_length=12000)


class RunAccepted(BaseModel):
    message_id: str
    run_id: str


class ClarificationResponse(BaseModel):
    selected_option_ids: list[str] = Field(default_factory=list, max_length=1)
    free_text: str = Field(default="", max_length=12000)


class ClarificationOption(BaseModel):
    option_id: str
    label: str
    description: str = ""


class ClarificationRequest(BaseModel):
    id: str
    conversation_id: str
    run_id: str
    goal_id: str
    question: str
    reason: str
    blocking_requirement_ids: list[str]
    options: list[ClarificationOption]
    allow_free_text: bool = True
    status: Literal["pending", "resolved"] = "pending"


class ColumnSpec(BaseModel):
    id: str
    label: str
    kind: Literal["string", "number", "boolean", "date"]
    unit: str | None = None
    format: str | None = None


class TablePayload(BaseModel):
    columns: list[ColumnSpec]
    rows: list[dict[str, str | int | float | bool | None]]
    default_sort: list[str] = Field(default_factory=list)
    total_rows: int | None = None
    pagination: Literal["client", "server"] = "client"


class ChartPayload(BaseModel):
    kind: Literal["bar", "line", "scatter"]
    x: str
    y: str
    rows: list[dict[str, str | int | float | bool | None]]


class PresentationArtifact(BaseModel):
    id: str
    type: Literal["text", "metric", "table", "chart", "evidence", "warning"]
    title: str
    description: str = ""
    status: Literal["accepted", "partial"] = "accepted"
    evidence_refs: list[str] = Field(default_factory=list)
    scope: dict[str, str | int | None] = Field(default_factory=dict)
    calculation: list[str] = Field(default_factory=list)
    payload: TablePayload | ChartPayload | dict[str, Any]


class RequirementView(BaseModel):
    id: str
    statement: str
    coverage: str
    required: bool


class AnswerIssue(BaseModel):
    code: str
    category: Literal["provider", "validation", "unsupported", "semantic", "run"]
    message: str


class AnswerView(BaseModel):
    goal_id: str
    status: Literal["COMPLETE", "LIMITED", "PARTIAL", "WAITING_FOR_USER", "FAILED"]
    text: str
    limitations: list[str]
    issues: list[AnswerIssue] = Field(default_factory=list)
    requirements: list[RequirementView]
    artifacts: list[PresentationArtifact]


class StartedPayload(BaseModel):
    status: Literal["running"] = "running"


class AcceptedPayload(BaseModel):
    text: str


class StatusPayload(BaseModel):
    label: str


class DeltaPayload(BaseModel):
    text: str


class ClarificationPayload(BaseModel):
    clarification: ClarificationRequest


class ResolvedPayload(BaseModel):
    clarification_id: str


class ArtifactPayload(BaseModel):
    artifact: PresentationArtifact
    goal_id: str


class AnswerPayload(BaseModel):
    answer: AnswerView


class FailurePayload(BaseModel):
    code: str
    category: Literal["transport", "provider", "validation", "unsupported", "semantic", "run"]
    message: str


class _Event(BaseModel):
    version: Literal[1] = 1
    id: str
    conversation_id: str
    run_id: str
    message_id: str
    timestamp: datetime


class RunStarted(_Event):
    type: Literal["run.started"] = "run.started"
    payload: StartedPayload


class MessageAccepted(_Event):
    type: Literal["message.accepted"] = "message.accepted"
    payload: AcceptedPayload


class AgentStatus(_Event):
    type: Literal["agent.status"] = "agent.status"
    payload: StatusPayload


class AssistantDelta(_Event):
    type: Literal["assistant.delta"] = "assistant.delta"
    payload: DeltaPayload


class ClarificationRequired(_Event):
    type: Literal["clarification.required"] = "clarification.required"
    payload: ClarificationPayload


class ClarificationResolved(_Event):
    type: Literal["clarification.resolved"] = "clarification.resolved"
    payload: ResolvedPayload


class ArtifactCreated(_Event):
    type: Literal["artifact.created"] = "artifact.created"
    payload: ArtifactPayload


class AnswerPartial(_Event):
    type: Literal["answer.partial"] = "answer.partial"
    payload: AnswerPayload


class AnswerCompleted(_Event):
    type: Literal["answer.completed"] = "answer.completed"
    payload: AnswerPayload


class RunFailed(_Event):
    type: Literal["run.failed"] = "run.failed"
    payload: FailurePayload


AgentEvent = Annotated[RunStarted | MessageAccepted | AgentStatus | AssistantDelta |
                       ClarificationRequired | ClarificationResolved | ArtifactCreated |
                       AnswerPartial | AnswerCompleted | RunFailed, Field(discriminator="type")]


class ConversationSnapshot(BaseModel):
    conversation_id: str
    messages: list[dict[str, str]]
    pending_clarification: ClarificationRequest | None = None
    events: list[AgentEvent] = Field(default_factory=list)


class APIError(BaseModel):
    code: str
    category: str
    message: str
