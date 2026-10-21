"""Capability registry: truthful capability specs for the Planner, execution for runtime."""

from __future__ import annotations

from typing import Any

from app.capabilities.base import (
    CAPABILITY_FAMILIES, FAMILY_LOCAL_DATA_ANALYTICS, FAMILY_SUPPORTING,
    FAMILY_WEB_EXTERNAL_RESEARCH, CapabilityResult, CapabilitySpec,
)
from app.contracts.models import ToolRequest


class CapabilityRegistry:
    def __init__(self):
        self._capabilities: dict[str, Any] = {}

    def register(self, capability) -> None:
        self._capabilities[capability.spec.name] = capability

    def get(self, name: str):
        return self._capabilities.get(name)

    def names(self) -> list[str]:
        return sorted(self._capabilities)

    def specs(self) -> list[CapabilitySpec]:
        return [cap.spec for cap in self._capabilities.values()]

    def execute(self, request: ToolRequest, context) -> CapabilityResult:
        capability = self._capabilities.get(request.capability)
        if capability is None:
            from app.contracts.models import ToolOutcome

            return CapabilityResult(
                None,
                ToolOutcome(
                    status="BLOCKED",
                    code="UNSUPPORTED_CAPABILITY",
                    message=f"capability '{request.capability}' is not registered",
                    need_id=request.need_id,
                    tool_request_id=request.id,
                    classification="UNSUPPORTED_CAPABILITY",
                ),
            )
        return capability.execute(request, context)

    def planner_view(self, availability: dict[str, bool] | None = None) -> list[dict[str, Any]]:
        availability = availability or {}
        view = []
        for spec in self.specs():
            view.append(
                {
                    "capability": spec.name,
                    "family": spec.family,
                    "description": spec.description,
                    "accepts": spec.accepts,
                    "produces": spec.produces,
                    "requires_inputs": spec.required_inputs,
                    "available": availability.get(spec.name, True),
                }
            )
        return view

    def capability_families(self) -> dict[str, list[str]]:
        """The two domain families plus supporting execution infrastructure.

        The Planner chooses *evidence needs* within a family; supporting capabilities are
        selected by execution (identity, knowledge, projection, computation, adapters).
        No provider or API becomes a new top-level domain source.
        """
        families: dict[str, list[str]] = {name: [] for name in CAPABILITY_FAMILIES}
        for spec in self.specs():
            families.setdefault(spec.family, []).append(spec.name)
        return families
