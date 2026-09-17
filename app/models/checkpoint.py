"""Checkpoint: a consistent recovery coordinate, not a full state dump.

A checkpoint references independently persisted state versions and work ids. The
domain state, artifacts and execution history live in their own stores.
"""

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.models.artifacts import ArtifactContract, utcnow
from app.models.contracts import Name

RecoveryPosition = Literal[
    "RUN_STARTED",
    "PLAN_ACCEPTED",
    "ARTIFACT_ASSESSED",
    "WAITING_FOR_USER",
    "PLANNER_TERMINAL",
    "OBJECTIVE_TERMINAL",
    "FINALIZATION",
]

TERMINAL_POSITIONS: frozenset[str] = frozenset(
    {"PLANNER_TERMINAL", "OBJECTIVE_TERMINAL", "FINALIZATION"})


class Checkpoint(ArtifactContract):
    checkpoint_id: Name
    run_id: Name
    recovery_position: RecoveryPosition
    state_version_refs: tuple[Name, ...] = ()
    active_work_refs: tuple[Name, ...] = ()
    pending_request_refs: tuple[Name, ...] = ()
    terminal_condition: tuple[tuple[Name, ...], tuple[Name, ...]] | None = None
    created_at: datetime = Field(default_factory=utcnow)
