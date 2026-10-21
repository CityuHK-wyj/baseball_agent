"""RuntimeContext: system time, goal, conversation, execution state.

LLM memory is never VERIFIED evidence. User statements enter as UNVERIFIED assertions.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

from app.contracts.models import (
    Artifact,
    ConversationState,
    Export,
    Goal,
    Reference,
    UserRequirement,
)


class SystemClock:
    """Injectable clock; production uses the real system time."""

    def now(self) -> dt.datetime:
        return dt.datetime.now(dt.timezone.utc)

    def today(self) -> dt.date:
        return self.now().date()


@dataclass
class ExecutionContext:
    goal: Goal
    conversation: ConversationState
    artifacts: list[Artifact] = field(default_factory=list)
    config: Any = None
    llm: Any = None
    trace: Any = None
    clock: SystemClock = field(default_factory=SystemClock)
    assumptions: list[str] = field(default_factory=list)
    # Phase 5B observability: optional full-fidelity diagnostics and latency spans.
    diagnostics: Any = None
    latency: Any = None

    def find_export(self, export_id: str) -> Export | None:
        for artifact in self.artifacts:
            for export in artifact.exports:
                if export.id == export_id:
                    return export
        return None

    def artifact_for_export(self, export_id: str) -> Artifact | None:
        for artifact in self.artifacts:
            for export in artifact.exports:
                if export.id == export_id:
                    return artifact
        return None

    def artifacts_for_need(self, need_id: str) -> list[Artifact]:
        return [a for a in self.artifacts if a.need_id == need_id]

    def requirements(self) -> list[UserRequirement]:
        return list(self.goal.requirements)

    def reference_for_user_message(self) -> Reference:
        return Reference(ref_type="USER_MESSAGE", ref_id=self.goal.id, source="user", span=self.goal.raw_query)
