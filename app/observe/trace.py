"""Developer trace: bounded observability over the same production runtime.

The trace records decisions, bindings, tool outcomes, artifacts and timings. It never
records prompts, chain of thought, credentials or hidden provider payloads.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from app.observe.diagnostics import _sanitize


@dataclass
class TraceEvent:
    stage: str
    summary: str
    data: dict[str, Any] = field(default_factory=dict)


class Trace:
    LEVELS = {"off": 0, "summary": 1, "normal": 2, "verbose": 3}

    def __init__(self, level: str = "summary"):
        self.level = level if level in self.LEVELS else "summary"
        self.events: list[TraceEvent] = []

    @property
    def enabled(self) -> bool:
        return self.LEVELS[self.level] > 0

    def record(self, stage: str, summary: str, **data: Any) -> None:
        if not self.enabled:
            return
        self.events.append(TraceEvent(stage, "<operational event>", _safe(data)))

    def render(self) -> str:
        if not self.events:
            return "(no trace events)"
        lines = []
        for event in self.events:
            lines.append(f"[{event.stage}] {event.summary}")
            if self.LEVELS[self.level] >= 2 and event.data:
                for key, value in event.data.items():
                    lines.append(f"    {key}: {_short(value)}")
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(
            [{"stage": e.stage, "summary": e.summary, "data": e.data} for e in self.events],
            ensure_ascii=False,
            indent=2,
        )

    def stage_summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for event in self.events:
            counts[event.stage] = counts.get(event.stage, 0) + 1
        return counts


def _short(value: Any, limit: int = 400) -> str:
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…"
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _safe(data: dict[str, Any]) -> dict[str, Any]:
    """Redact anything that looks like a secret before it can be traced."""
    return _sanitize(data, 4000)
