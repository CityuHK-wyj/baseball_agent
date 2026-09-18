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

from app.models.artifact_runtime import ArtifactExport, InputBinding, Need, RuntimeArtifact

from app.artifact_runtime.scope import population_category
from app.artifact_runtime.tool_base import ToolContext, resolve_export_ref


def _norm(value) -> str:
    return str(value or "").strip().casefold()


def _norm_set(values) -> set[str]:
    return {_norm(item) for item in (values or ()) if _norm(item)}


def export_compatible(need: Need, export: ArtifactExport,
                      artifact: RuntimeArtifact) -> tuple[bool, tuple[str, ...]]:
    """Validate that an export can genuinely satisfy a downstream Need input.

    An export-type match alone is insufficient. Compatibility also considers the owning
    Artifact's accepted state, the typed ``ExportContract`` (namespace/role/grain) and
    the declared scope (entity / population / membership / game type / season / window),
    so two same-type exports cannot silently bind to the wrong Need. This is a safety
    check, not a heuristic: an incompatible export is reported as a structured gap.
    """
    reasons: list[str] = []
    if not export.accepted:
        reasons.append("export is not accepted")
    if artifact.status not in ("OK", "PARTIAL"):
        reasons.append(f"owning artifact status is {artifact.status}")
    contract = export.contract
    requested = need.required_scope
    observed = (contract.scope if contract is not None and contract.scope is not None
                else artifact.actual_scope)
    if requested is not None and observed is not None:
        req_entities = _norm_set((*requested.entities, *requested.canonical_entities))
        obs_entities = _norm_set((*observed.entities, *observed.canonical_entities))
        if req_entities and obs_entities and not (req_entities & obs_entities):
            reasons.append(f"entity namespace mismatch: need={sorted(req_entities)} "
                           f"export={sorted(obs_entities)}")
        if requested.population and observed.population \
                and _norm(requested.population) != _norm(observed.population) \
                and population_category(requested.population) \
                != population_category(observed.population):
            reasons.append(f"population mismatch: need={requested.population!r} "
                           f"export={observed.population!r}")
        if requested.membership_basis and observed.membership_basis \
                and _norm(requested.membership_basis) != _norm(observed.membership_basis):
            reasons.append(f"membership mismatch: need={requested.membership_basis!r} "
                           f"export={observed.membership_basis!r}")
        req_games = _norm_set(requested.game_types)
        obs_games = _norm_set(observed.game_types)
        if req_games and obs_games and not (req_games & obs_games):
            reasons.append(f"game type mismatch: need={sorted(req_games)} "
                           f"export={sorted(obs_games)}")
        req_seasons = {int(item) for item in requested.seasons if str(item).isdigit()}
        obs_seasons = {int(item) for item in observed.seasons if str(item).isdigit()}
        if req_seasons and obs_seasons and not (req_seasons & obs_seasons):
            reasons.append(f"season mismatch: need={sorted(req_seasons)} "
                           f"export={sorted(obs_seasons)}")
        if requested.time_range is not None and observed.time_range is not None:
            if (observed.time_range.end < requested.time_range.start
                    or observed.time_range.start > requested.time_range.end):
                reasons.append("time window does not overlap the requested window")
    return (not reasons), tuple(reasons)


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
    """Choose explicit bindings for one Need (compatibility-checked)."""
    bindings, _gaps = resolve_bindings_with_gaps(
        need, artifacts=artifacts, export_refs=export_refs, by_need=by_need,
        accepted_types=accepted_types)
    return bindings


def resolve_bindings_with_gaps(need: Need, *, artifacts: tuple[RuntimeArtifact, ...],
                               export_refs: dict[str, str],
                               by_need: dict[str, tuple[str, ...]],
                               accepted_types: tuple[str, ...]
                               ) -> tuple[tuple[InputBinding, ...], tuple[str, ...]]:
    """Choose explicit bindings for one Need and report incompatible candidates.

    Only the Need's own ``input_refs`` and the artifacts of its declared dependencies are
    eligible. Returns the chosen bindings plus structured gaps for exports that matched
    by type but were rejected as incompatible (wrong entity/scope/state).
    """
    by_id = {artifact.artifact_id: artifact for artifact in artifacts}
    wanted = set(accepted_types)
    bindings: list[InputBinding] = []
    gaps: list[str] = []
    bound_exports: set[str] = set()

    def _consider(export, ref_id: str, source_need_id: str,
                  source_artifact_id: str) -> None:
        if wanted and export.export_type not in wanted:
            return
        if export.export_id in bound_exports:
            return
        artifact = by_id.get(source_artifact_id)
        ok, reasons = export_compatible(need, export, artifact) if artifact is not None \
            else (export.accepted, ())
        if not ok:
            gaps.append(f"export {export.export_id} [{export.export_type}] from "
                        f"{source_artifact_id} is incompatible: {', '.join(reasons)}")
            return
        bindings.append(InputBinding(
            binding_id=f"{need.need_id}:{export.export_id}",
            name=export.export_type, export_type=export.export_type,
            source_need_id=source_need_id, source_artifact_id=source_artifact_id,
            source_export_id=export.export_id, ref_id=ref_id))
        bound_exports.add(export.export_id)

    # 1. Explicit planner-declared refs win, but only if they resolve to real exports.
    for ref in need.input_refs:
        export = resolve_export_ref(_ref_context(artifacts, export_refs), ref)
        if export is None:
            continue
        source_artifact = _artifact_of_export(artifacts, export.export_id)
        _consider(export, ref, _need_for_artifact(by_need, source_artifact), source_artifact)

    # 2. Otherwise bind from declared dependencies, in declaration order.
    for dependency in need.depends_on:
        for artifact_id in by_need.get(dependency, ()):
            artifact = by_id.get(artifact_id)
            if artifact is None:
                continue
            for export in artifact.exports:
                ref_id = export_refs.get(export.export_id, "")
                _consider(export, ref_id, dependency, artifact_id)
    return tuple(bindings), tuple(dict.fromkeys(gaps))


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
