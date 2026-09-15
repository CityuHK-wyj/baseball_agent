"""Persisted clarification coordinate, kept separate from execution state."""

from typing import Literal
from datetime import datetime, timedelta, timezone
from pydantic import AwareDatetime, Field

from app.models.artifacts import ArtifactContract
from app.models.clarification import ClarificationRequest
from app.models.contracts import AnalysisObjective, CategoryConstraint, Constraint, Name


class PermissionRequest(ArtifactContract):
    permission_id: Name
    objective_ref: Name
    tool: Name
    source_kind: Name
    cost: Literal["PAID", "HIGH"]
    action: Name = "EXECUTE"
    reason: str = ""
    expires_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(minutes=15))


class PermissionAnswer(ArtifactContract):
    permission_ref: Name
    approved: bool


class ConstraintRevisionRequest(ArtifactContract):
    revision_id: Name
    objective_ref: Name
    original: CategoryConstraint
    proposed: CategoryConstraint
    reason: str
    expires_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(minutes=15))


class ConstraintRevisionAnswer(ArtifactContract):
    revision_ref: Name
    accepted: bool


class InteractionRecord(ArtifactContract):
    run_id: Name
    raw_query: Name
    objectives: tuple[AnalysisObjective, ...]
    clarification: ClarificationRequest | None = None
    permission: PermissionRequest | None = None
    constraint_revision: ConstraintRevisionRequest | None = None
    status: Literal["WAITING_FOR_USER", "CONFIRMED", "APPROVED", "REJECTED", "EXPIRED"] = "WAITING_FOR_USER"
    confirmed_constraints: tuple[Constraint, ...] = ()
