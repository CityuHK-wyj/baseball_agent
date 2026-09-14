"""Planning, routing, execution and report contracts.

``AgentTask`` is a semantic plan and carries no execution state. Runtime outcome
lives in ``TaskExecution``; each real try lives in ``TaskAttempt``.
"""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.models.artifacts import ArtifactContract, utcnow
from app.models.contracts import Name


class AgentTask(ArtifactContract):
    task_id: Name
    objective_ref: Name
    requirement_refs: tuple[Name, ...] = Field(min_length=1)
    description: Name
    source_preference: Name | None = None
    depends_on: tuple[Name, ...] = ()


class TaskAttempt(ArtifactContract):
    attempt_id: Name
    execution_ref: Name
    tool: Name
    status: Literal["SUCCEEDED", "EMPTY", "FAILED", "INTERRUPTED"]
    retryable: bool = False
    error_code: str = ""
    artifact_ref: Name | None = None
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime = Field(default_factory=utcnow)


class TaskExecution(ArtifactContract):
    execution_id: Name
    task_ref: Name
    status: Literal["PENDING", "SUCCEEDED", "EMPTY", "FAILED", "BLOCKED", "INTERRUPTED"]
    attempt_refs: tuple[Name, ...] = ()
    artifact_refs: tuple[Name, ...] = ()
    version: int = Field(default=0, ge=0)


class PlanningDecision(ArtifactContract):
    decision_id: Name
    objective_ref: Name
    kind: Literal["PLAN", "REPLAN", "STOP_PLANNING"]
    tasks: tuple[AgentTask, ...] = ()
    planner_terminal: bool = False
    terminal_reason: str = ""
    rationale: Name
    round: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def terminal_contract(self):
        if (self.kind == "STOP_PLANNING") != self.planner_terminal:
            raise ValueError("STOP_PLANNING and planner_terminal must agree")
        if self.planner_terminal and self.tasks:
            raise ValueError("A terminal planning decision carries no new tasks")
        if self.planner_terminal and not self.terminal_reason:
            raise ValueError("A terminal planning decision requires a reason")
        return self


class RoutingDecision(ArtifactContract):
    decision_id: Name
    task_ref: Name
    selected_tool: Name | None = None
    rationale: Name
    fallbacks: tuple[Name, ...] = ()
    policy_notes: tuple[Name, ...] = ()


StopReason = Literal[
    "COMPLETE",
    "MAX_ROUNDS",
    "BUDGET_EXHAUSTED",
    "NO_RECOVERABLE_PATH",
    "NO_PROGRESS",
    "POLICY_BLOCKED",
    "SOURCE_UNAVAILABLE",
]
