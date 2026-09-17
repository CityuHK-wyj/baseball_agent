"""Explicit Artifact bindings.

A downstream action must identify exactly which upstream Artifact/export it consumes.
Ambient export injection ("find the first PLAYER_ID_SET in the conversation") is
forbidden: prior-turn, wrong-team, rejected, stale or partial Artifacts must not bind
just because their export type matches.

The binder only considers exports produced by the Need's *declared dependencies* (or the
Need's own explicit ``input_refs``), and records the exact chosen export as an
``InputBinding``.
"""

from __future__ import annotations

from app.models.artifact_runtime import InputBinding, Need, RuntimeArtifact

from app.artifact_runtime.tool_base import ToolContext, resolve_export_ref


def artifact_index(needs: tuple[Need, ...]) -> dict[str, tuple[str, ...]]:
    """Map need_id -> the artifact ids that Need produced."""
    index: dict[str, tuple[str, ...]] = {}
    for need in needs:
        index[need.need_id] = tuple(need.linked_artifacts)
    return index


def resolve_bindings(need: Need, *, artifacts: tuple[RuntimeArtifact, ...],
                     export_refs: dict[str, str],
                     by_need: dict[str, tuple[str, ...]],
                     accepted_types: tuple[str, ...]) -> tuple[InputBinding, ...]:
    """Choose explicit bindings for one Need.

    Only the Need's own ``input_refs`` and the artifacts of its declared dependencies are
    eligible. Returns the chosen bindings; it never mutates global state.
    """
    by_id = {artifact.artifact_id: artifact for artifact in artifacts}
    wanted = set(accepted_types)
    bindings: list[InputBinding] = []
    bound_exports: set[str] = set()

    # 1. Explicit planner-declared refs win, but only if they resolve to real exports.
    for ref in need.input_refs:
        export = resolve_export_ref(_ref_context(artifacts, export_refs), ref)
        if export is None:
            continue
        if wanted and export.export_type not in wanted:
            continue
        source_artifact = _artifact_of_export(artifacts, export.export_id)
        bindings.append(InputBinding(
            binding_id=f"{need.need_id}:{export.export_id}",
            name=export.export_type, export_type=export.export_type,
            source_need_id=_need_for_artifact(by_need, source_artifact),
            source_artifact_id=source_artifact or "",
            source_export_id=export.export_id, ref_id=ref))
        bound_exports.add(export.export_id)

    # 2. Otherwise bind from declared dependencies, in declaration order.
    for dependency in need.depends_on:
        for artifact_id in by_need.get(dependency, ()):
            artifact = by_id.get(artifact_id)
            if artifact is None or artifact.status not in ("OK", "PARTIAL"):
                continue
            for export in artifact.exports:
                if export.export_type in bound_exports:
                    continue
                if wanted and export.export_type not in wanted:
                    continue
                ref_id = export_refs.get(export.export_id, "")
                bindings.append(InputBinding(
                    binding_id=f"{need.need_id}:{export.export_id}",
                    name=export.export_type, export_type=export.export_type,
                    source_need_id=dependency, source_artifact_id=artifact_id,
                    source_export_id=export.export_id, ref_id=ref_id))
                bound_exports.add(export.export_id)
    return tuple(bindings)


def binding_refs(bindings: tuple[InputBinding, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.ref_id for item in bindings if item.ref_id))


def binding_export_ids(bindings: tuple[InputBinding, ...]) -> tuple[str, ...]:
    return tuple(item.source_export_id for item in bindings if item.source_export_id)


def apply_bindings(need: Need, bindings: tuple[InputBinding, ...]) -> Need:
    return need.model_copy(update={
        "input_bindings": bindings,
        "input_refs": tuple(dict.fromkeys((*need.input_refs, *binding_refs(bindings))))})


def _ref_context(artifacts: tuple[RuntimeArtifact, ...],
                 export_refs: dict[str, str]) -> ToolContext:
    from app.artifact_runtime.artifacts import ArtifactStore
    from app.artifact_runtime.references import ReferenceStore
    from datetime import date

    store = ArtifactStore()
    for artifact in artifacts:
        try:
            store.add(artifact)
        except ValueError:
            pass
    refs = ReferenceStore()
    for export_id, ref_id in export_refs.items():
        artifact = _artifact_of_export(artifacts, export_id)
        if artifact is None:
            continue
        refs.add("ARTIFACT_EXPORT", artifact.artifact_id, selector=export_id, ref_id=ref_id)
    return ToolContext(refs=refs, artifacts=store, today=date.today())


def _artifact_of_export(artifacts: tuple[RuntimeArtifact, ...], export_id: str) -> str:
    for artifact in artifacts:
        if any(export.export_id == export_id for export in artifact.exports):
            return artifact.artifact_id
    return ""


def _need_for_artifact(by_need: dict[str, tuple[str, ...]], artifact_id: str) -> str:
    for need_id, artifact_ids in by_need.items():
        if artifact_id in artifact_ids:
            return need_id
    return ""
