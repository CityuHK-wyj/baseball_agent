"""Analytical tools: batting stats, IR-driven local analytics, and safe compute.

All tools accept Artifact references as inputs and produce reusable Artifacts as
outputs, so tool direction is compositional: Web -> SQL, SQL -> Compute, Compute -> Web,
roster -> SQL, and so on.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.models.artifact_runtime import (EvidenceSource, RuntimeArtifact, Scope,
                                         ToolCapabilityContract, ToolRequest)
from app.models.contracts import TimeRange
from app.artifact_runtime.analytical_ir import AnalyticalQuery, Between, Compare
from app.artifact_runtime.ir_compiler import compile_analytical_query
from app.artifact_runtime.scope import compare_scope
from app.artifact_runtime.tool_base import (RuntimeTool, ToolContext, ToolOutcome, build_export,
                                   ids_from_inputs, resolve_export_ref)
from app.tools.batting import BattingStatsUnavailable


def _scope_from_inputs(value) -> Scope | None:
    if isinstance(value, Scope):
        return value
    if isinstance(value, dict):
        try:
            return Scope.model_validate(value)
        except Exception:  # noqa: BLE001 - a malformed scope is simply absent
            return None
    return None


def _metric_scope(metric: str) -> Scope:
    return Scope(metric=metric.upper())


class BattingTool(RuntimeTool):
    """Season or date-range batting lines. Honors date windows and id sets."""

    contract = ToolCapabilityContract(
        name="batting_stats",
        accepts=("PLAYER_NAME", "PLAYER_ID_SET", "SEASON", "DATE_RANGE"),
        produces=("STATISTICAL_RESULT", "RANKED_ENTITY_SET"),
        description="Live batting lines by season or explicit date range.",
        temporal_modes=("SEASON", "ARBITRARY_DATE_RANGE"),
        supported_measures=("AVG", "OBP", "SLG", "OPS", "HR", "RBI", "SB", "H",
                            "BB", "SO"),
        game_types=("REGULAR_SEASON",), authority="SOURCE_BACKED")

    def run(self, request: ToolRequest, context: ToolContext) -> ToolOutcome:
        if context.batting is None:
            return ToolOutcome(recovery_code="BATTING_STATS_TOOL_UNAVAILABLE",
                               detail="batting stats are not configured")
        inputs = request.structured_inputs
        metric = str(inputs.get("metric") or "OPS").upper()
        if metric not in self.contract.supported_measures:
            # Never silently downgrade an unsupported measure to OPS.
            return ToolOutcome(
                recovery_code="UNSUPPORTED_OPERATION",
                detail=f"batting provider does not support measure {metric!r}; "
                       f"supported: {', '.join(self.contract.supported_measures)}")
        names = tuple(str(item) for item in (inputs.get("names") or ()) if str(item).strip())
        player_ids = tuple(str(item) for item in ids_from_inputs(context, request))
        start = inputs.get("start")
        end = inputs.get("end")
        year = inputs.get("season")
        try:
            if start and end:
                evidence = context.batting.range_evidence(
                    str(start), str(end), names=names, metric=metric, player_ids=player_ids)
                actual_time = TimeRange(start=date.fromisoformat(str(start)),
                                        end=date.fromisoformat(str(end)))
            else:
                season = int(year or context.today.year)
                evidence = context.batting.season_evidence(
                    season, names=names, metric=metric, player_ids=player_ids)
                actual_time = TimeRange(start=date(season, 1, 1), end=date(season, 12, 31))
        except BattingStatsUnavailable as error:
            return ToolOutcome(recovery_code="BATTING_STATS_UNAVAILABLE", detail=str(error))
        except (ValueError, TypeError) as error:
            return ToolOutcome(recovery_code="INVALID_TIME_RANGE", detail=str(error))
        rows = tuple(evidence.data.get("rows", ()))
        entities = tuple(str(row.get("name")) for row in rows if row.get("name"))
        requested = _scope_from_inputs(inputs.get("requested_scope"))
        season_value = None if start else int(year or context.today.year)
        actual = Scope(entities=entities, population="players", time_range=actual_time,
                       seasons=(season_value,) if season_value else (),
                       metric=metric, source_coverage=("baseball-reference",))
        receipt = {
            "mode": "range" if start else "season",
            "season": season_value, "start": str(start) if start else "",
            "end": str(end) if end else "", "metric": metric,
            "entities": list(entities), "population": "players",
            "source_coverage": ["baseball-reference"],
            "source_snapshot": f"bref:{evidence.reference}",
            "row_count": len(rows), "evidence_refs": []}
        artifact = RuntimeArtifact(
            artifact_id=context.artifacts.next_id("batting"), kind="batting_stats",
            structured_data={"rows": list(rows), "metric": metric},
            text_content=evidence.text, references=(),
            requested_scope=requested, actual_scope=actual,
            provenance=(EvidenceSource(source="baseball-reference", source_kind="STATS_API",
                                       reference=evidence.reference, title=evidence.summary),),
            confidence=0.8 if rows else 0.1, status="OK" if rows else "EMPTY",
            metadata={"execution_receipt": receipt})
        exports = [build_export(artifact, "STATISTICAL_RESULT",
                                {"columns": list(rows[0].keys()) if rows else [],
                                 "rows": [list(row.values()) for row in rows]},
                                provenance="baseball-reference", confidence=artifact.confidence)]
        ranked = sorted([row for row in rows if row.get(metric) is not None],
                        key=lambda row: row.get(metric), reverse=True)
        if ranked:
            exports.append(build_export(
                artifact, "RANKED_ENTITY_SET",
                [{"player_id": row.get("player_id"), "name": row.get("name"),
                  "value": row.get(metric)} for row in ranked],
                provenance="baseball-reference", confidence=artifact.confidence))
        artifact = artifact.model_copy(update={"exports": tuple(exports)})
        context.artifacts.add(artifact)
        return ToolOutcome(artifacts=(artifact,))


class LocalAnalyticsTool(RuntimeTool):
    """Safe Analytical IR -> SchemaCatalog validation -> deterministic SQL -> read-only.

    Accepts a PLAYER_ID_SET (for example from a roster or entity artifact) and runs a
    bounded analytical query. Invalid IR returns a structured recovery code; the
    unsupported portion is never silently removed.
    """

    contract = ToolCapabilityContract(
        name="local_analytics",
        accepts=("PLAYER_ID_SET", "DATE_RANGE", "ANALYTICAL_QUERY"),
        produces=("STATISTICAL_RESULT", "RANKED_ENTITY_SET", "DERIVED_MEASURE"),
        description="Strict read-only Statcast analytics over catalog-approved fields.",
        cost=2)

    def run(self, request: ToolRequest, context: ToolContext) -> ToolOutcome:
        raw = request.structured_inputs.get("analytical_query")
        if not isinstance(raw, dict):
            return ToolOutcome(recovery_code="MISSING_ANALYTICAL_QUERY",
                               detail="local analytics needs an analytical_query IR")
        try:
            query = AnalyticalQuery.model_validate(raw)
        except Exception as error:  # noqa: BLE001 - malformed IR is a planning signal
            return ToolOutcome(recovery_code="INVALID_IR",
                               detail=f"{type(error).__name__}: {error}")
        # A planner-provided window is applied as a real date predicate by the compiler
        # (single source of truth), so declared and executed window cannot diverge.
        inputs = request.structured_inputs
        if query.window is None and inputs.get("start") and inputs.get("end"):
            try:
                query = query.model_copy(update={"window": TimeRange(
                    start=date.fromisoformat(str(inputs["start"])),
                    end=date.fromisoformat(str(inputs["end"])))})
            except ValueError:
                pass
        if query.window is not None and not query.date_field:
            query = query.model_copy(update={"date_field": "game_date"})
        # An entity-set filter may name the field and leave the export to be resolved
        # from the request's referenced Artifacts (composition, not a hard-coded join).
        entity_labels: list[str] = []
        upstream: list[str] = []
        resolve = lambda ref: resolve_export_ref(context, ref)  # noqa: E731
        if query.entity_set is not None:
            export = resolve(query.entity_set.export_ref) if query.entity_set.export_ref \
                else None
            if export is None or export.export_type != "PLAYER_ID_SET":
                # A model-supplied export_ref that names a need id (or is otherwise
                # unresolvable) is replaced by the compatible export the engine already
                # bound explicitly from this Need's declared dependencies. This is still
                # explicit binding, not ambient export injection.
                replacement = None
                for ref in request.input_refs:
                    candidate = resolve(ref)
                    if candidate is not None and candidate.export_type == "PLAYER_ID_SET":
                        replacement = (ref, candidate)
                        break
                if replacement is None:
                    return ToolOutcome(
                        recovery_code="MISSING_ENTITY_SET",
                        detail="the entity-set filter needs a bound PLAYER_ID_SET export")
                ref, export = replacement
                query = query.model_copy(update={
                    "entity_set": query.entity_set.model_copy(update={"export_ref": ref})})
            if isinstance(export.metadata, dict) and export.metadata.get("team"):
                entity_labels.append(str(export.metadata["team"]))
            source_ref = context.refs.maybe(query.entity_set.export_ref)
            if source_ref is not None:
                upstream.append(source_ref.target_id)
        relation = context.relation_sql(query.source_kind, query.table)
        compiled = compile_analytical_query(query, context.catalog, resolve_export=resolve,
                                            relation_sql=relation)
        requested = _scope_from_inputs(request.structured_inputs.get("requested_scope"))
        if not compiled.ok:
            artifact = RuntimeArtifact(
                artifact_id=context.artifacts.next_id("local-ir"), kind="analytical_diagnostic",
                structured_data={"query": query.model_dump(mode="json"),
                                 "recovery_code": compiled.code, "detail": compiled.detail},
                requested_scope=requested, actual_scope=None,
                status="INVALID", confidence=0.0,
                metadata={"recovery_code": compiled.code})
            context.artifacts.add(artifact)
            return ToolOutcome(artifacts=(artifact,), recovery_code=compiled.code,
                               detail=compiled.detail)
        executor = (context.postgres_executor if query.source_kind == "POSTGRES"
                    else context.parquet_executor)
        if executor is None:
            return ToolOutcome(recovery_code="EXECUTOR_UNAVAILABLE",
                               detail=f"no executor for {query.source_kind}")
        tool_result, rows = executor.execute_with_rows(compiled.sql)
        if tool_result.status not in ("OK", "EMPTY"):
            return ToolOutcome(recovery_code=tool_result.error_code or "SOURCE_QUERY_FAILED",
                               detail=tool_result.safe_error_summary or "query failed")
        columns = [selection.alias for selection in query.selections]
        ordered_rows = [list(row) for row in rows]
        upstream_membership = ""
        upstream_seasons: tuple[str, ...] = ()
        for ref in request.input_refs:
            export = resolve_export_ref(context, ref)
            if export is None or export.export_type != "PLAYER_ID_SET":
                continue
            metadata = export.metadata if isinstance(export.metadata, dict) else {}
            upstream_membership = str(metadata.get("membership_basis")
                                      or upstream_membership)
        actual = Scope(
            entities=tuple(dict.fromkeys((
                *entity_labels,
                *(context.player_names.get(str(row[0]), str(row[0]))
                  for row in ordered_rows if row and str(row[0]).isdigit())))),
            population="players", time_range=query.window,
            membership_basis=upstream_membership,
            seasons=(tuple(period.time_range.start.year for period in query.periods)
                     if query.periods else ()),
            game_types=compiled.game_types,
            metric="+".join(compiled.applied_fields), event_population="pitch",
            aggregation=compiled.aggregation,
            qualification=compiled.qualification,
            source_coverage=(query.source_kind,))
        # A truthful execution receipt: the verifier reads this, never the declared scope,
        # to establish what actually ran.
        receipt = {
            "source_kind": query.source_kind, "table": query.table,
            "applied_window": (compiled.applied_window[0], compiled.applied_window[1])
            if compiled.applied_window else None,
            "game_types": list(compiled.game_types),
            "measure": "+".join(compiled.applied_fields) if compiled.applied_fields else "",
            "aggregation": compiled.aggregation, "qualification": compiled.qualification,
            "population": "players", "membership_basis": upstream_membership,
            "canonical_entities": list(entity_labels),
            "source_coverage": [query.source_kind],
            "source_snapshot": f"{query.source_kind}:{query.table}:{compiled.ir_digest}",
            "ir_digest": compiled.ir_digest, "row_count": len(ordered_rows),
            "truncated": bool(query.limit is not None and len(ordered_rows) >= query.limit),
            "evidence_refs": list(compiled.referenced_exports),
        }
        artifact = RuntimeArtifact(
            artifact_id=context.artifacts.next_id("local"), kind="analytics",
            structured_data={"columns": columns, "rows": ordered_rows,
                             "source_kind": query.source_kind, "sql": compiled.sql,
                             "ir_digest": compiled.ir_digest},
            text_content=_render(columns, ordered_rows),
            requested_scope=requested, actual_scope=actual,
            lineage=tuple(dict.fromkeys(upstream)),
            provenance=(EvidenceSource(source=f"statcast-{query.source_kind.lower()}",
                                       source_kind=query.source_kind,
                                       reference=f"{query.source_kind}:{query.table}",
                                       retrieved_at=datetime.now(timezone.utc)),),
            confidence=0.85 if ordered_rows else 0.2,
            status="OK" if ordered_rows else "EMPTY",
            metadata={"applied_fields": list(compiled.applied_fields),
                      "referenced_exports": list(compiled.referenced_exports),
                      "execution_receipt": receipt})
        exports = [build_export(artifact, "STATISTICAL_RESULT",
                                {"columns": columns, "rows": ordered_rows},
                                provenance=query.source_kind, confidence=artifact.confidence)]
        identifier_index = _identifier_index(query, columns)
        if identifier_index is not None:
            exports.append(build_export(
                artifact, "RANKED_ENTITY_SET",
                [{"id": row[identifier_index], "name": context.player_names.get(str(row[identifier_index]), "")}
                 for row in ordered_rows],
                provenance=query.source_kind, confidence=artifact.confidence))
        derived = _derived_export(query, columns, ordered_rows, artifact)
        if derived is not None:
            exports.append(derived)
        artifact = artifact.model_copy(update={"exports": tuple(exports)})
        context.artifacts.add(artifact)
        return ToolOutcome(artifacts=(artifact,), applied_fields=compiled.applied_fields,
                           referenced_exports=compiled.referenced_exports)


def _references_field(query: AnalyticalQuery, field: str) -> bool:
    def walk(condition) -> bool:
        if isinstance(condition, Between):
            return condition.field == field
        if isinstance(condition, Compare):
            return condition.field == field
        children = getattr(condition, "conditions", None)
        if children:
            return any(walk(child) for child in children)
        inner = getattr(condition, "condition", None)
        return walk(inner) if inner is not None else False
    return any(walk(condition) for condition in query.filters)


def _identifier_index(query: AnalyticalQuery, columns: list[str]) -> int | None:
    from app.artifact_runtime.schema_catalog import SchemaCatalog  # noqa: F401 - docs only
    for selection in query.selections:
        if selection.kind == "GROUP_KEY" and selection.alias in columns:
            if selection.field.endswith("_id") or selection.field in ("batter", "pitcher"):
                return columns.index(selection.alias)
    return None


def _derived_export(query: AnalyticalQuery, columns: list[str], rows: list[list],
                    artifact: RuntimeArtifact):
    derived = [selection for selection in query.selections if selection.kind == "DERIVED"]
    if not derived:
        return None
    summary: dict[str, float | None] = {}
    for selection in derived:
        index = columns.index(selection.alias)
        values = [row[index] for row in rows if isinstance(row[index], (int, float))]
        summary[selection.alias] = (sum(values) / len(values)) if values else None
    return build_export(artifact, "DERIVED_MEASURE", summary,
                        provenance="derived", confidence=artifact.confidence,
                        metadata={"columns": [item.alias for item in derived]})


def _render(columns: list[str], rows: list[list]) -> str:
    if not columns:
        return ""
    header = " | ".join(columns)
    body = [" | ".join("" if cell is None else str(cell) for cell in row) for row in rows]
    return "\n".join([header, *body])[:6000]


class ComputeTool(RuntimeTool):
    """Safe compute over already-aggregated Artifact exports. No eval, no SQL."""

    contract = ToolCapabilityContract(
        name="compute",
        accepts=("STATISTICAL_RESULT", "DERIVED_MEASURE", "RANKED_ENTITY_SET"),
        produces=("DERIVED_MEASURE", "STATISTICAL_RESULT", "RANKED_ENTITY_SET"),
        description="Comparisons, differences, rates, ranking over aggregated artifacts.",
        authority="DERIVED")

    def run(self, request: ToolRequest, context: ToolContext) -> ToolOutcome:
        op = str(request.structured_inputs.get("op") or "").upper()
        if op in ("DIFFERENCE", "RATIO", "PERCENTAGE"):
            left, error = _scalar(context, request.structured_inputs.get("left_ref"),
                                  request.structured_inputs.get("left_field"))
            if error:
                return ToolOutcome(recovery_code="COMPUTE_INPUT_MISSING", detail=error)
            right, error = _scalar(context, request.structured_inputs.get("right_ref"),
                                   request.structured_inputs.get("right_field"))
            if error:
                return ToolOutcome(recovery_code="COMPUTE_INPUT_MISSING", detail=error)
            if op == "DIFFERENCE":
                value = left - right
            elif op == "RATIO":
                value = None if right == 0 else left / right
            else:
                value = None if right == 0 else 100.0 * left / right
            label = str(request.structured_inputs.get("label") or op.lower())
            return self._numeric_artifact(context, request, label, value)
        if op == "MEAN":
            values: list[float] = []
            for ref in request.input_refs:
                number, error = _scalar(context, ref, None)
                if error is None and number is not None:
                    values.append(float(number))
            if not values:
                return ToolOutcome(recovery_code="COMPUTE_INPUT_MISSING",
                                   detail="mean needs at least one numeric input")
            return self._numeric_artifact(context, request, "mean", sum(values) / len(values))
        if op == "RANK":
            return self._rank(context, request)
        return ToolOutcome(recovery_code="UNSUPPORTED_COMPUTE_OPERATION",
                           detail=f"{op!r} is not a supported compute operation")

    def _numeric_artifact(self, context: ToolContext, request: ToolRequest, label: str,
                          value: float | None) -> ToolOutcome:
        artifact = RuntimeArtifact(
            artifact_id=context.artifacts.next_id("compute"), kind="derived",
            structured_data={"label": label, "value": value},
            text_content=f"{label}: {value}",
            requested_scope=_scope_from_inputs(request.structured_inputs.get("requested_scope")),
            actual_scope=_scope_from_inputs(request.structured_inputs.get("actual_scope"))
            or Scope(note=label),
            lineage=tuple(request.input_refs), confidence=0.8 if value is not None else 0.2,
            status="OK" if value is not None else "EMPTY",
            metadata={"execution_receipt": {
                "aggregation": label, "inputs": list(request.input_refs),
                "source_snapshot": label}})
        artifact = artifact.model_copy(update={"exports": (
            build_export(artifact, "DERIVED_MEASURE", {"label": label, "value": value},
                         provenance="compute", confidence=artifact.confidence),)})
        context.artifacts.add(artifact)
        return ToolOutcome(artifacts=(artifact,))

    def _rank(self, context: ToolContext, request: ToolRequest) -> ToolOutcome:
        ref = request.structured_inputs.get("input_ref")
        field = request.structured_inputs.get("field")
        export = resolve_export_ref(context, ref) if ref else None
        if export is None or not isinstance(export.value, dict):
            return ToolOutcome(recovery_code="COMPUTE_INPUT_MISSING",
                               detail="rank needs a tabular input export")
        columns = list(export.value.get("columns", ()))
        rows = list(export.value.get("rows", ()))
        if not columns or not rows:
            return ToolOutcome(recovery_code="COMPUTE_INPUT_EMPTY", detail="rank input is empty")
        index = columns.index(field) if field in columns else 0
        ranked = sorted(rows, key=lambda row: (row[index] is None, row[index]),
                        reverse=str(request.structured_inputs.get("direction", "DESC")).upper() == "DESC")
        artifact = RuntimeArtifact(
            artifact_id=context.artifacts.next_id("compute"), kind="ranked",
            structured_data={"columns": columns, "rows": ranked},
            text_content=_render(columns, ranked), lineage=tuple(request.input_refs),
            requested_scope=_scope_from_inputs(request.structured_inputs.get("requested_scope")),
            actual_scope=export.metadata.get("scope") if isinstance(export.metadata, dict) else None,
            confidence=0.8, status="OK" if ranked else "EMPTY")
        artifact = artifact.model_copy(update={"exports": (
            build_export(artifact, "RANKED_ENTITY_SET", ranked, provenance="compute",
                         confidence=artifact.confidence),)})
        context.artifacts.add(artifact)
        return ToolOutcome(artifacts=(artifact,))


def _scalar(context: ToolContext, ref, field) -> tuple[float | None, str | None]:
    if not ref:
        return None, "missing scalar reference"
    export = resolve_export_ref(context, str(ref))
    if export is None:
        return None, f"unknown export reference {ref!r}"
    value = export.value
    if isinstance(value, (int, float)):
        return float(value), None
    if isinstance(value, dict):
        if "value" in value and isinstance(value["value"], (int, float)):
            return float(value["value"]), None
        rows = value.get("rows") or []
        columns = value.get("columns") or []
        if field and field in columns and rows:
            cell = rows[0][columns.index(field)]
            if isinstance(cell, (int, float)):
                return float(cell), None
        for cell in value.values():
            if isinstance(cell, (int, float)):
                return float(cell), None
    return None, f"export {export.export_type!r} has no numeric value"
