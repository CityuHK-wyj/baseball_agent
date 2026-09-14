"""Internal finalization record and its user-facing projection.

``CompletionReport`` is the internal summary of a run. ``ResponsePackage`` is the
projection consumed by the Response Agent: accepted products only, never failed
attempts, rejected evidence, old plans or routing history.
"""

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.models.artifacts import ArtifactContract, QualityLevel, utcnow
from app.models.contracts import Name
from app.models.planning import StopReason


class RequirementCompletion(ArtifactContract):
    requirement_ref: Name
    criticality: Literal["CORE", "OPTIONAL"]
    origin: Literal["INITIAL", "PLANNER_ADDED"]
    status: Literal["PENDING", "PARTIAL", "SATISFIED", "UNSATISFIED"]


class ExecutionSummary(ArtifactContract):
    rounds: int = 0
    tasks_planned: int = 0
    tasks_succeeded: int = 0
    tasks_empty: int = 0
    tasks_failed: int = 0
    attempts: int = 0
    budget_spent: int = 0


class CompletionReport(ArtifactContract):
    run_id: Name
    query: Name
    confirmed_intent: str = ""
    objective_ref: Name
    objective_status: Literal["PENDING", "IN_PROGRESS", "COMPLETE", "LIMITED", "FAILED"]
    requirement_completion: tuple[RequirementCompletion, ...] = ()
    final_artifact_refs: tuple[Name, ...] = ()
    limitations: tuple[Name, ...] = ()
    unresolved_gaps: tuple[Name, ...] = ()
    plan_revisions: int = 0
    execution_summary: ExecutionSummary = Field(default_factory=ExecutionSummary)
    user_decisions: tuple[Name, ...] = ()
    stop_reason: StopReason
    created_at: datetime = Field(default_factory=utcnow)


class AcceptedEvidence(ArtifactContract):
    artifact_ref: Name
    requirement_ref: Name
    level: QualityLevel
    summary: Name
    payload_ref: Name
    source_kind: Literal["POSTGRES", "PARQUET", "WEB", "FEATURE", "SYNTHETIC"]
    source: Name


class ResponsePackage(ArtifactContract):
    """The only object the Response Agent may read for an analytical run."""

    run_id: Name
    objective_ref: Name
    objective_status: Literal["PENDING", "IN_PROGRESS", "COMPLETE", "LIMITED", "FAILED"]
    accepted_evidence: tuple[AcceptedEvidence, ...] = ()
    critical_shared_knowledge: tuple[Name, ...] = ()
    objective_result: str = ""
    limitations: tuple[Name, ...] = ()
    optional_gaps: tuple[Name, ...] = ()
    unresolved_items: tuple[Name, ...] = ()
    response_preferences: tuple[Name, ...] = ()
    created_at: datetime = Field(default_factory=utcnow)
