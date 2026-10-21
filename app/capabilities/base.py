"""Capability contract: a capability is a plain callable behind a registry entry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from app.contracts.models import Artifact, ToolOutcome, ToolRequest


@dataclass
class CapabilityResult:
    artifact: Artifact | None
    outcome: ToolOutcome
    extra_artifacts: list[Artifact] = field(default_factory=list)


# Two domain acquisition/execution families are the Planner's primary choice. Every
# capability is classified into one family or as SUPPORTING infrastructure. Supporting
# capabilities are typed compatibility adapters selected by execution, not peer sources.
FAMILY_LOCAL_DATA_ANALYTICS = "LOCAL_DATA_ANALYTICS"
FAMILY_WEB_EXTERNAL_RESEARCH = "WEB_EXTERNAL_RESEARCH"
FAMILY_SUPPORTING = "SUPPORTING"
CAPABILITY_FAMILIES = (FAMILY_LOCAL_DATA_ANALYTICS, FAMILY_WEB_EXTERNAL_RESEARCH, FAMILY_SUPPORTING)


@dataclass
class CapabilitySpec:
    name: str
    description: str
    accepts: list[str]
    produces: list[str]
    required_inputs: list[str] = field(default_factory=list)
    handler: Callable[..., CapabilityResult] | None = None
    # One of CAPABILITY_FAMILIES. SUPPORTING capabilities are execution infrastructure
    # (identity, knowledge, projection, bounded computation, structured adapters).
    family: str = FAMILY_SUPPORTING


class Capability(Protocol):
    spec: CapabilitySpec

    def execute(self, request: ToolRequest, context: Any) -> CapabilityResult: ...
