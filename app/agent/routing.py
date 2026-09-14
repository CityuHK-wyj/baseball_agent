"""Source/tool routing. Split from planning: Planner says what, Router says where.

Precedence: System Policy > User Hard Constraint > Source Capability >
Planner Preference > Router Optimization.
"""

from typing import Callable, Literal

from pydantic import Field

from app.models.artifacts import ArtifactContract
from app.models.contracts import Name
from app.models.planning import AgentTask, RoutingDecision

Cost = Literal["FREE", "PAID", "HIGH"]


class ToolCapability(ArtifactContract):
    tool: Name
    source_kind: Literal["POSTGRES", "PARQUET", "WEB", "FEATURE", "SYNTHETIC"]
    supported_artifact_types: tuple[Literal["TABLE", "EVIDENCE", "FEATURE"], ...] = Field(min_length=1)
    cost: Cost = "FREE"
    available: bool = True
    coverage: str = ""


class Router:
    def __init__(self, capabilities: tuple[ToolCapability, ...],
                 id_factory: Callable[[str], str] | None = None,
                 permitted_costs: tuple[Cost, ...] = ("FREE",)) -> None:
        self._capabilities = tuple(capabilities)
        self._id_factory = id_factory or (lambda prefix: f"{prefix}-{id(object())}")
        self._permitted_costs = permitted_costs

    def candidate_sources(self, artifact_type: str) -> tuple[ToolCapability, ...]:
        """Capabilities that can produce this artifact type, before policy and constraints."""
        return tuple(c for c in self._capabilities if artifact_type in c.supported_artifact_types)

    def eligible_sources(self, artifact_type: str, user_hard_sources: tuple[str, ...] = ()) -> tuple[ToolCapability, ...]:
        """Capabilities that satisfy policy, user constraints and required artifact type."""
        return tuple(
            capability for capability in self._capabilities
            if capability.available
            and capability.cost in self._permitted_costs
            and artifact_type in capability.supported_artifact_types
            and (not user_hard_sources or capability.source_kind in user_hard_sources)
        )

    def route(self, task: AgentTask, artifact_type: str,
              user_hard_sources: tuple[str, ...] = ()) -> RoutingDecision:
        notes: list[str] = []
        eligible: list[ToolCapability] = []
        for capability in self._capabilities:
            if not capability.available:
                notes.append(f"{capability.tool}: unavailable")
                continue
            if capability.cost not in self._permitted_costs:
                notes.append(f"{capability.tool}: {capability.cost} source not permitted")
                continue
            if artifact_type not in capability.supported_artifact_types:
                notes.append(f"{capability.tool}: cannot provide {artifact_type}")
                continue
            if user_hard_sources and capability.source_kind not in user_hard_sources:
                notes.append(f"{capability.tool}: violates a user source constraint")
                continue
            eligible.append(capability)

        if not eligible:
            return RoutingDecision(
                decision_id=self._id_factory("routing"), task_ref=task.task_id, selected_tool=None,
                rationale="No source satisfies policy, constraints and capability.", policy_notes=tuple(notes))

        preferred = [item for item in eligible if item.tool == task.source_preference]
        chosen = preferred[0] if preferred else self._optimize(eligible)
        fallbacks = tuple(item.tool for item in eligible if item.tool != chosen.tool)
        return RoutingDecision(
            decision_id=self._id_factory("routing"), task_ref=task.task_id, selected_tool=chosen.tool,
            rationale=f"Selected {chosen.tool} for {artifact_type}.",
            fallbacks=fallbacks, policy_notes=tuple(notes))

    @staticmethod
    def _optimize(eligible: list[ToolCapability]) -> ToolCapability:
        order = {"FREE": 0, "PAID": 1, "HIGH": 2}
        return sorted(eligible, key=lambda item: order[item.cost])[0]
