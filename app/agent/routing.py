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
    system_permitted: bool = True
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

    def permission_candidates(self, artifact_type: str) -> tuple[ToolCapability, ...]:
        """Consent-gated capabilities; system-forbidden capabilities are excluded."""
        return tuple(c for c in self._capabilities if c.system_permitted and c.available
                     and c.cost in ("PAID", "HIGH")
                     and c.cost not in self._permitted_costs
                     and artifact_type in c.supported_artifact_types)

    def authorized_for(self, costs: tuple[Cost, ...], tools: tuple[str, ...] = ()) -> "Router":
        capabilities = tuple(
            item.model_copy(update={"available": False})
            if item.cost in costs and tools and item.tool not in tools else item
            for item in self._capabilities)
        return Router(capabilities, id_factory=self._id_factory,
                      permitted_costs=tuple(dict.fromkeys((*self._permitted_costs, *costs))))

    def eligible_sources(self, artifact_type: str, user_hard_sources: tuple[str, ...] = ()) -> tuple[ToolCapability, ...]:
        """Capabilities that satisfy policy, user constraints and required artifact type."""
        return tuple(
            capability for capability in self._capabilities
            if capability.system_permitted
            and capability.available
            and capability.cost in self._permitted_costs
            and artifact_type in capability.supported_artifact_types
            and (not user_hard_sources or capability.source_kind in user_hard_sources)
        )

    def route(self, task: AgentTask, artifact_type: str,
              user_hard_sources: tuple[str, ...] = (),
              execution_route: object | None = None) -> RoutingDecision:
        """Pick a tool. ``execution_route`` (from SourceMappingResolver) acts as a
        system-derived capability constraint above Planner preference."""
        notes: list[str] = []
        user_sources = tuple(user_hard_sources)
        mapped_kind = ""
        mapped_tool = ""
        if execution_route is not None:
            mode = getattr(execution_route, "mode", "")
            if mode == "NO_MAPPING":
                reason = getattr(execution_route, "reason", "no source mapping")
                return RoutingDecision(
                    decision_id=self._id_factory("routing"), task_ref=task.task_id,
                    selected_tool=None, rationale=f"Source mapping blocked execution: {reason}",
                    policy_notes=(reason,))
            mapped_kind = getattr(execution_route, "required_source_kind", "")
            if mapped_kind:
                notes.append(f"source mapping requires {mapped_kind}")
            mapped_tool = getattr(execution_route, "tool", "") or ""
            if mapped_tool:
                notes.append(f"source mapping requires tool {mapped_tool}")

        eligible: list[ToolCapability] = []
        for capability in self._capabilities:
            if not capability.available:
                notes.append(f"{capability.tool}: unavailable")
                continue
            if not capability.system_permitted:
                notes.append(f"{capability.tool}: forbidden by system policy")
                continue
            if capability.cost not in self._permitted_costs:
                notes.append(f"{capability.tool}: {capability.cost} source not permitted")
                continue
            if artifact_type not in capability.supported_artifact_types:
                notes.append(f"{capability.tool}: cannot provide {artifact_type}")
                continue
            if user_sources and capability.source_kind not in user_sources:
                notes.append(f"{capability.tool}: violates a user source constraint")
                continue
            if mapped_kind and capability.source_kind != mapped_kind:
                notes.append(f"{capability.tool}: violates the source mapping")
                continue
            if mapped_tool and capability.tool != mapped_tool:
                notes.append(f"{capability.tool}: violates the mapped tool")
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
