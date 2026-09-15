"""Structured run metrics with mandatory redaction.

Every free-text field passes through ``redact_secrets`` before it is stored, so a
credential cannot reach a log or metric sink.
"""

from typing import Literal

from pydantic import Field

from app.models.artifacts import ArtifactContract, utcnow
from app.models.contracts import Name
from app.tools.results import redact_secrets

EventType = Literal["OBJECTIVE", "PLAN", "ROUTE", "TASK", "ATTEMPT", "ARTIFACT",
                    "ASSESSMENT", "LLM", "FINALIZATION"]


class RunEvent(ArtifactContract):
    run_id: Name
    event_type: EventType
    subject_ref: str = ""
    agent: str = ""
    status: str = ""
    duration_ms: int | None = None
    retry_count: int = 0
    replan_count: int = 0
    source: str = ""
    tool: str = ""
    tokens: int = 0
    cost_usd: float = 0.0
    message: str = ""


class RunMetrics:
    """Collects redacted events for one run. Not an agent; pure bookkeeping."""

    def __init__(self, run_id: str, secrets: tuple[str, ...] = ()) -> None:
        self._secrets = tuple(secrets)
        self._run_id = redact_secrets(run_id, self._secrets)
        self._events: list[RunEvent] = []

    def record(self, event_type: EventType, message: str = "", **fields) -> RunEvent:
        safe_fields = {
            key: redact_secrets(value, self._secrets) if isinstance(value, str) else value
            for key, value in fields.items()
        }
        event = RunEvent(run_id=self._run_id, event_type=event_type,
                         message=redact_secrets(message, self._secrets), **safe_fields)
        self._events.append(event)
        return event

    def events(self) -> tuple[RunEvent, ...]:
        return tuple(self._events)

    def count(self, event_type: EventType) -> int:
        return sum(1 for event in self._events if event.event_type == event_type)
