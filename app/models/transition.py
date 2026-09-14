"""Explicit state transitions.

State domains never mutate silently: a change carries its reason, trigger and version so
it can be audited and replayed. Transitions are the unit the Orchestrator reviews and
applies across domains (D044).
"""

from datetime import datetime
from typing import Literal

from pydantic import model_validator

from app.models.artifacts import ArtifactContract, utcnow
from app.models.contracts import Name

StateDomain = Literal[
    "QUERY", "OBJECTIVE", "REQUIREMENT", "PLANNING", "ROUTING", "EXECUTION",
    "ARTIFACT", "ASSESSMENT", "INTERACTION", "PERMISSION", "BUDGET",
]


class StateTransition(ArtifactContract):
    domain: StateDomain
    subject_ref: Name
    from_status: str
    to_status: str
    reason: str
    trigger: str = ""
    version: int = 0
    created_at: datetime = utcnow()

    @model_validator(mode="after")
    def must_change_something(self):
        if self.from_status == self.to_status:
            raise ValueError("A transition must change status")
        return self
