"""Runtime Binder: resolve Need dependencies to real Artifact exports under contract.

`Need B depends_on Need A` plus `input_requirements` becomes typed InputBindings. Artifact
ids, export ids, entity/scope compatibility and required-input completeness are resolved
here, never by the Planner. A binding that cannot satisfy the consumer's contract fails
closed with a typed :class:`BindingFailure` instead of silently producing a broader query.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.contracts.models import Artifact, BindingFailure, InputBinding, PlannerNeed, Scope
from app.verification.scope import binding_scope_conflicts

ENTITY_EXPORT_TYPES = {"PLAYER_ID_SET"}
ENTITY_NAMESPACE = "MLBAM"


@dataclass
class BindingResult:
    bindings: list[InputBinding] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    rejected: list[dict[str, str]] = field(default_factory=list)
    failures: list[BindingFailure] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unresolved

    @property
    def required_failures(self) -> list[BindingFailure]:
        return [failure for failure in self.failures if failure.required]

    def failure_codes(self) -> list[str]:
        return [failure.code for failure in self.failures]


class RuntimeBinder:
    def __init__(self, registry):
        self.registry = registry

    # ------------------------------------------------------------------ bind
    def bind(
        self,
        need: PlannerNeed,
        needs: list[PlannerNeed],
        artifacts: list[Artifact],
        *,
        requested_scope: Scope | None = None,
    ) -> BindingResult:
        result = BindingResult()
        capability = self.registry.get(need.capability)
        # A capability's `accepts` set is the authority. `input_requirements` is only a
        # preference and can never widen or narrow what the capability may consume.
        capability_accepts = set(capability.spec.accepts) if capability else set()
        restricted = bool(capability_accepts)
        declared = [str(item) for item in need.input_requirements if item]
        declared_set = set(declared)
        required_types = set(capability.spec.required_inputs) if capability else set()

        used_export_ids: set[str] = set()
        covered_types: set[str] = set()

        for dep_id in need.depends_on:
            dep_artifacts = [
                artifact
                for artifact in artifacts
                if artifact.need_id == dep_id and artifact.status in {"ACCEPTED", "PARTIAL"}
            ]
            if not dep_artifacts:
                self._fail(
                    result, need, dep_id, "ARTIFACT_NOT_ACCEPTED",
                    f"{dep_id} produced no accepted Artifact", required=True,
                )
                continue
            candidates: list[tuple[Artifact, Any]] = []
            for artifact in dep_artifacts:
                for export in artifact.exports:
                    if export.id in used_export_ids:
                        continue
                    if restricted and export.type not in capability_accepts:
                        continue
                    candidates.append((artifact, export))
            if not candidates:
                wanted = ", ".join(sorted(capability_accepts)) or "(unrestricted)"
                if declared_set:
                    self._fail(
                        result, need, dep_id, "EXPORT_TYPE_INCOMPATIBLE",
                        f"{dep_id} exposes none of the capability-compatible exports [{wanted}] "
                        f"(planner declared [{', '.join(declared)}])",
                        required=True,
                    )
                else:
                    self._fail(
                        result, need, dep_id, "EXPORT_TYPE_INCOMPATIBLE",
                        f"{dep_id} exposes none of the capability-compatible exports [{wanted}]",
                        required=True,
                    )
                continue
            if declared_set and not any(export.type in declared_set for _, export in candidates):
                # The Planner's declared type is not consumable; fall back to the
                # capability contract instead of failing, but record the mismatch.
                result.rejected.append({
                    "need_id": need.id,
                    "reason": "EXPORT_TYPE_INCOMPATIBLE",
                    "detail": f"{dep_id}: declared [{', '.join(declared)}] not in capability accepts",
                })

            chosen = self._choose(need, dep_id, candidates, declared_set, requested_scope, result)
            if chosen is None:
                continue
            artifact, export = chosen
            used_export_ids.add(export.id)
            covered_types.add(export.type)
            result.bindings.append(
                InputBinding(
                    need_id=need.id,
                    export_id=export.id,
                    export_type=export.type,
                    artifact_id=artifact.id,
                    requirement=export.type,
                    source_need_id=dep_id,
                )
            )

        # Required-input completeness: every type the capability declares as indispensable
        # must actually be covered by a binding.
        missing = required_types - covered_types
        for missing_type in sorted(missing):
            self._fail(
                result, need, "", "UNRESOLVED_REQUIRED_INPUT",
                f"capability '{need.capability}' requires an input of type [{missing_type}]",
                required=True, retryable=True,
            )
        return result

    # ------------------------------------------------------------------ helpers
    def _choose(
        self,
        need: PlannerNeed,
        dep_id: str,
        candidates: list[tuple[Artifact, Any]],
        declared_set: set[str],
        requested_scope: Scope | None,
        result: BindingResult,
    ) -> tuple[Artifact, Any] | None:
        # Reject structurally unusable exports (an empty entity set can never be bound).
        usable: list[tuple[Artifact, Any]] = []
        for artifact, export in candidates:
            failure = _structural_failure(need, dep_id, export)
            if failure is not None:
                result.failures.append(failure)
                continue
            usable.append((artifact, export))
        if not usable:
            self._fail(
                result, need, dep_id, "UNRESOLVED_REQUIRED_INPUT",
                f"{dep_id}: no usable export after contract checks", required=True,
            )
            return None

        compatible: list[tuple[Artifact, Any]] = []
        conflicts: list[str] = []
        for artifact, export in usable:
            channel_conflicts = binding_scope_conflicts(requested_scope, artifact.actual_scope)
            if channel_conflicts:
                conflicts.extend(
                    f"{dep_id}:{export.type}:{channel}={requested}->{actual}"
                    for channel, requested, actual in channel_conflicts
                )
                continue
            compatible.append((artifact, export))
        if not compatible:
            self._fail(
                result, need, dep_id, "SCOPE_INCOMPATIBLE",
                f"{dep_id}: accepted exports violate the requested scope ({'; '.join(conflicts)})",
                required=True,
            )
            return None

        compatible.sort(
            key=lambda pair: (
                1 if pair[1].type in declared_set else 0,
                _scope_weight(pair[0]),
                pair[1].id,
            ),
            reverse=True,
        )
        return compatible[0]

    def _fail(
        self,
        result: BindingResult,
        need: PlannerNeed,
        dep_id: str,
        code: str,
        detail: str,
        *,
        required: bool,
        retryable: bool = False,
        channel: str = "",
    ) -> None:
        result.unresolved.append(detail)
        result.rejected.append({"need_id": need.id, "reason": code, "detail": detail})
        result.failures.append(
            BindingFailure(
                need_id=need.id,
                dependency_id=dep_id,
                code=code,
                detail=detail,
                channel=channel,
                required=required,
                retryable=retryable,
            )
        )


def _structural_failure(need: PlannerNeed, dep_id: str, export) -> BindingFailure | None:
    """Export-type-specific structural contract checks."""
    if export.type in ENTITY_EXPORT_TYPES:
        payload = export.payload if isinstance(export.payload, dict) else {}
        ids = payload.get("ids") or []
        if not ids:
            return BindingFailure(
                need_id=need.id,
                dependency_id=dep_id,
                code="EMPTY_ENTITY_SET",
                detail=f"{dep_id}: {export.type} carries no resolved entity id",
                channel="entity",
                required=True,
            )
        namespace = (export.contract or {}).get("namespace")
        if namespace and namespace != ENTITY_NAMESPACE:
            return BindingFailure(
                need_id=need.id,
                dependency_id=dep_id,
                code="ENTITY_INCOMPATIBLE",
                detail=f"{dep_id}: entity namespace '{namespace}' is not '{ENTITY_NAMESPACE}'",
                channel="entity",
                required=True,
            )
    return None


def _scope_weight(artifact: Artifact) -> int:
    scope = artifact.actual_scope
    if scope is None:
        return 0
    return sum(
        [
            len(scope.entities or []),
            len(scope.entity_names or []),
            1 if scope.season else 0,
            1 if scope.game_population else 0,
            1 if scope.measure else 0,
            len(scope.measures or []),
        ]
    )
