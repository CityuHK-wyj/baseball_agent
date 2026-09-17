"""Durable, redacted event journal.

Every important runtime transition is appended here so a failed action is diagnosable and
the ``--trace`` output is a *projection of the event model*, not an independent partial
debug implementation. Events never contain hidden chain-of-thought, prompts, credentials
or unrestricted payloads.
"""

from __future__ import annotations

import re
from typing import Iterable

from app.models.artifact_runtime import RuntimeEvent, utcnow

_SECRET = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer|password|secret|token)\s*[:=]\s*\S+")
_MAX_DETAIL = 400


def redact(text: str, limit: int = _MAX_DETAIL) -> str:
    """Bound and redact a safe summary string before it is persisted or renderable."""
    if not text:
        return ""
    cleaned = _SECRET.sub(r"\1=<redacted>", str(text))
    return cleaned[:limit]


class EventJournal:
    def __init__(self) -> None:
        self._events: list[RuntimeEvent] = []
        self._counter = 0

    def emit(self, event_type: str, *, detail: str = "", data: dict | None = None,
             **context) -> RuntimeEvent:
        self._counter += 1
        event = RuntimeEvent(
            event_id=f"event-{self._counter}", event_type=event_type,
            detail=redact(detail), data=_safe_data(data), created_at=utcnow(), **context)
        self._events.append(event)
        return event

    def extend(self, events: Iterable[RuntimeEvent]) -> None:
        for event in events:
            self._events.append(event)
            self._counter = max(self._counter, _event_number(event.event_id))

    def all(self) -> tuple[RuntimeEvent, ...]:
        return tuple(self._events)

    def of_type(self, *event_types: str) -> tuple[RuntimeEvent, ...]:
        wanted = set(event_types)
        return tuple(item for item in self._events if item.event_type in wanted)

    def __len__(self) -> int:
        return len(self._events)


def _event_number(event_id: str) -> int:
    try:
        return int(event_id.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return 0


def _safe_data(data: dict | None) -> dict:
    if not data:
        return {}
    safe: dict = {}
    for key, value in data.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = redact(value) if isinstance(value, str) else value
        elif isinstance(value, (list, tuple)):
            safe[key] = [redact(item) if isinstance(item, str) else item for item in value][:32]
        else:
            continue
    return safe
