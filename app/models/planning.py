"""Planning, routing, execution and report contracts.

``AgentTask`` is a semantic plan and carries no execution state. Runtime outcome
lives in ``TaskExecution``; each real try lives in ``TaskAttempt``.
"""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.models.artifacts import ArtifactContract, utcnow
from app.models.contracts import Name
from app.models.understanding import MAX_FREE_TEXT


class SupportingNeed(ArtifactContract):
    """A Planner-created information need that did not exist at objective construction.

    The Planner may discover mid-run that it needs a supporting requirement (for example
    web research to resolve an unknown nickname). A need is a *request*: the Orchestrator
    materializes it into an ``ArtifactRequirement`` with ``PLANNER_ADDED`` origin, so the
    immutable initial baseline is never edited.
    """

    need_id: Name
    objective_ref: Name
    description: Name
    artifact_type: Literal["TABLE", "EVIDENCE", "FEATURE"] = "EVIDENCE"
    data_keys: tuple[Name, ...] = Field(min_length=1)
    parent_ref: Name | None = None
    task_type: Literal[
        "GENERIC", "LOCAL_ANALYTICS", "WEB_RESEARCH", "ENTITY_RESOLUTION",
        "KNOWLEDGE", "COMPUTATION", "SYNTHESIS",
    ] = "WEB_RESEARCH"
    objective: str = Field(default="", max_length=MAX_FREE_TEXT)
    instructions: str = Field(default="", max_length=MAX_FREE_TEXT)
    expected_evidence: str = Field(default="", max_length=MAX_FREE_TEXT)
    search_hints: tuple[str, ...] = ()


class AgentTask(ArtifactContract):
    """A semantic plan of work. It carries no execution state.

    ``requirement_refs`` binds the task to the immutable requirement baseline. The
    free-form fields (``objective``, ``instructions``, ``expected_evidence``) and
    ``search_hints`` let the Planner express a genuinely open-world task without being
    forced into fixed enum/value fields. They are interpretation aids for tools; they are
    never SQL, never physical identifiers and never executable.
    """

    task_id: Name
    objective_ref: Name
    requirement_refs: tuple[Name, ...] = Field(min_length=1)
    description: Name
    source_preference: Name | None = None
    depends_on: tuple[Name, ...] = ()
    task_type: Literal[
        "GENERIC", "LOCAL_ANALYTICS", "WEB_RESEARCH", "ENTITY_RESOLUTION",
        "KNOWLEDGE", "COMPUTATION", "SYNTHESIS",
    ] = "GENERIC"
    objective: str = Field(default="", max_length=MAX_FREE_TEXT)
    instructions: str = Field(default="", max_length=MAX_FREE_TEXT)
    expected_evidence: str = Field(default="", max_length=MAX_FREE_TEXT)
    search_hints: tuple[str, ...] = ()


class TaskAttempt(ArtifactContract):
    attempt_id: Name
    execution_ref: Name
    tool: Name
    status: Literal["SUCCEEDED", "EMPTY", "FAILED", "INTERRUPTED"]
    retryable: bool = False
    error_code: str = ""
    error_type: Literal["NONE", "POLICY_REJECTED", "NO_DATA", "TECHNICAL_FAILURE"] = "NONE"
    safe_error_summary: str = ""
    artifact_ref: Name | None = None
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime = Field(default_factory=utcnow)


class TaskExecution(ArtifactContract):
    execution_id: Name
    task_ref: Name
    status: Literal["PENDING", "RUNNING", "SUCCEEDED", "EMPTY", "FAILED", "BLOCKED", "INTERRUPTED"]
    attempt_refs: tuple[Name, ...] = ()
    artifact_refs: tuple[Name, ...] = ()
    version: int = Field(default=0, ge=0)


class PlanningDecision(ArtifactContract):
    decision_id: Name
    objective_ref: Name
    kind: Literal["PLAN", "REPLAN", "STOP_PLANNING"]
    tasks: tuple[AgentTask, ...] = ()
    supporting_needs: tuple[SupportingNeed, ...] = ()
    planner_terminal: bool = False
    terminal_reason: str = ""
    rationale: Name
    round: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def terminal_contract(self):
        if (self.kind == "STOP_PLANNING") != self.planner_terminal:
            raise ValueError("STOP_PLANNING and planner_terminal must agree")
        if self.planner_terminal and (self.tasks or self.supporting_needs):
            raise ValueError("A terminal planning decision carries no new work")
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
    # Open-world recovery signals. SQL compilation failure and unknown local semantics
    # return control to the Planner instead of failing the objective.
    "UNSUPPORTED_LOCAL_ANALYTICS",
    "MISSING_SQL_SEMANTICS",
    "UNKNOWN_LOCAL_METRIC",
    "INSUFFICIENT_LOCAL_COVERAGE",
    "WEB_RECOVERY",
    "CLARIFICATION_REQUIRED",
]
