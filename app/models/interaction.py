"""Persisted clarification coordinate, kept separate from execution state."""

from typing import Literal

from app.models.artifacts import ArtifactContract
from app.models.clarification import ClarificationRequest
from app.models.contracts import AnalysisObjective, Constraint, Name


class PermissionRequest(ArtifactContract):
    permission_id: Name
    objective_ref: Name
    tool: Name
    source_kind: Name
    cost: Literal["PAID", "HIGH"]
    action: Name = "EXECUTE"
    reason: str = ""


class PermissionAnswer(ArtifactContract):
    permission_ref: Name
    approved: bool


class InteractionRecord(ArtifactContract):
    run_id: Name
    raw_query: Name
    objectives: tuple[AnalysisObjective, ...]
    clarification: ClarificationRequest | None = None
    permission: PermissionRequest | None = None
    status: Literal["WAITING_FOR_USER", "CONFIRMED", "APPROVED", "REJECTED"] = "WAITING_FOR_USER"
    confirmed_constraints: tuple[Constraint, ...] = ()
