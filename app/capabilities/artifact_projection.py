"""artifact_projection: an explicit, lineage-preserving analytical -> searchable conversion.

F2 (ADR 0019). A verified database analysis may contain the subject a later tool needs
(the top-ranked hitter, a player with the biggest change, a specific row). The Binder is
correctly strict: an analytics artifact exports ``RANKED_ENTITY_SET`` / ``STATISTICAL_RESULT``,
while ``web_research`` and ``entity_resolution`` consume ``SEARCH_QUERY``. This capability is
the missing *typed converter*.

Safety rules (Phase 5A closure):

* Default ``TOP`` is only allowed when the source artifact has a *verified ordering* (an
  analytical sort), or when it has at most one row. An unsorted multi-row artifact requires
  an explicit ``INDEX`` / ``ENTITY_ID`` selection.
* A generic ``id`` / ``entity_id`` column is never interpreted as an MLBAM player id. Only
  the catalog-validated ``batter_id`` / ``pitcher_id`` / ``player_id`` columns (or an
  artifact whose enumeration declares ``entity_kind=PLAYER`` / ``namespace=MLBAM``) may
  produce a ``PLAYER_ID_SET``.
* Every produced export carries the source artifact/export, the selected row identity, the
  selection rule, the original scope and the lineage.

No LLM is involved: the projection is deterministic and auditable.
"""

from __future__ import annotations

from typing import Any

from app.capabilities.base import CapabilityResult, CapabilitySpec
from app.contracts.models import (
    Artifact,
    Export,
    Reference,
    ReferenceType,
    Scope,
    SelectionRule,
    ToolOutcome,
    ToolRequest,
)

_CONSUMABLE_TYPES = ("RANKED_ENTITY_SET", "STATISTICAL_RESULT", "DERIVED_MEASURE")
_NAME_CANDIDATES = ("player_name", "batter_name", "pitcher_name", "entity_name", "name", "full_name")
# Catalog-validated MLBAM player identity columns.
_PLAYER_ID_COLUMNS = ("batter_id", "pitcher_id", "player_id", "mlbam_id")
# Generic identifiers: selectable, but never canonical and never emitted as PLAYER_ID_SET.
_GENERIC_ID_COLUMNS = ("entity_id", "id")
_ALL_ID_COLUMNS = _PLAYER_ID_COLUMNS + _GENERIC_ID_COLUMNS


class ArtifactProjectionTool:
    def __init__(self):
        self.spec = CapabilitySpec(
            name="artifact_projection",
            description=(
                "Select an explicit row from an analytical artifact and project it into a "
                "searchable SEARCH_QUERY / canonical PLAYER_ID_SET with full lineage."
            ),
            accepts=list(_CONSUMABLE_TYPES),
            produces=["SEARCH_QUERY", "PLAYER_ID_SET", "ENTITY_MAPPING"],
            # The type actually available is checked at execution: a rank artifact is the
            # normal input, but a scalar/derived artifact may be projectable too.
            required_inputs=[],
        )

    # ------------------------------------------------------------------ execution
    def execute(self, request: ToolRequest, ctx=None) -> CapabilityResult:
        rows, columns, source_artifact, source_export = self._bound_rows(request, ctx)
        if source_artifact is None:
            return _blocked(request, "INPUT_UNRESOLVED", "artifact_projection needs a bound RANKED_ENTITY_SET / STATISTICAL_RESULT / DERIVED_MEASURE")
        if not rows:
            return CapabilityResult(
                None,
                ToolOutcome(
                    status="NO_DATA",
                    code="VALID_EMPTY",
                    message="the source artifact contains no row to project",
                    need_id=request.need_id,
                    tool_request_id=request.id,
                    classification="VALID_EMPTY",
                ),
            )

        rule = _selection_rule(request.inputs, source_artifact, source_export)
        if rule.mode == "TOP" and len(rows) > 1 and not _ordering_verified(source_artifact, source_export):
            return _blocked(
                request,
                "UNVERIFIED_ORDERING",
                "TOP is only deterministic for a verified ordering; give an explicit INDEX or ENTITY_ID",
            )

        selected = _select(rows, rule)
        if selected is None:
            return _blocked(request, "SELECTION_FAILED", f"the selection rule {rule.mode!r} did not match any row")

        identity = _row_identity(selected, columns, request.inputs, source_artifact)
        name = identity.get("name")
        if not name:
            return _blocked(request, "SELECTION_FAILED", "the selected row carries no searchable name column")

        canonical_id = identity.get("canonical_id")
        raw_id = identity.get("raw_id")
        namespace = identity.get("namespace") if canonical_id else None
        scope = _copy_scope(source_artifact.actual_scope) or Scope()
        scope.entity_names = _dedupe(list(scope.entity_names) + [str(name)])
        if canonical_id:
            scope.entities = _dedupe(list(scope.entities) + [str(canonical_id)])

        lineage = _dedupe([source_artifact.id, source_export.id, *source_artifact.lineage])
        selection_payload = {
            "source_artifact_id": source_artifact.id,
            "source_export_id": source_export.id,
            "source_export_type": source_export.type,
            "selected_row_identity": identity,
            "selection_rule": rule.model_dump(),
            "canonical_id": str(canonical_id) if canonical_id else None,
            "raw_id": raw_id,
            "identity_kind": identity.get("id_kind", "UNKNOWN"),
            "identity_namespace": namespace,
            "identity_verified": bool(identity.get("identity_verified")),
            "searchable_name": str(name),
            "original_scope": source_artifact.actual_scope.model_dump() if source_artifact.actual_scope else {},
            "lineage": lineage,
        }
        provenance_block = {
            "producer": "artifact_projection",
            "source_artifact_id": source_artifact.id,
            "source_export_id": source_export.id,
            "selection_rule": rule.model_dump(),
            "original_scope": selection_payload["original_scope"],
            "lineage": lineage,
        }

        exports = [
            Export(
                id=f"exp_{request.id}_search",
                type="SEARCH_QUERY",
                payload={
                    "subjects": [str(name)],
                    "query": str(name),
                    "canonical_id": str(canonical_id) if canonical_id else None,
                    "namespace": namespace,
                    **selection_payload,
                },
                contract={**provenance_block, "searchable": True},
            ),
            Export(
                id=f"exp_{request.id}_mapping",
                type="ENTITY_MAPPING",
                payload={
                    "resolved": (
                        [
                            {
                                "mention": str(name),
                                "id": int(canonical_id),
                                "name": str(name),
                                "role": "",
                                "namespace": namespace,
                            }
                        ]
                        if canonical_id
                        else []
                    ),
                    "candidates": ([] if canonical_id else [{"mention": str(name), "id": None, "name": str(name)}]),
                    "selection": selection_payload,
                },
                contract=provenance_block,
            ),
        ]
        if canonical_id:
            exports.append(
                Export(
                    id=f"exp_{request.id}_players",
                    type="PLAYER_ID_SET",
                    payload={
                        "ids": [int(canonical_id)],
                        "names": [str(name)],
                        "role": identity.get("id_kind", "PLAYER"),
                        "resolved": [
                            {
                                "mention": str(name),
                                "id": int(canonical_id),
                                "name": str(name),
                                "namespace": namespace,
                            }
                        ],
                    },
                    contract={
                        **provenance_block,
                        "namespace": namespace or "MLBAM",
                        "cardinality": "SET",
                        "entity": "PLAYER",
                        "selected_row_identity": identity,
                    },
                )
            )

        artifact = Artifact(
            kind="ENTITY_PROJECTION",
            structured_data={
                "selection": selection_payload,
                "selected_row": selected,
                "rows_available": len(rows),
                "columns": list(columns),
            },
            text_content=(
                f"selected {name}"
                + (f" (id {canonical_id})" if canonical_id else "")
                + f" from {source_artifact.id}:{source_export.type} via {rule.mode}"
            ),
            exports=exports,
            provenance={
                "capability": "artifact_projection",
                "request_id": request.id,
                "source_artifact_id": source_artifact.id,
                "source_export_id": source_export.id,
                "selection_rule": rule.model_dump(),
                "identity_verified": bool(identity.get("identity_verified")),
            },
            references=[
                Reference(
                    ref_type=ReferenceType.ARTIFACT_EXPORT,
                    ref_id=source_export.id,
                    source=f"artifact:{source_artifact.id}",
                    span=str(selected)[:500],
                )
            ],
            requested_scope=source_artifact.requested_scope,
            actual_scope=scope,
            lineage=lineage,
            confidence="ACCEPTABLE",
            status="ACCEPTED",
            need_id=request.need_id,
        )
        return CapabilityResult(
            artifact,
            ToolOutcome(
                status="SUCCESS",
                code="OK",
                message=f"projected '{name}' from {source_artifact.id}",
                need_id=request.need_id,
                tool_request_id=request.id,
            ),
        )

    # ------------------------------------------------------------------ helpers
    def _bound_rows(self, request: ToolRequest, ctx):
        for binding in request.bindings:
            export = ctx.find_export(binding.export_id) if ctx else None
            if export is None or export.type not in _CONSUMABLE_TYPES:
                continue
            payload = export.payload if isinstance(export.payload, dict) else {}
            rows = payload.get("rows") or []
            if not isinstance(rows, list):
                rows = []
            columns = list(payload.get("columns") or (list(rows[0].keys()) if rows and isinstance(rows[0], dict) else []))
            artifact = ctx.artifact_for_export(export.id) if ctx else None
            if artifact is not None:
                return rows, columns, artifact, export
        return [], [], None, None


def _blocked(request: ToolRequest, code: str, message: str) -> CapabilityResult:
    return CapabilityResult(
        None,
        ToolOutcome(
            status="BLOCKED",
            code=code,
            message=message,
            need_id=request.need_id,
            tool_request_id=request.id,
            classification="WRONG_BINDING",
        ),
    )


def _ordering_verified(artifact: Artifact, export: Export) -> bool:
    enumeration = artifact.enumeration
    if enumeration is not None and enumeration.ordering:
        if enumeration.ordering_verified:
            return True
    if isinstance(export.contract, dict) and export.contract.get("verified_ordering") and export.contract.get("ordering"):
        return True
    return False


def _selection_rule(inputs: dict[str, Any], artifact: Artifact, export: Export) -> SelectionRule:
    selection = inputs.get("selection")
    order_by = ""
    if artifact.enumeration is not None and artifact.enumeration.ordering:
        order_by = artifact.enumeration.ordering[0]
    elif isinstance(export.contract, dict) and export.contract.get("ordering"):
        order_by = str(export.contract["ordering"][0])
    if isinstance(selection, dict):
        mode = str(selection.get("mode") or "TOP").upper()
        return SelectionRule(
            mode=mode,
            by=str(selection.get("by") or order_by),
            index=_safe_int(selection.get("index")),
            entity_id=str(selection["entity_id"]) if selection.get("entity_id") is not None else None,
            detail=str(selection.get("detail") or ""),
        )
    if inputs.get("row_index") is not None:
        return SelectionRule(mode="INDEX", by=order_by, index=_safe_int(inputs.get("row_index")), detail="explicit row index")
    if inputs.get("entity_id") is not None:
        return SelectionRule(mode="ENTITY_ID", by=order_by, entity_id=str(inputs["entity_id"]), detail="explicit entity id")
    return SelectionRule(
        mode="TOP",
        by=order_by,
        detail="first row of the artifact (requires a verified analytical ordering)",
    )


def _select(rows: list[dict[str, Any]], rule: SelectionRule) -> dict[str, Any] | None:
    if rule.mode == "TOP":
        return rows[0]
    if rule.mode == "INDEX":
        index = rule.index if rule.index is not None else 0
        if 0 <= index < len(rows):
            return rows[index]
        return None
    if rule.mode == "ENTITY_ID":
        target = str(rule.entity_id)
        for row in rows:
            if not isinstance(row, dict):
                continue
            for column in _ALL_ID_COLUMNS:
                if column in row and str(row[column]) == target:
                    return row
        return None
    return None


def _row_identity(
    row: dict[str, Any], columns: list[str], inputs: dict[str, Any], source_artifact: Artifact
) -> dict[str, Any]:
    name_columns = [str(c) for c in (inputs.get("name_columns") or [])]
    id_columns = [str(c) for c in (inputs.get("entity_id_columns") or [])]
    if not name_columns:
        name_columns = [c for c in _NAME_CANDIDATES if c in row] or [c for c in columns if str(c).endswith("_name")]
    if not id_columns:
        id_columns = [c for c in _ALL_ID_COLUMNS if c in row]
    identity: dict[str, Any] = {}
    if name_columns:
        identity["name"] = str(row.get(name_columns[0]) or "")
        identity["name_column"] = name_columns[0]
    if id_columns:
        id_column = id_columns[0]
        value = row.get(id_column)
        parsed = _safe_int(value)
        identity["id_column"] = id_column
        source_kind, source_namespace = _source_identity_domain(source_artifact)
        if id_column in _PLAYER_ID_COLUMNS or (source_kind == "PLAYER" and id_column not in _GENERIC_ID_COLUMNS):
            identity["canonical_id"] = parsed if parsed is not None else value
            identity["id_kind"] = "PLAYER"
            identity["namespace"] = source_namespace or "MLBAM"
            identity["identity_verified"] = source_kind == "PLAYER" or id_column in _PLAYER_ID_COLUMNS
        else:
            # A generic identifier is recorded for the audit trail only.
            identity["raw_id"] = parsed if parsed is not None else value
            identity["id_kind"] = "UNKNOWN"
            identity["namespace"] = None
            identity["identity_verified"] = False
    identity["row"] = {key: row.get(key) for key in columns} if columns else dict(row)
    return identity


def _source_identity_domain(artifact: Artifact) -> tuple[str | None, str | None]:
    enumeration = artifact.enumeration
    if enumeration is not None and enumeration.entity_kind:
        return str(enumeration.entity_kind).upper(), (str(enumeration.entity_namespace).upper() if enumeration.entity_namespace else None)
    return None, None


def _copy_scope(scope: Scope | None) -> Scope | None:
    if scope is None:
        return None
    return scope.model_copy(deep=True)


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = str(value)
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result
