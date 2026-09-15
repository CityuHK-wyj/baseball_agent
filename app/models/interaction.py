"""Persisted clarification coordinate, kept separate from execution state."""

from typing import Literal

from app.models.artifacts import ArtifactContract
from app.models.clarification import ClarificationRequest
from app.models.contracts import AnalysisObjective, Constraint, Name


class InteractionRecord(ArtifactContract):
    run_id: Name
    raw_query: Name
    objectives: tuple[AnalysisObjective, ...]
    clarification: ClarificationRequest
    status: Literal["WAITING_FOR_USER", "CONFIRMED"] = "WAITING_FOR_USER"
    confirmed_constraints: tuple[Constraint, ...] = ()
