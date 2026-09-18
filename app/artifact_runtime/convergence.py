"""Planner-facing convergence surface.

The planner is allowed to reason freely, but its proposals must converge onto the
capabilities, schemas, evidence products and bindings that actually exist in this
runtime. Before v0.5 the planner was given only a tool-name map and a flat field list;
Artifact availability, prior ToolOutcomes, obligation coverage and scope mismatches
existed in Python but were not represented to the planner. Replanning therefore appended
another predefined action instead of reacting to what actually happened.

This module is deliberately a *view builder*, not an authority:

* it never authorizes a field, identifier or action;
* it never mutates the artifact/catalog/attempt stores;
* it bounds and summarizes information for planning only.

The authoritative components remain ``SchemaCatalog`` (execution validation),
``ToolCapabilityContract`` (admission), ``bindings`` (explicit input selection),
``obligations`` (frozen baseline) and ``recovery`` (durable outcome taxonomy).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.artifact_runtime import (Goal, Need, RuntimeArtifact, Scope, ToolAttempt)
from app.artifact_runtime.recovery import failure_class, replan_hint

# -- Planning state vocabulary -------------------------------------------------------
# A scheduler must be able to tell these apart; "not ready" is not one state.
READY = "READY"
SATISFIED = "SATISFIED"
CLOSED = "CLOSED"  # already failed/blocked and not retryable in place
ATTEMPTED = "ATTEMPTED"
IMPOSSIBLE_CAPABILITY = "IMPOSSIBLE_CAPABILITY"
BLOCKED_WAITING = "BLOCKED_WAITING"
DEPENDENCY_REJECTED = "DEPENDENCY_REJECTED"
NO_CAPABILITY = "NO_CAPABILITY"


def _bounded(text: str, limit: int = 240) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def scope_summary(scope: Scope | None) -> str:
    """A bounded, human/LLM-readable scope summary. Never a raw payload dump."""
    if scope is None:
        return ""
    parts: list[str] = []
    if scope.entities or scope.canonical_entities:
        parts.append("entities=" + ",".join((*scope.entities, *scope.canonical_entities)))
    if scope.population:
        parts.append(f"population={scope.population}")
    if scope.membership_basis:
        parts.append(f"membership={scope.membership_basis}")
    if scope.time_range is not None:
        parts.append(f"time={scope.time_range.start.isoformat()}..{scope.time_range.end.isoformat()}")
    if scope.seasons:
        parts.append("seasons=" + ",".join(str(item) for item in scope.seasons))
    if scope.game_types:
        parts.append("game_types=" + ",".join(scope.game_types))
    if scope.metric:
        parts.append(f"measure={scope.metric}")
    if scope.event_population:
        parts.append(f"event_population={scope.event_population}")
    if scope.qualification:
        parts.append(f"qualification={scope.qualification}")
    if scope.source_coverage:
        parts.append("sources=" + ",".join(scope.source_coverage))
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Capability / schema views
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CapabilityView:
    name: str
    accepts: tuple[str, ...]
    produces: tuple[str, ...]
    description: str = ""
    cost: int = 1
    availability: str = "AVAILABLE"
    authority: str = "DERIVED"
    temporal_modes: tuple[str, ...] = ()
    population_modes: tuple[str, ...] = ()
    game_types: tuple[str, ...] = ()
    entity_namespace: str = ""
    supported_measures: tuple[str, ...] = ()
    required_inputs: tuple[str, ...] = ()

    @classmethod
    def from_tool(cls, tool) -> "CapabilityView":
        contract = tool.contract
        return cls(
            name=contract.name, accepts=contract.accepts, produces=contract.produces,
            description=_bounded(contract.description), cost=contract.cost,
            availability=contract.availability, authority=contract.authority,
            temporal_modes=contract.temporal_modes, population_modes=contract.population_modes,
            game_types=contract.game_types, entity_namespace=contract.entity_namespace,
            supported_measures=contract.supported_measures,
            required_inputs=contract.required_inputs)

    def render(self) -> str:
        pieces = [f"- {self.name}: {self.accepts} -> {self.produces}"]
        meta: list[str] = []
        if self.availability != "AVAILABLE":
            meta.append(f"availability={self.availability}")
        if self.authority:
            meta.append(f"authority={self.authority}")
        if self.temporal_modes:
            meta.append("time=" + "/".join(self.temporal_modes))
        if self.population_modes:
            meta.append("population=" + "/".join(self.population_modes))
        if self.game_types:
            meta.append("game_types=" + "/".join(self.game_types))
        if self.entity_namespace:
            meta.append(f"ids={self.entity_namespace}")
        if self.supported_measures:
            meta.append("measures=" + ",".join(self.supported_measures))
        if self.required_inputs:
            meta.append("requires=" + ",".join(self.required_inputs))
        if self.cost > 1:
            meta.append(f"cost={self.cost}")
        if meta:
            pieces.append(" [" + "; ".join(meta) + "]")
        if self.description:
            pieces.append(f" — {self.description}")
        return "".join(pieces)


@dataclass(frozen=True)
class SchemaFieldView:
    name: str
    data_type: str = "UNKNOWN"
    role: str = "DIMENSION"
    meaning: str = ""
    entity: str = ""
    allowed_operations: tuple[str, ...] = ()
    nullable: bool = True

    def render(self) -> str:
        detail = f"{self.name}({self.role},{self.data_type}"
        if self.entity:
            detail += f",{self.entity}"
        detail += ")"
        if self.meaning:
            detail += f"={_bounded(self.meaning, 60)}"
        return detail


@dataclass(frozen=True)
class SchemaTableView:
    name: str
    source_kind: str
    grain: str = ""
    coverage: tuple[str, ...] = ()
    description: str = ""
    fields: tuple[SchemaFieldView, ...] = ()

    def render(self, *, max_fields: int = 40) -> str:
        coverage = f" coverage={','.join(self.coverage)}" if self.coverage else ""
        lines = [f"- {self.source_kind}:{self.name} grain={self.grain}{coverage}"]
        if self.description:
            lines.append(f"    {_bounded(self.description, 160)}")
        fields = self.fields[:max_fields]
        lines.append("    fields: " + ", ".join(item.render() for item in fields))
        return "\n".join(lines)


def capability_views(registry) -> tuple[CapabilityView, ...]:
    return tuple(CapabilityView.from_tool(tool) for tool in registry.all())


def schema_views(catalog) -> tuple[SchemaTableView, ...]:
    views: list[SchemaTableView] = []
    for table in catalog.tables():
        views.append(SchemaTableView(
            name=table.name, source_kind=table.source_kind, grain=table.grain,
            coverage=table.coverage, description=table.description,
            fields=tuple(SchemaFieldView(
                name=item.name, data_type=item.data_type, role=item.role,
                meaning=item.meaning, entity=item.entity,
                allowed_operations=item.allowed_operations, nullable=item.nullable)
                for item in table.fields)))
    return tuple(views)


# ---------------------------------------------------------------------------
# Artifact / export availability
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExportView:
    export_id: str
    export_type: str
    artifact_id: str
    artifact_kind: str = ""
    artifact_status: str = "OK"
    accepted: bool = True
    entity_namespace: str = ""
    role: str = ""
    grain: str = ""
    cardinality: str = "UNKNOWN"
    producer: str = ""
    produced_by_need: str = ""
    created_at: str = ""
    scope_summary: str = ""
    verified_dimensions: tuple[tuple[str, str], ...] = ()
    hard_mismatch: bool = False
    value_shape: str = ""

    @property
    def usable(self) -> bool:
        return self.accepted and self.artifact_status in ("OK", "PARTIAL") \
            and not self.hard_mismatch

    def render(self) -> str:
        flags: list[str] = []
        if not self.accepted:
            flags.append("NOT-ACCEPTED")
        if self.artifact_status not in ("OK", "PARTIAL"):
            flags.append(self.artifact_status)
        if self.hard_mismatch:
            flags.append("SCOPE-MISMATCH")
        if self.verified_dimensions:
            flags.append("verified=" + ",".join(
                f"{dim}:{status}" for dim, status in self.verified_dimensions))
        head = (f"- {self.export_id} [{self.export_type}] from {self.artifact_id}"
                f"({self.artifact_kind}/{self.artifact_status})")
        if self.entity_namespace or self.cardinality:
            head += f" ids={self.entity_namespace or '?'} card={self.cardinality}"
        if self.produced_by_need:
            head += f" by={self.produced_by_need}"
        if flags:
            head += " " + " ".join(flags)
        if self.scope_summary:
            head += f" scope={{{self.scope_summary}}}"
        return head


def _value_shape(value) -> str:
    if isinstance(value, dict):
        if "columns" in value and "rows" in value:
            rows = value.get("rows") or []
            return f"table(rows={len(rows)})"
        return "object"
    if isinstance(value, (list, tuple)):
        return f"list(n={len(value)})"
    if value is None:
        return "empty"
    return type(value).__name__


def export_views(artifacts: tuple[RuntimeArtifact, ...],
                 by_need: dict[str, tuple[str, ...]] | None = None) -> tuple[ExportView, ...]:
    by_need = by_need or {}
    producer_of: dict[str, str] = {}
    for need_id, artifact_ids in by_need.items():
        for artifact_id in artifact_ids:
            producer_of.setdefault(artifact_id, need_id)
    views: list[ExportView] = []
    for artifact in artifacts:
        for export in artifact.exports:
            contract = export.contract
            views.append(ExportView(
                export_id=export.export_id, export_type=export.export_type,
                artifact_id=artifact.artifact_id, artifact_kind=artifact.kind,
                artifact_status=artifact.status, accepted=export.accepted,
                entity_namespace=(contract.entity_namespace if contract else ""),
                role=(contract.role if contract else ""),
                grain=(contract.grain if contract else ""),
                cardinality=(contract.cardinality if contract else "UNKNOWN"),
                producer=export.provenance,
                produced_by_need=producer_of.get(artifact.artifact_id, ""),
                created_at=artifact.created_at.isoformat(),
                scope_summary=scope_summary(contract.scope if contract else artifact.actual_scope),
                verified_dimensions=tuple(
                    (item.dimension, item.status) for item in artifact.scope_verifications),
                hard_mismatch=artifact.has_hard_mismatch(),
                value_shape=_value_shape(export.value)))
    return tuple(views)


# ---------------------------------------------------------------------------
# Attempt / recovery feedback
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttemptView:
    need_id: str
    capability: str
    status: str
    outcome_code: str
    failure_class: str
    retryable: bool
    detail: str = ""
    hint: str = ""
    artifact_ids: tuple[str, ...] = ()

    def render(self) -> str:
        line = (f"- need={self.need_id or '-'} {self.capability}: {self.outcome_code} "
                f"[{self.status}/{self.failure_class}]")
        if self.retryable:
            line += " retryable"
        if self.detail:
            line += f" — {_bounded(self.detail, 180)}"
        if self.hint:
            line += f" | next: {self.hint}"
        return line


def attempt_views(attempts: tuple[ToolAttempt, ...]) -> tuple[AttemptView, ...]:
    return tuple(AttemptView(
        need_id=item.need_id, capability=item.capability, status=item.status,
        outcome_code=item.outcome_code, failure_class=failure_class(item.outcome_code),
        retryable=item.retryable, detail=item.detail, hint=replan_hint(item),
        artifact_ids=item.artifact_ids) for item in attempts)


def unavailable_capabilities(attempts: tuple[ToolAttempt, ...], *,
                             registry=None) -> tuple[str, ...]:
    """Capabilities whose *structural precondition* is known to be impossible.

    Conservative on purpose: a per-request unsupported *operation* or a per-request
    provider refusal must not blacklist a whole capability. A capability is marked
    unavailable only for a policy block, for a capability that does not exist in the
    registry, or for an advertised-unavailable provider.
    """
    available = {tool.name: tool.contract.availability for tool in registry.all()} \
        if registry is not None else {}
    blocked = {
        item.capability for item in attempts
        if item.outcome_code == "POLICY_BLOCKED"
        or (item.outcome_code == "UNSUPPORTED_CAPABILITY" and (
            item.capability not in available
            or available.get(item.capability) not in (None, "AVAILABLE")))}
    return tuple(sorted(name for name in blocked if name))


# ---------------------------------------------------------------------------
# Scheduling / dependency state
# ---------------------------------------------------------------------------


def _dependency_ready(need: Need) -> bool:
    return need.status in ("SATISFIED", "PARTIAL") or bool(need.linked_artifacts)


def planning_state(need: Need, needs: tuple[Need, ...], *,
                   attempted: set[tuple[str, str]],
                   unavailable: set[str]) -> str:
    """Classify one Need for the scheduler. Distinct states, not a boolean."""
    if need.status == "SATISFIED":
        return SATISFIED
    if need.status in ("FAILED", "BLOCKED"):
        return CLOSED
    capability = need.proposed_capability
    if not capability:
        return NO_CAPABILITY
    if capability in unavailable:
        return IMPOSSIBLE_CAPABILITY
    if (need.need_id, capability) in attempted:
        return ATTEMPTED
    by_id = {item.need_id: item for item in needs}
    for dependency in need.depends_on:
        parent = by_id.get(dependency)
        if parent is None:
            return BLOCKED_WAITING
        if _dependency_ready(parent):
            continue
        if parent.status in ("FAILED", "BLOCKED"):
            return DEPENDENCY_REJECTED
        return BLOCKED_WAITING
    return READY


# ---------------------------------------------------------------------------
# Feedback bundle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlannerFeedback:
    attempts: tuple[AttemptView, ...] = ()
    obligation_coverage: dict[str, str] = field(default_factory=dict)
    obligation_descriptions: dict[str, str] = field(default_factory=dict)
    missing_obligations: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    unavailable_capabilities: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()
    budget_remaining: int = 0
    blocked: tuple[str, ...] = ()
    impossible: tuple[str, ...] = ()

    def render(self, *, max_attempts: int = 12, max_gaps: int = 8) -> str:
        lines: list[str] = []
        if self.attempts:
            lines.append("Attempted actions and durable outcomes:")
            lines.extend(item.render() for item in self.attempts[-max_attempts:])
        if self.obligation_coverage:
            lines.append("Frozen user obligations:")
            for obligation_id, state in self.obligation_coverage.items():
                description = self.obligation_descriptions.get(obligation_id, obligation_id)
                lines.append(f"- {state}: {_bounded(description, 120)}")
        if self.missing_obligations:
            lines.append("Unresolved obligations: " + "; ".join(self.missing_obligations[:8]))
        if self.conflicts:
            lines.append("Unsatisfiable requirements: " + "; ".join(self.conflicts[:6]))
        if self.unavailable_capabilities:
            lines.append("Capabilities known to be unavailable in this runtime: "
                         + ", ".join(self.unavailable_capabilities))
        if self.blocked:
            lines.append("Needs blocked waiting for a dependency: "
                         + ", ".join(self.blocked[:8]))
        if self.impossible:
            lines.append("Needs with no possible capability: "
                         + ", ".join(self.impossible[:8]))
        if self.gaps:
            lines.append("Coverage gaps:")
            lines.extend(f"- {_bounded(gap, 200)}" for gap in self.gaps[:max_gaps])
        lines.append(f"Iterations remaining: {self.budget_remaining}")
        return "\n".join(lines)


def build_feedback(*, goal: Goal, needs: tuple[Need, ...],
                   artifacts: tuple[RuntimeArtifact, ...],
                   attempts: tuple[ToolAttempt, ...],
                   obligation_coverage: dict[str, str] | None = None,
                   gaps: tuple[str, ...] = (), budget_remaining: int = 0,
                   registry=None) -> PlannerFeedback:
    attempted_pairs = {(item.need_id, item.capability) for item in attempts
                       if item.need_id and item.capability}
    unavailable = set(unavailable_capabilities(attempts, registry=registry))
    descriptions = {item.obligation_id: item.description for item in goal.obligations}
    blocked: list[str] = []
    impossible: list[str] = []
    for need in needs:
        state = planning_state(need, needs, attempted=attempted_pairs, unavailable=unavailable)
        if state == BLOCKED_WAITING:
            blocked.append(need.need_id)
        elif state in (IMPOSSIBLE_CAPABILITY, NO_CAPABILITY):
            impossible.append(need.need_id)
    missing = tuple(
        obligation.obligation_id for obligation in goal.obligations
        if (obligation_coverage or {}).get(obligation.obligation_id, "MISSING") != "VERIFIED")
    return PlannerFeedback(
        attempts=attempt_views(attempts),
        obligation_coverage=dict(obligation_coverage or {}),
        obligation_descriptions=descriptions,
        missing_obligations=missing,
        conflicts=tuple(
            f"{item.kind}/{item.severity}: {item.description}" for item in goal.conflicts),
        unavailable_capabilities=tuple(unavailable),
        gaps=tuple(gaps), budget_remaining=budget_remaining,
        blocked=tuple(blocked), impossible=tuple(impossible))
