"""Source Mapping execution planning.

Bridges semantic metrics to a physical execution mode: DIRECT (a source provides the
metric), CALCULATED (the Feature Engine computes it) or NO_MAPPING (blocked). The
Planner stays semantic (D015); only this resolver and the Router touch physical sources.
"""

from collections.abc import Mapping
from typing import Literal

from app.models.artifacts import ArtifactContract
from app.models.contracts import Name
from app.semantic.metric_registry import MetricRegistry


class ExecutionRoute(ArtifactContract):
    task_ref: Name
    mode: Literal["DIRECT", "CALCULATED", "NO_MAPPING"]
    required_source_kind: str = ""
    tool: str | None = None
    metric_keys: tuple[Name, ...] = ()
    unmapped_keys: tuple[Name, ...] = ()
    reason: str = ""


class SourceMappingResolver:
    def __init__(self, registry: MetricRegistry,
                 tool_by_source_kind: Mapping[str, str] | None = None) -> None:
        self._registry = registry
        self._tools = dict(tool_by_source_kind or {})

    def resolve(self, task_ref: str, data_keys: tuple[str, ...]) -> ExecutionRoute:
        direct: list[tuple[str, str]] = []
        calculated: list[str] = []
        unmapped: list[str] = []
        for key in data_keys:
            mapping = self._registry.mapping_for(key)
            if mapping is None:
                unmapped.append(key)
            elif mapping.computation == "DIRECT":
                direct.append((key, mapping.source_kind))
            else:
                calculated.append(key)

        if unmapped:
            return ExecutionRoute(
                task_ref=task_ref, mode="NO_MAPPING", unmapped_keys=tuple(unmapped),
                reason=f"No source mapping for {', '.join(unmapped)}")

        if (direct and calculated) or len({kind for _, kind in direct}) > 1:
            return ExecutionRoute(task_ref=task_ref, mode="NO_MAPPING",
                reason="Requirement spans multiple execution sources; no single tool provides every key")

        for key, source_kind in direct:
            tool = self._tools.get(source_kind)
            if tool is not None:
                return ExecutionRoute(
                    task_ref=task_ref, mode="DIRECT", required_source_kind=source_kind, tool=tool,
                    metric_keys=tuple(item for item, _ in direct),
                    reason=f"{key} is provided directly by {source_kind}")
        if direct:
            return ExecutionRoute(
                task_ref=task_ref, mode="NO_MAPPING",
                reason="No configured tool provides the mapped direct source")

        return ExecutionRoute(
            task_ref=task_ref, mode="CALCULATED", required_source_kind="FEATURE",
            metric_keys=tuple(calculated),
            reason="All required metrics are computed by the Feature Engine")
