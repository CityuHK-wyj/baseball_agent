"""local_analytics: the only capability that performs strong semantic convergence.

Analytical intent (open) -> SchemaCatalog slice -> LLM IR candidate -> validation ->
deterministic SQL compiler -> read-only guard -> read-only execution -> Database Artifact.
"""

from __future__ import annotations

import json
from typing import Any

from app.capabilities.analytics.catalog import CATALOG, SOURCE_PARQUET, SOURCE_POSTGRES
from app.capabilities.analytics.canonical import build_canonical_query, is_general_performance
from app.capabilities.analytics.compiler import SQLCompiler
from app.capabilities.analytics.executor import AnalyticalExecutor
from app.capabilities.analytics.guard import guard_sql
from app.capabilities.analytics.ir import AnalyticalQuery, Aggregate, IRError, validate_query
from app.capabilities.analytics.conformance import (
    OBSERVATION_COLUMN, attest, compare, digest, requested_contract, bind_obligations,
    scope_from_execution, scope_from_request, SourceAvailability,
    role_from_text,
)
from app.capabilities.analytics.measures import (
    MEASURES,
    MeasureResolution,
    declare_measures,
    resolve_texts,
)
from app.capabilities.base import CapabilityResult, CapabilitySpec, FAMILY_LOCAL_DATA_ANALYTICS
from app.contracts.models import (
    Artifact,
    EnumerationContract,
    Export,
    Reference,
    ReferenceType,
    Scope,
    ToolOutcome,
    ToolRequest,
)

SYSTEM_PROMPT = """You are the analytical IR builder for a baseball Statcast database.
You convert an open analytical request into a Safe Analytical IR JSON object.

HARD RULES
- Use ONLY field names that appear in the provided catalog. Unknown fields are rejected.
- Never write SQL. Never invent tables. Never use a field whose listed type does not match.
- Entity ids come from the provided entity bindings. Put them in entity_filters, using role
  BATTER for hitter analyses and PITCHER for pitcher analyses. Do not invent ids.
- Preserve the bound analytical_role independently, including whole-population queries.
  The role must agree with the subject identity/grouping; opponent filters do not relabel it.
- Global filters restrict the requested row population exactly. Do not add row restrictions
  to implement a Measure: put governed counting predicates inside COUNT_IF conditions.
- Follow bound_obligations for grouping, qualification, primary ordering, tie breaks and limit.
- Use `season` and `population` for competition scope. `population` must be one of the
  provided population names.
- Batter outcomes live in the `events` field (null on non-terminal pitches). Hits are
  single/double/triple/home_run. Walks are walk/intent_walk. HBP is hit_by_pitch.
  Strikeouts include BOTH strikeout and strikeout_double_play, using IN.
  At-bats = events that are not NULL and not one of the listed non-at-bat events.
- Derived metrics use aggregates and derived names only, combined with ADD/SUB/MUL/DIV/
  SAFE_DIV/PCT/DIFF/RATE. This is a closed expression tree; no free text math.
- Add a qualification when the user requires a minimum sample size.
- Use sort + limit for ranking questions.
- Output a compact JSON object only.

OUTPUT SCHEMA
{
  "relation": "pitch",
  "analytical_role": "BATTER" | "PITCHER" | null,
  "entity_filters": [{"role": "BATTER", "ids": [660271], "names": ["Shohei Ohtani"]}],
  "filters": [{"op": "COMPARE", "field": "pitch_type", "comparator": "=", "value": "FF"}],
  "population": "POSTSEASON" | null,
  "game_types": ["R"],
  "season": 2025,
  "date_range": {"start": "2025-09-01", "end": "2025-09-30"},
  "group_by": ["season"],
  "aggregates": [{"name": "n_pa", "function": "COUNT_NON_NULL", "field": "events"}],
  "derived": [{"name": "avg", "op": "DIV", "left": {"kind": "aggregate", "name": "hits"}, "right": {"kind": "aggregate", "name": "at_bats"}}],
  "sort": [{"name": "avg", "direction": "DESC"}],
  "limit": 10,
  "qualification": {"basis": "PA", "minimum": 25},
  "intent_summary": "short description"
}

AGGREGATE FUNCTIONS: COUNT (field may be "*"), COUNT_NON_NULL, COUNT_IF (needs condition),
COUNT_DISTINCT, AVG, SUM, MIN, MAX.
CONDITIONS: COMPARE {field, comparator, value}, BETWEEN {field, low, high}, IN {field, values},
IS_NULL {field}, IS_NOT_NULL {field}, AND/OR/NOT {conditions: [...]}.
QUALIFICATION BASIS: PA, BBE, PITCHES, GAMES, RESULTS.

WORKED EXAMPLE — single-season batting line for one batter
{
  "relation": "pitch",
  "entity_filters": [{"role": "BATTER", "ids": [660271], "names": ["Shohei Ohtani"]}],
  "population": "REGULAR_SEASON",
  "season": 2025,
  "aggregates": [
    {"name": "pa", "function": "COUNT_NON_NULL", "field": "events"},
    {"name": "hits", "function": "COUNT_IF", "condition": {"op": "IN", "field": "events", "values": ["single", "double", "triple", "home_run"]}},
    {"name": "at_bats", "function": "COUNT_IF", "condition": {"op": "AND", "conditions": [
        {"op": "IS_NOT_NULL", "field": "events"},
        {"op": "NOT", "conditions": [{"op": "IN", "field": "events", "values": ["walk", "intent_walk", "hit_by_pitch", "catcher_interf", "sac_fly", "sac_bunt", "sac_fly_double_play", "sac_bunt_double_play", "truncated_pa", "game_advisory"]}]}]}},
    {"name": "home_runs", "function": "COUNT_IF", "condition": {"op": "COMPARE", "field": "events", "comparator": "=", "value": "home_run"}}
  ],
  "derived": [
    {"name": "avg", "op": "SAFE_DIV", "left": {"kind": "aggregate", "name": "hits"}, "right": {"kind": "aggregate", "name": "at_bats"}}
  ]
}
COUNT_IF needs no `field`. COUNT/COUNT_NON_NULL on `events` counts plate appearances.

STANDARD METRIC DEFINITIONS (use these exactly)
- AVG = H / AB
- OBP = (H + BB + HBP) / (AB + BB + HBP + SF)
- SLG = TB / AB where TB = 1B + 2*2B + 3*3B + 4*HR
- OPS = OBP + SLG
- K% = 100 * strikeouts / PA ; BB% = 100 * walks / PA ; Hard-hit% = 100 * COUNT_IF(launch_speed >= 95) / COUNT_NON_NULL(launch_speed)
Compute every named numerator as its own aggregate (e.g. walks, hbp) and combine them with derived ADD/SAFE_DIV.
Every derived operand MUST reference an aggregate name or another derived name that you defined in this same object.
"""


class LocalAnalyticsTool:
    def __init__(self, executor: AnalyticalExecutor, llm, *, parquet_archive: str = ""):
        self.executor = executor
        self.llm = llm
        self.catalog = CATALOG
        self.compiler = SQLCompiler(self.catalog, parquet_archive=parquet_archive)
        self.spec = CapabilitySpec(
            name="local_analytics",
            description="Ad-hoc Statcast analysis over the read-only PostgreSQL/Parquet sources.",
            accepts=["PLAYER_ID_SET", "SCOPE"],
            produces=["STATISTICAL_RESULT", "RANKED_ENTITY_SET"],
            required_inputs=[],
            family=FAMILY_LOCAL_DATA_ANALYTICS,
        )

    # ------------------------------------------------------------------ helpers
    def _intent(self, request: ToolRequest) -> dict[str, Any]:
        intent = dict(request.inputs.get("intent") or {})
        constraints = dict(request.inputs.get("constraints") or {})
        entities: list[dict[str, Any]] = []
        for dataset in request.inputs.get("entity_sets", []) or []:
            entities.append(dataset)
        merged = {
            "analytical_role": intent.get("analytical_role") or request.inputs.get("entity_role"),
            "objective": intent.get("objective") or request.objective,
            "measure": _coerce_text(intent.get("measure") or constraints.get("measure")),
            "population": _coerce_text(intent.get("population") or constraints.get("game_population")),
            "season": intent.get("season") or constraints.get("season"),
            "date_range": intent.get("date_range") or constraints.get("date_range"),
            "notes": intent.get("notes", ""),
            "entities": entities,
            "qualification": intent.get("qualification") or constraints.get("qualification"),
        }
        return merged

    def _bound_entity_sets(self, request: ToolRequest, ctx, analytical_role=None) -> list[dict[str, Any]]:
        """Resolve PLAYER_ID_SET data from bindings (payload lives on the bound export)."""
        sets: list[dict[str, Any]] = []
        for binding in request.bindings:
            if binding.export_type != "PLAYER_ID_SET":
                continue
            export = ctx.find_export(binding.export_id) if ctx else None
            payload = export.payload if export else {}
            sets.append(
                {
                    "role": analytical_role or role_from_text(request.inputs.get("raw_query") or "") or request.inputs.get("entity_role") or "PLAYER",
                    "namespace": (export.contract or {}).get("namespace", "MLBAM") if export else "MLBAM",
                    "ids": payload.get("ids", []),
                    "names": payload.get("names", []),
                }
            )
        if not sets:
            for dataset in request.inputs.get("entity_sets", []) or []:
                sets.append(dataset)
        return sets

    # ------------------------------------------------------------------ execution
    def execute(self, request: ToolRequest, ctx=None) -> CapabilityResult:
        intent = self._intent(request)
        if request.bindings:
            intent['entities'] = []
        intent['entities'] = self._bound_entity_sets(request, ctx, intent.get('analytical_role')) or intent['entities']
        from app.capabilities.analytics.scalar_inputs import scalar_input_contract, ScalarMeasureUnsupported
        try:
            scalar_selection = scalar_input_contract(request, ctx, intent)
        except ScalarMeasureUnsupported as exc:
            return CapabilityResult(None, ToolOutcome(status='BLOCKED', code='UNSUPPORTED_MEASURE',
                message=str(exc), need_id=request.need_id, tool_request_id=request.id,
                classification='UNSUPPORTED_MEASURE'))
        except (ValueError, TypeError) as exc:
            return CapabilityResult(None, ToolOutcome(status='BLOCKED', code='ANALYTICAL_OBLIGATION_UNPROVEN',
                message=str(exc), need_id=request.need_id, tool_request_id=request.id, classification='WRONG_BINDING'))
        if scalar_selection is not None:
            requested, intent, scalar_scope = scalar_selection
            requested_measures = [MeasureResolution(text=MEASURES[key].label, key=key, label=MEASURES[key].label,
                derivable=MEASURES[key].derivable, matched=True, note=MEASURES[key].note)
                for key in requested.measures]
            contract_inputs = {}
        else:
            # Preserve the legacy identity trust order: Binder exports are attached
            # after binding semantic obligations, not pre-promoted into an LLM intent.
            intent = self._intent(request)
            if request.bindings:
                intent['entities'] = []
            intent, obligation_issues = bind_obligations(intent, request.inputs)
            intent['entities'] = self._bound_entity_sets(request, ctx, intent.get('analytical_role')) or intent['entities']
            contract_inputs = {**request.inputs, 'obligation_issues': obligation_issues,
                '_canonical_identity_exports': intent['entities'] if request.bindings and ctx else []}
            requested_measures = self._requested_measures(request, intent)
        intent["execution_hints"] = {"constraints": request.inputs.get("constraints") or {}, "for_comparison": bool(request.inputs.get("for_comparison"))}
        blocked = [item for item in requested_measures if not item.derivable]
        if blocked:
            labels = "; ".join(
                f"{item.label} ({item.note or 'not derivable from pitch-level data'})" for item in blocked
            )
            return CapabilityResult(
                None,
                ToolOutcome(
                    status="BLOCKED",
                    code="UNSUPPORTED_MEASURE",
                    message=f"requested measure cannot be derived from the available data: {labels}",
                    need_id=request.need_id,
                    tool_request_id=request.id,
                    classification="UNSUPPORTED_MEASURE",
                ),
            )

        entity_ids = [
            int(i) for e in intent["entities"] for i in e.get("ids", []) if str(i).isdigit()
        ]
        if intent.get("require_entities") and not entity_ids:
            return CapabilityResult(
                None,
                ToolOutcome(
                    status="BLOCKED",
                    code="UNRESOLVED_REQUIRED_INPUT",
                    message="the question identifies an entity but no resolved player id was bound",
                    need_id=request.need_id,
                    tool_request_id=request.id,
                    classification="WRONG_BINDING",
                ),
            )

        season, season_error = _coerce_season(intent.get("season"))
        if season_error:
            return CapabilityResult(
                None,
                ToolOutcome(
                    status="BLOCKED",
                    code="SCOPE_MISMATCH",
                    message=season_error,
                    need_id=request.need_id,
                    tool_request_id=request.id,
                    classification="SCOPE_MISMATCH",
                ),
            )
        intent["season"] = season
        date_range = intent.get("date_range") or {}
        start = _to_date(date_range.get("start")) if isinstance(date_range, dict) else None
        end = _to_date(date_range.get("end")) if isinstance(date_range, dict) else None
        source, reason = self.catalog.source_for_window("pitch", season=season, start=start, end=end)
        if source is None:
            return CapabilityResult(
                None,
                ToolOutcome(
                    status="BLOCKED",
                    code="SCOPE_MISMATCH",
                    message=f"no data source covers the requested window: {reason}",
                    need_id=request.need_id,
                    tool_request_id=request.id,
                    classification="SCOPE_MISMATCH",
                ),
            )

        try:
            if scalar_selection is None:
                requested = requested_contract(intent, contract_inputs, requested_measures)
        except (ValueError, TypeError) as error:
            return CapabilityResult(None, ToolOutcome(
                status="BLOCKED", code="ANALYTICAL_OBLIGATION_UNPROVEN", message=f"invalid bound analytical obligation: {error}",
                need_id=request.need_id, tool_request_id=request.id, classification="WRONG_BINDING"))
        if requested.issues:
            return CapabilityResult(None, ToolOutcome(
                status="BLOCKED", code="ANALYTICAL_OBLIGATION_UNPROVEN", message="; ".join(requested.issues),
                need_id=request.need_id, tool_request_id=request.id, classification="WRONG_BINDING"))
        intent["bound_obligations"] = requested.model_dump(mode="json", exclude={"issues"})
        intent["bound_measure_definitions"] = {key: MEASURES[key].definition for key in requested.measures if key in MEASURES}
        slice_data = self.catalog.builder_slice("pitch")
        feedback = ""
        last_error: IRError | None = None
        last_candidate: dict[str, Any] | None = None
        query: AnalyticalQuery | None = None
        ir_origin = "llm"
        if scalar_selection is not None or is_general_performance(intent):
            try:
                canonical = build_canonical_query(intent)
                if canonical is not None:
                    if scalar_selection is not None:
                        canonical = canonical.model_copy(update={'game_types': intent.get('game_types') or []})
                    query = self._validate_candidate(canonical, requested, source)
                    ir_origin = "canonical_metric_pack"
            except (IRError, ValueError, TypeError) as exc:
                query = None
                feedback = f"The canonical pack was rejected: {exc}"
        if query is None:
            for attempt in range(3):
                raw = self._build_ir(intent, slice_data, source, feedback)
                if raw is None:
                    return CapabilityResult(
                        None,
                        ToolOutcome(
                            status="FAILED",
                            code="INVALID_IR",
                            message="the analytical IR builder did not return a usable object",
                            retryable=True,
                            need_id=request.need_id,
                            tool_request_id=request.id,
                            classification="UNSUPPORTED_ANALYSIS",
                        ),
                    )
                last_candidate = raw
                try:
                    candidate = AnalyticalQuery.model_validate(raw)
                    query = self._validate_candidate(candidate, requested, source)
                    break
                except IRError as exc:
                    last_error = exc
                    feedback = self._feedback(exc, raw)
                except Exception as exc:  # noqa: BLE001
                    last_error = IRError("INVALID_IR", f"schema error: {exc}")
                    feedback = f"The previous candidate did not match the schema: {exc}. Fix it and return the full object again."
        if query is None:
            code = last_error.code if last_error else "INVALID_IR"
            detail = last_error.message if last_error else "IR validation failed"
            return CapabilityResult(
                None,
                ToolOutcome(
                    status="BLOCKED" if code == "ANALYTICAL_NONCONFORMANCE" else "FAILED",
                    code=code,
                    message=f"{detail} | last candidate: {json.dumps(last_candidate, ensure_ascii=False)[:600]}" if last_candidate else detail,
                    need_id=request.need_id,
                    tool_request_id=request.id,
                    classification="SCOPE_MISMATCH" if code == "ANALYTICAL_NONCONFORMANCE" else "UNSUPPORTED_ANALYSIS" if code != "INVALID_IR" else "RETRYABLE_MODEL",
                ),
            )

        if self.catalog.source_for_window("pitch", season=query.season, start=_to_date(query.date_range.start) if query.date_range else None, end=_to_date(query.date_range.end) if query.date_range else None)[0] != source:
            # The builder changed the scope; re-resolve to stay honest.
            resolved, _ = self.catalog.source_for_window(
                "pitch", season=query.season,
                start=_to_date(query.date_range.start) if query.date_range else None,
                end=_to_date(query.date_range.end) if query.date_range else None,
            )
            if resolved is None:
                return CapabilityResult(
                    None,
                    ToolOutcome(status="BLOCKED", code="SCOPE_MISMATCH", message="IR scope is not covered by any source", need_id=request.need_id, tool_request_id=request.id, classification="SCOPE_MISMATCH"),
                )
            source = resolved

        # F5 (ADR 0020): a Need whose rows feed a cross-artifact comparison must enumerate
        # its complete qualified population. An LLM top-N is an intermediate truncation,
        # not a complete population, so it is replaced by a bounded complete scan. The
        # ceiling keeps a safety budget; if it is hit the contract records TRUNCATED.
        for_comparison = bool(request.inputs.get("for_comparison"))
        ceiling = request.inputs.get("internal_row_ceiling") or (_COMPARISON_ROW_CEILING if for_comparison else None)
        if isinstance(ceiling,bool) or ceiling is not None and (not isinstance(ceiling,int) or not 1 <= ceiling <= _COMPARISON_ROW_CEILING):
            return CapabilityResult(None,ToolOutcome(status="BLOCKED",code="ANALYTICAL_OBLIGATION_UNPROVEN",message="invalid execution row ceiling",need_id=request.need_id,tool_request_id=request.id))
        # A ceiling is an execution budget; it cannot replace an authoritative top-N.
        applied_limit = query.limit
        internal_ceiling = ceiling if query.limit is None else None
        if internal_ceiling is not None:
            query = query.model_copy(update={"limit":internal_ceiling})
            applied_limit = internal_ceiling

        # Count source observations in the same read-only query, including scalar NULLs.
        query = query.model_copy(update={"aggregates": [*query.aggregates, Aggregate(name=OBSERVATION_COLUMN, function="COUNT", field="*")]})
        query = validate_query(query, self.catalog, comparison_plan=bool(internal_ceiling))
        try:
            compiled = self.compiler.compile(query, source)
        except IRError as exc:
            return CapabilityResult(
                None,
                ToolOutcome(status="FAILED", code=exc.code, message=exc.message, need_id=request.need_id, tool_request_id=request.id, classification="UNSUPPORTED_ANALYSIS"),
            )
        guard = guard_sql(compiled.sql)
        if not guard.ok:
            return CapabilityResult(
                None,
                ToolOutcome(status="FAILED", code="SQL_GUARD_REJECTED", message=guard.reason, need_id=request.need_id, tool_request_id=request.id, classification="INTERNAL_FAILURE"),
            )

        result = self.executor.run(compiled.sql, source)
        if result.error:
            return CapabilityResult(
                None,
                ToolOutcome(
                    status="FAILED",
                    code=result.error_code or "QUERY_FAILED",
                    message=result.error,
                    retryable=result.error_code == "SOURCE_UNAVAILABLE",
                    need_id=request.need_id,
                    tool_request_id=request.id,
                    classification="RETRYABLE_SOURCE" if result.error_code == "SOURCE_UNAVAILABLE" else "UNSUPPORTED_ANALYSIS",
                ),
            )

        availability = (self.executor.availability(source, query.relation)
                        if hasattr(self.executor, "availability") else SourceAvailability(source=source, relation=query.relation, note="executor provides no availability receipt"))
        population_count = self._count_groups(query,source) if internal_ceiling is not None and result.row_count >= internal_ceiling else None
        attestation = attest(query, compiled, availability=availability, executed=True,
                             internal_row_ceiling=internal_ceiling,returned_groups=result.row_count if internal_ceiling else None,
                             population_groups=population_count)
        conformance = compare(requested, attestation)
        receipt = {"validated_ir":query.model_dump(mode="json"),"execution_hints": intent.get("execution_hints") or {}, "requested": requested.model_dump(mode="json"),
                   "attestation": attestation.model_dump(mode="json"),
                   "attestation_digest": digest(attestation),
                   "conformance": conformance.model_dump(mode="json")}
        observations = [r.pop(OBSERVATION_COLUMN, None) for r in result.rows]
        result.columns = [c for c in result.columns if c["name"] != OBSERVATION_COLUMN]
        compiled.output_columns = [c for c in compiled.output_columns if c != OBSERVATION_COLUMN]
        rows = self._attach_names(result.rows, source)
        normalized = []
        declared = [m for m in declare_measures(query, ir_origin=ir_origin) if m.column != OBSERVATION_COLUMN]
        measure_keys = sorted({item.key for item in declared if item.verified and item.key})
        requested_measure_keys = sorted({item.key for item in requested_measures if item.key})
        # When a grouped scan fills its budget, ask the database for the exact group count so
        # completeness is measured rather than inferred (F5, ADR 0020).
        if compiled.group_columns and applied_limit is not None and result.row_count >= int(applied_limit):
            population_count = self._count_groups(query, source)
        entity_kind, entity_namespace = _entity_identity(compiled, bool(entity_ids))
        enumeration = _enumeration_contract(
            query, compiled, result, source, applied_limit,
            population_count=population_count,
            entity_kind=entity_kind,
            entity_namespace=entity_namespace,
            source_coverage=_source_coverage(self.catalog, source),
        )
        empty_population = result.row_count == 0 or observations == [0]
        actual_scope = scope_from_execution(attestation)
        requested_scope = scope_from_request(requested, [n for e in intent["entities"] for n in e.get("names", [])])
        enumeration.source_coverage = availability.note
        if conformance.status != "MATCH" and not all(d.get("channel") == "execution_ceiling" for d in conformance.differences):
            enumeration.completeness = "UNKNOWN"
            enumeration.note += f"; source/conformance: {conformance.status}/{conformance.coverage}"

        receipt["result_digest"] = digest(rows)
        structured = {
            "execution_receipt": receipt,
            "observation_state": "UNAVAILABLE" if conformance.coverage == "UNAVAILABLE" else "UNKNOWN" if conformance.coverage == "UNKNOWN" else "EMPTY" if empty_population else "OBSERVED",
            "schema": result.columns,
            "rows": rows,
            "row_count": result.row_count,
            "source": source,
            "relation": compiled.table,
            "sql": compiled.sql,
            "applied_fields": compiled.applied_fields,
            "output_columns": compiled.output_columns,
            "group_columns": compiled.group_columns,
            "intent_summary": query.intent_summary,
            "ir_origin": ir_origin,
            "canonical_normalized": normalized,
            "actual_measures": list(attestation.measures),
            "measure_keys": measure_keys,
            "requested_measure_keys": requested_measure_keys,
            "empty_population": empty_population,
            "enumeration": enumeration.model_dump(),
            "for_comparison": for_comparison,
        }
        artifact = Artifact(
            kind="DATABASE_RESULT",
            structured_data=structured,
            text_content=_result_text(rows, compiled.output_columns),
            exports=[
                Export(
                    id=f"exp_{request.id}_result",
                    type="RANKED_ENTITY_SET" if compiled.group_columns else "STATISTICAL_RESULT",
                    payload={"rows": rows, "columns": compiled.output_columns, "group_columns": compiled.group_columns},
                    contract={
                        "grain": "GROUP" if compiled.group_columns else "SCALAR",
                        "source": source,
                        "measures": measure_keys,
                        "ordering": enumeration.ordering,
                        "verified_ordering": enumeration.ordering_verified,
                        "entity_kind": entity_kind,
                        "entity_namespace": entity_namespace,
                    },
                )
            ],
            lineage=[f"ir:sha256:{attestation.ir_digest}", f"sql:sha256:{attestation.sql_digest}", f"source:{source}:{availability.version}"],
            provenance={
                "capability": "local_analytics",
                "source": source,
                "relation": compiled.table,
                "sql_hash": result.sql_hash,
                "duration_ms": result.duration_ms,
                "request_id": request.id,
                "ir_origin": ir_origin,
                **({'scalar_input_contract': scalar_scope} if scalar_selection is not None else {}),
                "validated_ir_digest": attestation.ir_digest,
                "compiler_version": attestation.compiler_version,
                "as_of": availability.observed_at,
                "partitions": list(availability.partitions),
            },
            references=[
                Reference(ref_type=ReferenceType.SQL, ref_id=result.sql_hash, source=f"{source}:{compiled.table}", span=compiled.sql),
                Reference(ref_type=ReferenceType.TOOL_REQUEST, ref_id=request.id, source="runtime"),
            ],
            requested_scope=requested_scope,
            actual_scope=actual_scope,
            enumeration=enumeration,
            confidence="STRONG" if result.row_count and not empty_population and conformance.status == "MATCH" else "WEAK",
            status="ACCEPTED" if result.row_count and not empty_population and conformance.status == "MATCH" else "REJECTED" if conformance.status == "MISMATCH" else "PARTIAL",
            need_id=request.need_id,
        )
        if conformance.status != "MATCH":
            return CapabilityResult(artifact, ToolOutcome(
                status="BLOCKED" if conformance.status in {"UNKNOWN", "MISMATCH"} else "SUCCESS",
                code="ANALYTICAL_NONCONFORMANCE" if conformance.status == "MISMATCH" else "SOURCE_COVERAGE_" + conformance.coverage,
                message=json.dumps(conformance.model_dump(mode="json"), ensure_ascii=False),
                need_id=request.need_id, tool_request_id=request.id, classification="SCOPE_MISMATCH"))
        if empty_population:
            return CapabilityResult(
                artifact,
                ToolOutcome(
                    status="NO_DATA",
                    code="VALID_EMPTY",
                    message=f"no observations in the requested window ({source})",
                    need_id=request.need_id,
                    tool_request_id=request.id,
                    classification="VALID_EMPTY",
                ),
            )
        status = "SUCCESS" if result.row_count else "NO_DATA"
        return CapabilityResult(
            artifact,
            ToolOutcome(
                status=status,
                code="OK" if result.row_count else "VALID_EMPTY",
                message=f"{result.row_count} row(s) from {source}",
                need_id=request.need_id,
                tool_request_id=request.id,
                classification="" if result.row_count else "VALID_EMPTY",
            ),
        )

    def _validate_candidate(self, candidate, requested, source):
        query = validate_query(candidate, self.catalog)
        if any(a.name == OBSERVATION_COLUMN for a in query.aggregates) or any(d.name == OBSERVATION_COLUMN for d in query.derived):
            raise IRError("INVALID_IR", "reserved execution observation column")
        compiled = self.compiler.compile(query, source)
        conformance = compare(requested, attest(query, compiled), check_availability=False)
        if conformance.status != "MATCH":
            raise IRError("ANALYTICAL_NONCONFORMANCE", json.dumps(conformance.model_dump(mode="json"), default=str))
        return query

    # ------------------------------------------------------------------ measures
    def _requested_measures(self, request: ToolRequest, intent: dict[str, Any]) -> list[MeasureResolution]:
        resolutions: list[MeasureResolution] = []
        seen: set[str] = set()
        requirements = request.inputs.get("analytical_requirements") or []
        items = [r.get("value") for r in requirements if r.get("required",True) and r.get("kind") == "MEASURE"]
        for item in items:
            if not isinstance(item, dict):
                continue
            spec = MEASURES.get(str(item.get("key") or ""))
            if spec is None or spec.key in seen:
                continue
            seen.add(spec.key)
            resolutions.append(
                MeasureResolution(
                    text=str(item.get("label") or spec.label),
                    key=spec.key,
                    label=spec.label,
                    derivable=spec.derivable,
                    matched=True,
                    note=spec.note,
                )
            )
        # Bound Requirement measures supersede Planner shorthand (e.g. "avg speed"
        # must not add a batting-AVG obligation to a bound velocity Requirement).
        if resolutions:
            return resolutions
        for resolution in resolve_texts([intent.get("measure") or "", intent.get("objective") or ""]):
            if resolution.key in seen:
                continue
            seen.add(resolution.key)
            resolutions.append(resolution)
        return resolutions

    # ------------------------------------------------------------------ LLM IR
    def _count_groups(self, query: AnalyticalQuery, source: str) -> int | None:
        """Exact group count via a bounded COUNT pushdown (no ORDER BY / LIMIT).

        Used only when a grouped scan fills its budget, so completeness is measured
        rather than inferred. Returns None if the count cannot be verified (source
        failure or guard rejection); the caller then keeps the conservative TRUNCATED.
        """
        count_query = query.model_copy(update={"limit": None, "sort": []})
        try:
            inner = self.compiler.compile(count_query, source)
        except IRError:
            return None
        wrapped = f"SELECT COUNT(*) AS n FROM ({inner.sql}) AS sub"
        if not guard_sql(wrapped).ok:
            return None
        counted = self.executor.run(wrapped, source)
        if counted.error or not counted.rows:
            return None
        value = counted.rows[0].get("n")
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _feedback(self, error: IRError, candidate: dict[str, Any]) -> str:
        available_fields = sorted(self.catalog.table("pitch").fields)
        aggregates = [a.get("name") for a in candidate.get("aggregates", []) if isinstance(a, dict)]
        derived = [d.get("name") for d in candidate.get("derived", []) if isinstance(d, dict)]
        return (
            f"Your previous candidate was rejected: {error.code}: {error.message}.\n"
            f"Available aggregate names you defined: {aggregates}\n"
            f"Available derived names you defined: {derived}\n"
            f"A derived operand of kind 'aggregate' or 'derived' MUST reference one of those exact names.\n"
            f"Catalog fields: {available_fields}\n"
            "Return the COMPLETE corrected IR object (all aggregates and derived entries)."
        )

    def _build_ir(self, intent: dict[str, Any], slice_data: dict[str, Any], source: str, feedback: str) -> dict[str, Any] | None:
        if not self.llm or not self.llm.configured:
            return None
        user = (
            f"CATALOG (source={source}):\n{json.dumps(slice_data, ensure_ascii=False)}\n\n"
            f"ANALYTICAL REQUEST:\n{json.dumps(intent, ensure_ascii=False, default=str)}\n\n"
            "Produce the Safe Analytical IR JSON object now."
        )
        if feedback:
            user += f"\n\n{feedback}"
        try:
            result = self.llm.complete_json(role="analytics", system=SYSTEM_PROMPT, user=user, max_tokens=2000)
        except Exception:  # noqa: BLE001
            return None
        return result.data

    def _attach_names(self, rows: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
        if not rows:
            return rows
        id_fields: list[tuple[str, str]] = []
        sample = rows[0]
        if "batter_id" in sample and "batter_name" not in sample:
            id_fields.append(("batter_id", "batter_name"))
        if "pitcher_id" in sample and "pitcher_name" not in sample:
            id_fields.append(("pitcher_id", "pitcher_name"))
        if not id_fields:
            return rows
        ids = {int(row[field]) for field, _ in id_fields for row in rows if isinstance(row.get(field), (int, float))}
        if not ids:
            return rows
        names = self._lookup_player_names(sorted(ids))
        for row in rows:
            for field, name_field in id_fields:
                value = row.get(field)
                if isinstance(value, (int, float)):
                    row[name_field] = names.get(int(value), "")
        return rows

    def _lookup_player_names(self, ids: list[int]) -> dict[int, str]:
        if not ids:
            return {}
        safe_ids = ", ".join(str(int(i)) for i in ids[:500])
        sql = f"SELECT player_id, player_name FROM public.player_dictionary WHERE player_id IN ({safe_ids})"
        result = self.executor.run(sql, SOURCE_POSTGRES)
        if result.error:
            return {}
        return {int(r["player_id"]): r["player_name"] for r in result.rows}


def _to_date(value):
    if value is None or value == "":
        return None
    if hasattr(value, "year") and not isinstance(value, str):
        return value
    from datetime import date

    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _coerce_text(value: Any) -> str | None:
    """Planner constraints are free-form; a list/dict must never break scope construction."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value) or None
    return str(value)


def _coerce_season(value: Any) -> tuple[int | None, str | None]:
    """Return (season, error). Never silently drop a season the user asked for."""
    if value is None or value == "":
        return None, None
    if isinstance(value, bool):
        return None, f"season {value!r} is not a supported year"
    if isinstance(value, int):
        return value, None
    if isinstance(value, float):
        return (int(value), None) if value.is_integer() else (None, f"season {value!r} is not a supported year")
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit() and len(text) == 4:
            return int(text), None
        return None, f"season '{value}' is not a supported year; specify a single season"
    return None, f"season {value!r} is not a supported year"


def _has_volume_aggregate(query: AnalyticalQuery) -> bool:
    return any(agg.function in {"COUNT", "COUNT_NON_NULL", "COUNT_DISTINCT"} for agg in query.aggregates)


def _scalar_is_empty(rows: list[dict[str, Any]]) -> bool:
    """True when a single scalar row carries no observation (all numeric values zero).

    An aggregate over an empty population returns one all-zero row; presenting that as a
    zero-valued metric is exactly the "failed calculation written as zero" failure mode.
    """
    if len(rows) != 1:
        return False
    numeric = [
        value for value in rows[0].values()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    if not numeric:
        return False
    return all(value == 0 for value in numeric)


def _num(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def canonicalize_metrics(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Recompute standard rate metrics from base counts deterministically.

    The IR builder occasionally mis-assembles a derived expression tree. Base counts come
    straight from the database, so standard metrics are recomputed from them here. This
    never invents a value: a metric is only normalized when all of its base counts exist.
    """
    normalized: list[str] = []
    for row in rows:
        singles, doubles = _num(row, "singles"), _num(row, "doubles")
        triples, home_runs = _num(row, "triples"), _num(row, "home_runs")
        hits, at_bats = _num(row, "hits"), _num(row, "at_bats")
        walks, hbp, sac_flies = _num(row, "walks"), _num(row, "hbp"), _num(row, "sac_flies")
        pa, strikeouts = _num(row, "pa"), _num(row, "strikeouts")

        total_bases = None
        if None not in (singles, doubles, triples, home_runs):
            total_bases = singles + 2 * doubles + 3 * triples + 4 * home_runs
            if "tb" in row:
                row["tb"] = total_bases
                normalized.append("tb")
        if total_bases is None:
            total_bases = _num(row, "tb")

        if hits is not None and at_bats:
            if "avg" in row:
                row["avg"] = hits / at_bats
                normalized.append("avg")
        if total_bases is not None and at_bats:
            if "slg" in row:
                row["slg"] = total_bases / at_bats
                normalized.append("slg")
        if None not in (hits, walks, hbp, at_bats) and sac_flies is not None:
            denominator = at_bats + walks + hbp + sac_flies
            if denominator and "obp" in row:
                row["obp"] = (hits + walks + hbp) / denominator
                normalized.append("obp")
        if "ops" in row:
            obp, slg = _num(row, "obp"), _num(row, "slg")
            if obp is not None and slg is not None:
                row["ops"] = obp + slg
                normalized.append("ops")
        if pa:
            if strikeouts is not None and "k_pct" in row:
                row["k_pct"] = 100.0 * strikeouts / pa
                normalized.append("k_pct")
            if walks is not None and "bb_pct" in row:
                row["bb_pct"] = 100.0 * walks / pa
                normalized.append("bb_pct")
    return rows, sorted(set(normalized))


def _scope_date(query: AnalyticalQuery):
    from app.contracts.models import DateRange

    if query.date_range and (query.date_range.start or query.date_range.end):
        return DateRange(start=query.date_range.start, end=query.date_range.end)
    return None


_COMPARISON_ROW_CEILING = 5000


def _enumeration_contract(
    query: AnalyticalQuery,
    compiled,
    result,
    source: str,
    applied_limit: int | None,
    *,
    population_count: int | None = None,
    entity_kind: str | None = None,
    entity_namespace: str | None = None,
    source_coverage: str | None = None,
) -> EnumerationContract:
    """Describe the population a result enumerates (F5, ADR 0020).

    A scalar result, or a grouped result that returned fewer rows than its limit, is a
    complete enumeration. A grouped result that filled its limit is only TRUNCATED unless
    an exact COUNT pushdown proved the population is exactly that size.
    """
    if not compiled.group_columns or applied_limit is None:
        completeness = "COMPLETE"
    elif result.row_count < int(applied_limit):
        completeness = "COMPLETE"
    elif population_count is not None:
        completeness = "COMPLETE" if population_count <= result.row_count else "TRUNCATED"
    else:
        completeness = "TRUNCATED"
    qualification = None
    if query.qualification is not None:
        qualification = f"{query.qualification.basis} >= {query.qualification.minimum}"
    ordering = [f"{spec.name} {spec.direction}" for spec in query.sort]
    if completeness == "COMPLETE":
        note = "complete qualified population"
        if population_count is not None:
            note += f" (exact count {population_count})"
    else:
        note = f"bounded to {applied_limit} rows; a larger population may exist"
    return EnumerationContract(
        population=query.population,
        qualification=qualification,
        ordering=ordering,
        ordering_verified=bool(query.sort),
        limit=applied_limit,
        returned_rows=result.row_count,
        population_count=population_count,
        completeness=completeness,
        identity_columns=_identity_columns(compiled),
        entity_kind=entity_kind,
        entity_namespace=entity_namespace,
        source=source,
        source_coverage=source_coverage,
        season=query.season,
        date_range=_scope_date(query),
        note=note,
    )


def _entity_identity(compiled, has_entity_filter: bool) -> tuple[str | None, str | None]:
    """Verified identity domain of a result, or (None, None) when unproven."""
    player_columns = {"batter_id", "pitcher_id", "player_id"}
    columns = set(compiled.group_columns) | set(compiled.output_columns)
    if columns & player_columns or has_entity_filter:
        return "PLAYER", "MLBAM"
    return None, None


def _source_coverage(catalog, source: str) -> str | None:
    try:
        window = catalog.table("pitch").coverage.get(source)
    except Exception:  # noqa: BLE001
        return None
    if not window:
        return None
    return f"{source}:{window[0]}..{window[1]}"


def _identity_columns(compiled) -> list[str]:
    columns: list[str] = []
    for name in list(compiled.group_columns) + list(compiled.output_columns):
        if name in columns:
            continue
        if name in {"name", "player_name", "batter_name", "pitcher_name"} or name.endswith("_id"):
            columns.append(name)
    return columns


def _result_text(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "The query returned no rows."
    # Include post-execution enrichment columns (e.g. resolved player names).
    all_columns = list(columns)
    for key in rows[0]:
        if key not in all_columns:
            all_columns.append(key)
    header = " | ".join(all_columns)
    body = "\n".join(" | ".join(str(row.get(c, "")) for c in all_columns) for row in rows[:25])
    return f"{header}\n{body}"
