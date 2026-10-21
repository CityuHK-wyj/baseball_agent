"""Minimal source contract (M1-T4, ADR 0026, SPEC §13.1).

``SourceDescriptor`` declares what a source can do; ``SearchProvider`` extends the accepted
``SearchBackend`` surface. No Planner-visible capability is added and no ranking consumes
``trust_weight`` in M1 — the descriptor is metadata plus the M2 contract seam.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

_ALLOWED_KINDS = {"BROAD_SEARCH", "PUBLISHER_FEEDS", "ENCYCLOPEDIA", "REFERENCE", "STRUCTURED"}
_ALLOWED_AUTH = {"none", "api_key"}


@dataclass(frozen=True)
class SourceDescriptor:
    """Declarative source capabilities.

    ``trust_weight`` is monotone: **larger means more trusted** (1.00 official,
    0.85 encyclopedia/reference, 0.55 publisher news, 0.25 open web, 0.00 discovery-only).
    """

    source_id: str
    kind: str
    discovery: bool
    full_body: bool
    structured: bool
    historical: bool
    current: bool
    auth: str = "none"
    trust_weight: float = 0.25
    latency_class: str = "medium"
    rate_limit_per_min: int | None = None

    def __post_init__(self) -> None:
        if not self.source_id:
            raise ValueError("source_id must be non-empty")
        if self.kind not in _ALLOWED_KINDS:
            raise ValueError(f"unknown source kind '{self.kind}'")
        if self.auth not in _ALLOWED_AUTH:
            raise ValueError(f"unknown auth mode '{self.auth}'")
        if not 0.0 <= float(self.trust_weight) <= 1.0:
            raise ValueError("trust_weight must be within [0.0, 1.0] (larger = more trusted)")
        if self.rate_limit_per_min is not None and self.rate_limit_per_min <= 0:
            raise ValueError("rate_limit_per_min must be positive when set")


@runtime_checkable
class SearchProvider(Protocol):
    """A source that can discover fetchable URLs. Extends ``SearchBackend`` semantics."""

    descriptor: SourceDescriptor

    def search(self, query: str, *, limit: int = 5, language: str = "en") -> list[Any]: ...


def descriptor_to_dict(descriptor: SourceDescriptor) -> dict[str, Any]:
    return {
        "source_id": descriptor.source_id,
        "kind": descriptor.kind,
        "discovery": descriptor.discovery,
        "full_body": descriptor.full_body,
        "structured": descriptor.structured,
        "historical": descriptor.historical,
        "current": descriptor.current,
        "auth": descriptor.auth,
        "trust_weight": descriptor.trust_weight,
        "latency_class": descriptor.latency_class,
        "rate_limit_per_min": descriptor.rate_limit_per_min,
    }
