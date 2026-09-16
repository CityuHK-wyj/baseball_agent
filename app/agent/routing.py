"""Source/tool routing. Split from planning: Planner says what, Router says where.

Precedence: System Policy > User Hard Constraint > Source Capability >
Planner Preference > Router Optimization.
"""

from typing import Callable, Literal

from pydantic import Field

from datetime import date

from app.models.artifacts import ArtifactContract
from app.models.contracts import (Constraint, CountConstraint, LocationConstraint, Name,
                                  NumericConstraint, PitchTypeConstraint, PopulationConstraint,
                                  RankingConstraint, TimeRange)
from app.models.planning import AgentTask, RoutingDecision

Cost = Literal["FREE", "PAID", "HIGH"]


def constraint_capability_keys(constraints: tuple[Constraint, ...]) -> tuple[str, ...]:
    """Stable capability keys for typed analytical constraints.

    The date range is handled by ``TimeRange`` on the descriptor, not by a source's
    constraint capability, so it is excluded. A location constraint keys on its exact
    definition so an unavailable definition can never silently degrade to another one.
    """
    keys: list[str] = []
    for constraint in constraints:
        if isinstance(constraint, NumericConstraint):
            keys.append(constraint.key)
        elif isinstance(constraint, CountConstraint):
            keys.append("count")
        elif isinstance(constraint, PitchTypeConstraint):
            keys.append("pitch_type")
        elif isinstance(constraint, LocationConstraint):
            keys.append(f"pitch_location:{constraint.definition}")
        elif isinstance(constraint, RankingConstraint):
            keys.append("ranking")
        elif isinstance(constraint, PopulationConstraint):
            keys.append("population")
        # CategoryConstraint (entity_key, source, season, date_range) is metadata or
        # routing state; QualificationConstraint is frozen requirement eligibility that
        # does not change which source can provide the metric.
    return tuple(dict.fromkeys(keys))


class ToolCapability(ArtifactContract):
    tool: Name
    source_kind: Literal["POSTGRES", "PARQUET", "WEB", "FEATURE", "SYNTHETIC"]
    supported_artifact_types: tuple[Literal["TABLE", "EVIDENCE", "FEATURE"], ...] = Field(min_length=1)
    cost: Cost = "FREE"
    available: bool = True
    system_permitted: bool = True
    coverage: str = ""
    coverage_start: date | None = None
    coverage_end: date | None = None
    supported_data_keys: tuple[str, ...] = ()
    supported_constraint_keys: tuple[str, ...] = ()

    def covers(self, time_range: TimeRange | None) -> bool:
        """True when the declared coverage overlaps the requested window at all."""
        if time_range is None or (self.coverage_start is None and self.coverage_end is None):
            return True
        start_ok = self.coverage_end is None or time_range.start <= self.coverage_end
        end_ok = self.coverage_start is None or time_range.end >= self.coverage_start
        return start_ok and end_ok

    def supports(self, artifact_type: str, data_keys: tuple[str, ...] = (),
                 constraint_keys: tuple[str, ...] = (),
                 time_range: TimeRange | None = None) -> bool:
        data_ok = artifact_type in self.supported_artifact_types and (
            not self.supported_data_keys or set(data_keys) <= set(self.supported_data_keys))
        constraint_ok = (not constraint_keys
                         or set(constraint_keys) <= set(self.supported_constraint_keys))
        return data_ok and constraint_ok and self.covers(time_range)


class Router:
    def __init__(self, capabilities: tuple[ToolCapability, ...],
                 id_factory: Callable[[str], str] | None = None,
                 permitted_costs: tuple[Cost, ...] = ("FREE",)) -> None:
        self._capabilities = tuple(capabilities)
        self._id_factory = id_factory or (lambda prefix: f"{prefix}-{id(object())}")
        self._permitted_costs = permitted_costs

    def candidate_sources(self, artifact_type: str, data_keys: tuple[str, ...] = (),
                          constraint_keys: tuple[str, ...] = (),
                          time_range: TimeRange | None = None) -> tuple[ToolCapability, ...]:
        """Capabilities that can produce this artifact type, before policy and constraints."""
        return tuple(c for c in self._capabilities if c.supports(artifact_type, data_keys,
                                                                 constraint_keys, time_range))

    def permission_candidates(self, artifact_type: str, data_keys: tuple[str, ...] = (),
                              constraint_keys: tuple[str, ...] = (),
                              time_range: TimeRange | None = None) -> tuple[ToolCapability, ...]:
        """Consent-gated capabilities; system-forbidden capabilities are excluded."""
        return tuple(c for c in self._capabilities if c.system_permitted and c.available
                     and c.cost in ("PAID", "HIGH")
                     and c.cost not in self._permitted_costs
                     and c.supports(artifact_type, data_keys, constraint_keys, time_range))

    def authorized_for(self, costs: tuple[Cost, ...], tools: tuple[str, ...] = ()) -> "Router":
        capabilities = tuple(
            item.model_copy(update={"available": False})
            if item.cost in costs and tools and item.tool not in tools else item
            for item in self._capabilities)
        return Router(capabilities, id_factory=self._id_factory,
                      permitted_costs=tuple(dict.fromkeys((*self._permitted_costs, *costs))))

    def eligible_sources(self, artifact_type: str, user_hard_sources: tuple[str, ...] = (),
                         data_keys: tuple[str, ...] = (),
                         constraint_keys: tuple[str, ...] = (),
                         time_range: TimeRange | None = None) -> tuple[ToolCapability, ...]:
        """Capabilities that satisfy policy, user constraints and required artifact type."""
        return tuple(
            capability for capability in self._capabilities
            if capability.system_permitted
            and capability.available
            and capability.cost in self._permitted_costs
            and capability.supports(artifact_type, data_keys, constraint_keys, time_range)
            and (not user_hard_sources or capability.source_kind in user_hard_sources)
        )

    def route(self, task: AgentTask, artifact_type: str,
              user_hard_sources: tuple[str, ...] = (),
              execution_route: object | None = None, data_keys: tuple[str, ...] = (),
              constraint_keys: tuple[str, ...] = (),
              time_range: TimeRange | None = None) -> RoutingDecision:
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
            if not capability.supports(artifact_type, data_keys, constraint_keys, time_range):
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
