"""Analytical obligations and operation-backed execution receipts.

This module owns semantic comparison, not SQL interpretation or planning. Requested
contracts exist before IR generation. Attestations use validated IR/compiled plans only.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from typing import Any, Literal
from types import SimpleNamespace

from pydantic import BaseModel, ConfigDict, Field

from app.capabilities.analytics.catalog import CATALOG, POPULATIONS
from app.capabilities.analytics.authority import resolve_authority
from app.capabilities.analytics.compiler import sanitize
from app.capabilities.analytics.ir import AnalyticalQuery, canonical_value, validate_query
from app.capabilities.analytics.predicates import canonical_population, conjunction_leaves, validate_predicates
from app.capabilities.analytics.measures import declare_measures, resolve_texts, MEASURES
from app.contracts.models import DateRange, Scope

RECEIPT_VERSION = "analytical-execution-v3"
COMPILER_VERSION = "safe-analytical-sql-v2"
OBSERVATION_COLUMN = "__execution_observations"


def digest(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EntityConstraint(ContractModel):
    role: Literal["BATTER", "PITCHER"]
    namespace: Literal["MLBAM"] = "MLBAM"
    ids: tuple[int, ...]


class RequestedAnalyticalContract(ContractModel):
    relation: str = "pitch"
    analytical_role: Literal["BATTER", "PITCHER"] | None = None
    entities: tuple[EntityConstraint, ...] = ()
    require_entities: bool = False
    season: int | None = None
    date_range: DateRange | None = None
    game_types: tuple[str, ...] | None = None  # None = no bound competition restriction
    qualification: dict[str, Any] | None = None
    measures: tuple[str, ...] = ()
    filters: tuple[dict[str, Any], ...] = ()  # empty = full population within bound scope
    group_by: tuple[str, ...] = ()
    ordering: tuple[dict[str, Any], ...] | None = None
    tie_break_ordering: tuple[dict[str, Any], ...] = ()
    limit: int | None = None
    comparison_population: bool = False  # governed bounded complete-scan execution
    requirement_ids: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()
    obligation_provenance: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    clause_coverage: tuple[dict[str, Any], ...] = ()
    unresolved_obligations: tuple[dict[str, Any], ...] = ()
    unproven_requirements: tuple[str, ...] = ()


class SourceAvailability(ContractModel):
    source: str
    relation: str
    known: bool = False
    start: date | None = None
    end: date | None = None
    seasons: tuple[int, ...] = ()
    partitions: tuple[str, ...] = ()
    observed_at: str = ""
    version: str = ""
    note: str = ""


class ExecutionAttestation(ContractModel):
    version: str = RECEIPT_VERSION
    compiler_version: str = COMPILER_VERSION
    ir_digest: str
    sql_digest: str
    source: str
    relation: str
    analytical_role: Literal["BATTER", "PITCHER"] | None = None
    entities: tuple[EntityConstraint, ...] = ()
    season: int | None = None
    date_range: DateRange | None = None
    game_types: tuple[str, ...] | None = None
    qualification: dict[str, Any] | None = None
    measures: tuple[dict[str, Any], ...] = ()
    filters: tuple[dict[str, Any], ...] = ()
    group_by: tuple[str, ...] = ()
    ordering: tuple[dict[str, Any], ...] = ()
    limit: int | None = None
    scope_issues: tuple[str, ...] = ()
    availability: SourceAvailability | None = None
    executed: bool = False
    internal_row_ceiling: int | None = None
    returned_groups: int | None = None
    population_groups: int | None = None


class AnalyticalConformanceResult(ContractModel):
    status: Literal["MATCH", "PARTIAL", "MISMATCH", "UNKNOWN"]
    differences: tuple[dict[str, Any], ...] = ()
    coverage: Literal["FULL", "PARTIAL", "UNAVAILABLE", "UNKNOWN"] = "UNKNOWN"


def qualification(value: Any) -> dict[str, Any] | None:
    """Only existing governed basis/minimum semantics; no league rule invention."""
    if value is None:
        return None
    if isinstance(value, dict):
        basis, minimum = value.get("basis", "PA"), value.get("minimum")
    else:
        text = str(value).upper()
        match = re.search(r"\b(PA|BBE|PITCHES|GAMES|RESULTS|PLATE APPEARANCES)\s*(?:>=|≥|AT LEAST|MINIMUM|MIN)\s*(\d+)\b", text)
        if match:
            basis, minimum = match.groups()
        else:
            match = re.search(r"(?:AT LEAST|MINIMUM|MIN|至少|>=|≥)\s*(?:有\s*)?(\d+)\s*(?:个|個|次)?\s*(PA\b|BBE\b|PITCHES\b|GAMES\b|RESULTS\b|PLATE APPEARANCES\b|打席)", text)
            if not match:
                raise ValueError("qualification is not a bound basis/minimum")
            minimum, basis = match.groups()
        basis = {"PLATE APPEARANCES": "PA", "打席": "PA"}.get(basis, basis)
    if str(basis).upper() not in {"PA", "BBE", "PITCHES", "GAMES", "RESULTS"}:
        raise ValueError("unsupported bound qualification basis")
    if isinstance(minimum, bool) or not str(minimum).isdigit():
        raise ValueError("qualification requires a nonnegative integer minimum")
    return {"basis": str(basis).upper(), "minimum": int(minimum)}


def _entities(sets) -> tuple[EntityConstraint, ...]:
    grouped: dict[str, set[int]] = {}
    for item in sets:
        namespace = item.get("namespace") or item.get("entity_namespace") or "MLBAM"
        if namespace != "MLBAM":
            raise ValueError("wrong entity namespace")
        role = str(item.get("role") or "").upper()
        if role not in {"BATTER", "PITCHER"}:
            raise ValueError("unsupported entity role")
        ids = item.get("ids", [])
        if not ids or any(isinstance(i, bool) or not str(i).isdigit() or int(i) <= 0 for i in ids):
            raise ValueError("unbound or invalid canonical entity IDs")
        governed = [canonical_value(CATALOG.field("pitch", "batter_id" if role == "BATTER" else "pitcher_id"),int(i),"canonical identity") for i in ids]
        grouped.setdefault(role, set()).update(governed)
    return tuple(EntityConstraint(role=role, ids=tuple(sorted(ids))) for role, ids in sorted(grouped.items()))


def requested_contract(intent, inputs, measures, *, source_review_text=None) -> RequestedAnalyticalContract:
    intent, binding_issues = bind_obligations(intent, inputs, source_review_text=source_review_text)
    issues = list(dict.fromkeys(binding_issues))
    try:
        entities = _entities(intent.get("entities") or [])
    except ValueError as error:
        entities = (); issues.append(str(error))
    try:
        q = qualification(intent.get("qualification"))
    except ValueError as error:
        q = None; issues.append(str(error))
    population = intent.get("population")
    codes = None
    if population:
        codes = CATALOG.resolve_population(str(population))
        if not codes:
            issues.append("unknown requested population")
    explicit_codes = intent.get("execution_constraints", {}).get("game_types")
    if explicit_codes is not None:
        if codes is not None and set(explicit_codes) != set(codes):
            issues.append("requested population and game types disagree")
        codes = explicit_codes
    constraints = intent.get("execution_constraints") or {}
    role = intent.get("analytical_role")
    if not role and len({e.role for e in entities}) == 1:
        role = entities[0].role
    if role:
        role = str(role).upper()
        if role not in {"BATTER", "PITCHER"}:
            issues.append("analytical role is unresolved or unsupported"); role = None
    if role and any(e.role != role for e in entities):
        issues.append("bound entity role differs from requested role")
    normalized_orders = {}
    for field in ("ordering", "tie_break_ordering"):
        orders = constraints.get(field)
        if orders is None:
            normalized_orders[field] = None if field == "ordering" else ()
            continue
        normalized = []
        for spec in orders:
            if not isinstance(spec, dict) or set(spec) - {"metric", "field", "direction", "nulls"}:
                issues.append("unsupported requested ordering attributes"); continue
            direction = str(spec.get("direction") or "DESC").upper()
            if direction not in {"ASC", "DESC"} or str(spec.get("nulls") or "LAST").upper() != "LAST":
                issues.append("unsupported requested ordering semantics")
            normalized.append({k:v for k,v in {**spec,"direction":direction}.items() if k != "nulls"})
        normalized_orders[field] = tuple(normalized)
    filters = tuple(constraints.get("filters") or ())
    # Derive scope atoms from *requested* predicates, independently of candidate IR.
    try:
        validate_predicates(filters)
        scope_query = AnalyticalQuery(analytical_role=role, entity_filters=[e.model_dump() for e in entities],
                                     season=intent.get("season"), date_range=intent.get("date_range"),
                                     game_types=list(codes or []), filters=list(filters))
        scoped = _extract_scope(scope_query)
        entities, season, dates, codes, scope_issues = scoped
        issues.extend(scope_issues)
        for condition in conjunction_leaves(filters):
            field = {"game_date":"date_range", "season":"season", "game_type":"game_types", "batter_id":"entities", "pitcher_id":"entities"}.get(condition.field)
            if field:
                derived = [{**p,"source_clause":condition.model_dump(mode="json"),"status":"DERIVED","derivation":"finite requested scope-predicate extraction"} for p in intent.get("obligation_provenance",{}).get("filters", [])]
                if derived:
                    value = {"season":season,"date_range":dates,"game_types":sorted(codes) if codes is not None else None,"entities":[e.model_dump() for e in entities]}[field]
                    intent["obligation_provenance"].setdefault(field,[]).extend({**p,"value":value} for p in derived)
    except (ValueError, TypeError) as error:
        issues.append(f"unproven requested predicate: {error}")
        season, dates = intent.get("season"), intent.get("date_range")
    return RequestedAnalyticalContract(
        analytical_role=role, entities=entities, require_entities=bool(intent.get("require_entities")),
        season=season, date_range=dates,
        game_types=tuple(sorted(set(codes))) if codes is not None else None,
        qualification=q, measures=tuple(intent.get("measure_keys") or ()),
        filters=filters, group_by=tuple(constraints.get("group_by") or ()),
        ordering=normalized_orders["ordering"],
        tie_break_ordering=normalized_orders["tie_break_ordering"],
        limit=constraints.get("limit"),
        comparison_population=False,
        requirement_ids=tuple(intent.get("requirement_ids") or []), issues=tuple(issues),
        obligation_provenance=intent.get("obligation_provenance") or {},
        clause_coverage=tuple(intent.get("clause_coverage") or ()),
        unresolved_obligations=tuple(intent.get("unresolved_obligations") or ()),
        unproven_requirements=tuple(intent.get("unproven_requirements") or ()),
    )


def _extract_scope(query):
    # Compiler joins same-role entity predicates with AND, not OR.
    entities: dict[str, set[int]] = {}
    issues = []
    for binding in query.entity_filters:
        ids = set(binding.ids)
        entities[binding.role] = entities[binding.role] & ids if binding.role in entities else ids
    season = query.season
    dates = query.date_range.model_dump() if query.date_range else {}
    codes = set(query.game_types)
    if query.population:
        codes.update(CATALOG.resolve_population(query.population))
    effective_codes = codes or None
    for condition in conjunction_leaves(query.filters):
        field = condition.field
        if field in {"batter_id", "pitcher_id", "game_type"}:
            values = condition.values if condition.op == "IN" else [condition.value] if condition.op == "COMPARE" and condition.comparator == "=" else None
            if values is None:
                issues.append(f"unproven scope predicate on {field}"); continue
            if field == "game_type":
                values = set(values)
                effective_codes = effective_codes & values if effective_codes is not None else values
            else:
                role = "BATTER" if field == "batter_id" else "PITCHER"
                values = [canonical_value(CATALOG.field(query.relation,field),v,field) for v in values]
                if any(v is None or not isinstance(v,int) or v <= 0 for v in values):
                    issues.append(f"noncanonical identity predicate on {field}"); continue
                ids = {int(v) for v in values}
                entities[role] = entities[role] & ids if role in entities else ids
        elif field == "season" and condition.op == "COMPARE" and condition.comparator == "=":
            value = canonical_value(CATALOG.field(query.relation, "season"), condition.value, "season")
            if value is None or season is not None and season != value:
                issues.append("unproven or contradictory season predicate")
            else:
                season = value
        elif field == "game_date":
            try:
                if condition.op == "BETWEEN":
                    bounds = {"start": date.fromisoformat(str(condition.low)), "end": date.fromisoformat(str(condition.high))}
                elif condition.op == "COMPARE" and condition.comparator in {"=", ">=", ">", "<=", "<"}:
                    value = date.fromisoformat(str(condition.value))
                    bounds = {"start": value, "end": value} if condition.comparator == "=" else {"start": value + timedelta(days=condition.comparator == ">") } if condition.comparator in {">", ">="} else {"end": value - timedelta(days=condition.comparator == "<")}
                else:
                    raise ValueError("unsupported temporal predicate")
                for key,value in bounds.items():
                    dates[key] = (max if key == "start" else min)(dates[key], value) if dates.get(key) else value
            except (ValueError, TypeError):
                issues.append("unproven game_date predicate")
        elif field == "season":
            issues.append("unproven season predicate")
        elif condition.op in {"OR", "NOT"} and any(_scope_field(c) for c in condition.conditions or []):
            issues.append("unproven compound scope predicate")
    return (tuple(EntityConstraint(role=role, ids=tuple(sorted(ids))) for role, ids in sorted(entities.items())),
            season, dates or None, effective_codes, issues)


def attest(query: AnalyticalQuery, compiled, *, availability=None, executed=False, internal_row_ceiling=None, returned_groups=None, population_groups=None) -> ExecutionAttestation:
    entities, season, dates, effective_codes, issues = _extract_scope(query)
    # A role declaration cannot relabel a different executed grouping/subject.
    group_roles = {"BATTER" if f == "batter_id" else "PITCHER" for f in query.group_by if f in {"batter_id", "pitcher_id"}}
    subject_roles = group_roles or {e.role for e in entities}
    role = query.analytical_role or (next(iter(subject_roles)) if len(subject_roles) == 1 else None)
    if query.analytical_role and subject_roles and subject_roles != {query.analytical_role}:
        issues.append("analytical role disagrees with executed subject/grouping")
    try:
        validate_predicates(query.filters, query.relation)
    except (ValueError, TypeError) as error:
        issues.append(f"unproven row population predicate: {error}")
    declared = [m.to_dict() for m in declare_measures(query) if m.column != OBSERVATION_COLUMN]
    aggregates = {a.name: a.model_dump(mode="json") for a in query.aggregates}
    derived = {d.name: d.model_dump(mode="json") for d in query.derived}
    def lineage(name):
        if name in aggregates:
            return aggregates[name]
        if name in derived:
            operation = dict(derived[name])
            operation["inputs"] = [lineage(o["name"]) for o in (operation["left"], operation["right"]) if o["kind"] in {"aggregate", "derived"}]
            return operation
        return {"group_by": name}
    for measure in declared:
        measure["operations"] = lineage(measure["column"])
        measure["zero_null_semantics"] = "division by zero -> NULL; aggregates retain database NULL semantics"
        key = measure.get("key") or ""
        measure["unit"] = "percent" if key.endswith("_pct") else "mph" if key in {"avg_release_speed", "exit_velocity"} else "rpm" if key == "avg_spin_rate" else "degrees" if key == "launch_angle" else "ratio" if key in {"avg", "obp", "slg", "ops"} else "count" if key else None
        measure["column"] = sanitize(measure["column"])
    by_column = {m["column"]: m for m in declared}
    ordering = []
    for spec in query.sort:
        column = sanitize(spec.name); measure = by_column.get(column, {})
        ordering.append(dict(column=column, metric=measure.get("key"),
                             operation=measure.get("signature") or measure.get("definition"),
                             direction=spec.direction, nulls="LAST"))
    return ExecutionAttestation(
        ir_digest=digest(query), sql_digest=hashlib.sha256(compiled.sql.encode()).hexdigest(),
        source=compiled.source, relation=query.relation, analytical_role=role,
        entities=entities,
        season=season, date_range=dates or None,
        game_types=tuple(sorted(effective_codes)) if effective_codes is not None else None,
        qualification=query.qualification.model_dump() if query.qualification else None,
        measures=tuple(declared), filters=tuple(c.model_dump(mode="json") for c in query.filters),
        group_by=tuple(query.group_by), ordering=tuple(ordering), limit=query.limit,
        scope_issues=tuple(issues), availability=availability, executed=executed,
        internal_row_ceiling=internal_row_ceiling, returned_groups=returned_groups, population_groups=population_groups,
    )


def _scope_field(condition):
    return condition.field in {"batter_id", "pitcher_id", "season", "game_date", "game_type"} or any(_scope_field(c) for c in condition.conditions or [])


def window(season, dates):
    start, end = (date(season,1,1),date(season,12,31)) if season else (None,None)
    if dates:
        start = max(x for x in (start,dates.start) if x is not None) if start or dates.start else None
        end = min(x for x in (end,dates.end) if x is not None) if end or dates.end else None
    return start,end


def compare(requested: RequestedAnalyticalContract, executed: ExecutionAttestation, *, check_availability=True) -> AnalyticalConformanceResult:
    differences=[]
    def mismatch(channel, want, got):
        if want != got:
            differences.append(dict(channel=channel,requested=want,executed=got))
    if requested.issues:
        differences.append(dict(channel="binding",requested="bound obligations",executed=list(requested.issues)))
    if executed.scope_issues:
        differences.append(dict(channel="scope_predicates",requested="proven scope",executed=list(executed.scope_issues)))
    if requested.require_entities and not requested.entities:
        differences.append(dict(channel="entity",requested="canonical bound entity",executed="unbound"))
    mismatch("relation",requested.relation,executed.relation)
    # An unbound all-player request must not silently acquire an entity restriction.
    mismatch("entities",[e.model_dump() for e in requested.entities],[e.model_dump() for e in executed.entities])
    if requested.analytical_role is not None or executed.entities or {"batter_id", "pitcher_id"} & set(executed.group_by):
        mismatch("analytical_role",requested.analytical_role,executed.analytical_role)
    mismatch("time",window(requested.season,requested.date_range),window(executed.season,executed.date_range))
    mismatch("population",requested.game_types,executed.game_types)
    mismatch("qualification",requested.qualification,executed.qualification)
    keys={m["key"] for m in executed.measures if m.get("verified") and m.get("key")}
    missing=set(requested.measures)-keys
    if missing:
        differences.append(dict(channel="measures",requested=sorted(missing),executed=sorted(keys)))
    try:
        mismatch("row_population",canonical_population(requested.filters, requested.relation),
                 canonical_population(executed.filters, executed.relation))
    except (ValueError, TypeError) as error:
        differences.append(dict(channel="row_population_unproven",requested="finite proven predicates",executed=str(error)))
    mismatch("grouping",sorted(set(requested.group_by)),sorted(set(executed.group_by)))
    if requested.ordering is not None:
        observed=[dict(metric=o.get("metric") or o["column"],direction=o["direction"]) for o in executed.ordering]
        expected=[dict(metric=o.get("metric") or o.get("field"),direction=o.get("direction","DESC")) for o in requested.ordering]
        mismatch("ordering",expected,observed[:len(expected)])
    if requested.tie_break_ordering:
        expected=[dict(metric=o.get("metric") or o.get("field"),direction=o.get("direction","ASC")) for o in requested.tie_break_ordering]
        observed=[dict(metric=o.get("metric") or o["column"],direction=o["direction"]) for o in executed.ordering[1:]]
        mismatch("tie_break_ordering",expected,observed[:len(expected)])
    if requested.limit is not None:
        mismatch("limit",requested.limit,executed.limit)
    elif executed.internal_row_ceiling is None:
        if requested.group_by: mismatch("limit",None,executed.limit)
    else:
        if executed.limit != executed.internal_row_ceiling or isinstance(executed.internal_row_ceiling,bool) or not 1 <= executed.internal_row_ceiling <= 5000:
            differences.append(dict(channel="execution_ceiling",requested="bounded internal ceiling",executed=executed.limit))
    if differences:
        return AnalyticalConformanceResult(status="MISMATCH",differences=tuple(differences))
    if not check_availability:
        return AnalyticalConformanceResult(status="MATCH")
    coverage=source_coverage(requested,executed)
    if executed.internal_row_ceiling is not None:
        full = executed.returned_groups is not None and (executed.returned_groups < executed.internal_row_ceiling or executed.population_groups == executed.returned_groups)
        if not full and coverage == "FULL":
            return AnalyticalConformanceResult(status="PARTIAL",coverage="PARTIAL",differences=(dict(channel="execution_ceiling",requested="complete population",executed="truncated or unproven"),))
    status={"FULL":"MATCH","PARTIAL":"PARTIAL","UNAVAILABLE":"UNKNOWN","UNKNOWN":"UNKNOWN"}[coverage]
    if coverage != "FULL":
        differences.append(dict(channel="availability",requested="FULL",executed=coverage))
    return AnalyticalConformanceResult(status=status,coverage=coverage,differences=tuple(differences))


def source_coverage(requested,executed):
    a=executed.availability
    if a is None or not a.known:
        return "UNKNOWN"
    start,end=window(requested.season,requested.date_range)
    if not a.start or not a.end:
        return "UNAVAILABLE"
    if requested.season and not requested.date_range:
        return "FULL" if requested.season in a.seasons else "PARTIAL" if a.start.year <= requested.season <= a.end.year else "UNAVAILABLE"
    if start and start > a.end or end and end < a.start:
        return "UNAVAILABLE"
    if a.partitions and start and end:
        years=set(range(start.year,end.year+1)); available=set(a.seasons)
        if not years & available:return "UNAVAILABLE"
        if not years <= available:return "PARTIAL"
    if start and start < a.start or end and end > a.end:
        return "PARTIAL"
    return "FULL"


def scope_from_execution(a: ExecutionAttestation) -> Scope:
    population=next((name for name,codes in POPULATIONS.items() if a.game_types is not None and set(codes)==set(a.game_types)),None)
    return Scope(entities=sorted({str(i) for e in a.entities for i in e.ids}),
                 season=a.season,date_range=a.date_range,game_population=population,
                 game_types=list(a.game_types or []),measures=sorted({m['key'] for m in a.measures if m.get('verified') and m.get('key')}),
                 source=a.source,source_coverage=a.availability.note if a.availability else None)


def scope_from_request(r: RequestedAnalyticalContract, names=()) -> Scope:
    population=next((name for name,codes in POPULATIONS.items() if r.game_types is not None and set(codes)==set(r.game_types)),None)
    return Scope(entities=sorted({str(i) for e in r.entities for i in e.ids}),entity_names=list(names),
                 season=r.season,date_range=r.date_range,game_population=population,
                 game_types=list(r.game_types or []),measures=list(r.measures))


def receipt_usable(artifact) -> bool:
    """Judge admission checks the immutable receipt, not SQL semantics."""
    receipt=artifact.structured_data.get("execution_receipt")
    if not isinstance(receipt,dict):return False
    try:
        attestation=ExecutionAttestation.model_validate(receipt["attestation"])
        result=AnalyticalConformanceResult.model_validate(receipt["conformance"])
        requested=RequestedAnalyticalContract.model_validate(receipt["requested"])
        query=validate_query(AnalyticalQuery.model_validate(receipt["validated_ir"]),comparison_plan=attestation.internal_row_ceiling is not None)
        reconstructed=attest(query,SimpleNamespace(sql=artifact.structured_data["sql"],source=attestation.source),
                             availability=attestation.availability,executed=attestation.executed,
                             internal_row_ceiling=attestation.internal_row_ceiling,returned_groups=attestation.returned_groups,
                             population_groups=attestation.population_groups)
        recomputed=compare(requested, attestation)
        return (reconstructed == attestation and attestation.executed and attestation.version == RECEIPT_VERSION and attestation.compiler_version == COMPILER_VERSION
                and result == recomputed and result.status in {"MATCH","PARTIAL"}
                and (attestation.internal_row_ceiling is None or attestation.returned_groups == len(artifact.structured_data.get("rows", [])))
                and artifact.actual_scope == scope_from_execution(attestation)
                and receipt.get("result_digest") == digest(artifact.structured_data.get("rows", []))
                and receipt.get("attestation_digest")==digest(attestation)
                and artifact.structured_data.get("actual_measures") == list(attestation.measures)
                and attestation.sql_digest==hashlib.sha256(artifact.structured_data["sql"].encode()).hexdigest())
    except (ValueError,KeyError,TypeError):return False


def bind_obligations(intent, inputs, *, source_review_text=None):
    """Requirement-owned semantics; open Planner metadata is retained only as hints.

    Rebuilt on every call. Cached provenance/coverage supplied by a caller is never proof.
    Bound canonical IDs are the only execution input admitted, and only for an ENTITY
    Requirement. Requirement selection cannot delete global mandatory clauses.
    """
    proposal = dict(intent)
    from app.semantic.analytical_candidate import assess_candidate, ignored_span_is_safe
    all_requirements = [{**r, "id":r.get("id") or "bound:"+digest(r)} for r in inputs.get("analytical_requirements") or [] if r.get("required", True)]
    # M2.12R3: only authoritative Requirements may define requested truth. An inferred
    # Requirement with no proven authoritative parent and no governed deterministic
    # derivation is retained as planning metadata, never promoted to factual semantics.
    requirements, unproven_requirements, authority_issues = resolve_authority(
        all_requirements, identity_evidence=inputs.get('_canonical_identity_exports') or (),
        bound_user_message=inputs.get('bound_user_intent') or inputs.get('raw_query') or None,
        clarification_deltas=inputs.get('_semantic_deltas') or ())
    issues = list(inputs.get("obligation_issues") or []) + authority_issues
    if not requirements:
        issues.append("authoritative analytical Requirements are missing")
    result = dict(objective="", measure=None, analytical_role=None, entities=proposal.get("entities") or [],
                  season=None, date_range=None, population=None, qualification=None,
                  execution_constraints={}, measure_keys=[], require_entities=False,
                  requirement_ids=[r.get("id", "") for r in requirements],
                  unproven_requirements=[r.get("id", "") for r in unproven_requirements],
                  execution_hints=dict(intent=proposal, constraints=inputs.get("constraints") or {},
                                       for_comparison=bool(inputs.get("for_comparison"))),
                  obligation_provenance={}, clause_coverage=[], unresolved_obligations=[])
    provenance = result["obligation_provenance"]
    def record(field, req, value, derivation=None, clause=None):
        provenance.setdefault(field, []).append(dict(requirement_id=req.get("id", ""),
            source_clause=clause if clause is not None else req.get("value", req.get("statement")),
            origin=req.get("source", "user"), status="DERIVED" if derivation else "BOUND",
            value=value, derivation=derivation))
    def assign(field, value, req, derivation=None):
        if result.get(field) is not None and result[field] != value:
            issues.append(f"incompatible required {field}")
        result[field] = value
        record(field, req, value, derivation)
    # Typed output obligations have their own input/admission contracts. Do not
    # turn their output words into legacy acquisition measures. Keep every one in
    # requirements and in the whole-source review below: this is not permission to
    # discharge a sibling output, delete a clause, or lose a shared input restriction.
    analyses = [r for r in requirements if r.get("kind") == "ANALYSIS" and r.get("completion") != "EXPLANATION"
                and not r.get("reporting_contract") and not r.get("operation_contract")]
    texts = [str(r.get('bound_source_clause') or ((r.get('value') or {}).get('objective') if isinstance(r.get('value'),dict) else r.get('value')) or r.get('statement') or '') for r in analyses]
    # The source clause is an ancestry/accounting input, not a replacement for
    # interpreted meaning. Acquisition consumes the governed normalized objective.
    result["objective"] = " ; ".join(
        str((r.get('value') or {}).get('objective') or r.get('statement') or text)
        if isinstance(r.get('value'), dict) else text for r, text in zip(analyses, texts))
    # Original user wording can resolve subject role, never a Planner objective.
    role_sources = [(r, (r.get("value") or {}).get("role")) for r in requirements
                    if r.get("kind") == "ENTITY" and isinstance(r.get("value"), dict) and r["value"].get("role")]
    role_sources += [(r, (r.get("value") or {}).get("analytical_role")) for r in requirements if r.get("kind") == "CONSTRAINT" and isinstance(r.get("value"),dict) and r["value"].get("analytical_role")]
    role_sources += [(r, role_from_text(str(r.get("value")))) for r in requirements if r.get("kind") == "CONSTRAINT" and isinstance(r.get("value"),str) and role_from_text(r["value"])]
    role_sources += [(r, (r.get('value') or {}).get('analytical_role')) for r in analyses
                     if isinstance(r.get('value'), dict) and r['value'].get('analytical_role')]
    # An exact source quote establishes ancestry, not all normalized meaning. Preserve
    # the Interpreter's governed analytical intent even when a follow-up quote is elliptical.
    # The existing finite role vocabulary is a compatibility adapter for older Interpreters;
    # neither an unproved Requirement nor a Planner objective reaches this list.
    role_sources += [(r, role_from_text(str(r['value'].get('objective', '')))) for r in analyses
                     if isinstance(r.get('value'), dict) and not r['value'].get('analytical_role')
                     and role_from_text(str(r['value'].get('objective', '')))]
    role_sources += [(r, role_from_text(t)) for r,t in zip(analyses,texts) if role_from_text(t)]
    if not role_sources and inputs.get("bound_user_intent") and requirements:
        role = role_from_text(source_review_text or inputs["bound_user_intent"])
        if role: role_sources.append(({**requirements[0],"value":inputs["bound_user_intent"]},role))
    if not role_sources:
        role_sources += [(r,role_from_text(r.get("original_intent"))) for r in requirements if role_from_text(r.get("original_intent"))]
    # A schema-grounded semantic candidate is authoritative for the subject role: it is the
    # interpreter's reading of the user's own meaning. A brittle text cue (e.g. the word
    # "pitcher" inside "a pitcher throwing from the left side") must not override it. Entity
    # roles from resolution remain independent and still conflict-check.
    candidate_roles = [
        (r, (r.get("value") or {}).get("analytical_role"))
        for r in requirements
        if r.get("derivation_kind") == "governed_semantic_candidate"
        and isinstance(r.get("value"), dict) and r["value"].get("analytical_role")
    ]
    if candidate_roles:
        entity_roles = [
            (r, (r.get("value") or {}).get("role"))
            for r in requirements
            if r.get("kind") == "ENTITY" and isinstance(r.get("value"), dict) and r["value"].get("role")
        ]
        role_sources = entity_roles + candidate_roles
    for req in requirements:
        if req.get("source", "user") not in {"user", "inferred", "clarification"}:
            issues.append("analytical obligation has unsupported authority")
    for req,role in role_sources:
        role = str(role).upper()
        if role not in {"BATTER", "PITCHER"}:
            issues.append("analytical role is unresolved or unsupported")
        else:
            assign("analytical_role", role, req, "finite bound subject-role resolution")
    for kind,field in (("SEASON", "season"), ("TIME", "date_range"), ("GAME_POPULATION", "population")):
        reqs = [r for r in requirements if r.get("kind") == kind]
        values = []
        for req in reqs:
            value = req.get("value")
            derivation = None
            if kind == 'SEASON':
                if isinstance(value,bool) or not str(value).isdigit():
                    issues.append('required season is invalid or unresolved'); continue
                value=int(value)
            if kind == 'TIME' and isinstance(value,dict) and ('start' in value or 'end' in value):
                if set(value)-{'start','end','inclusive'} or value.get('inclusive') is False:
                    issues.append('unsupported temporal endpoint semantics'); continue
                value={k:value.get(k) for k in ('start','end')}
            if kind == "TIME" and not (isinstance(value, dict) and ("start" in value or "end" in value)):
                scope = req.get("resolved_scope") or {}
                value = {k:scope.get(k) for k in ("start", "end")}
                if not any(value.values()):
                    issues.append("required temporal scope is unresolved"); continue
                derivation = "resolved authoritative TemporalScope endpoints"
            if value not in values: values.append(value)
            record(field, req, value, derivation)
        if len(values) == 1:
            result[field] = values[0]
        elif values:
            # A proven partition may be selected from the authoritative value set.
            chosen = proposal.get(field)
            if chosen not in values: issues.append(f"analytical partition is not bound to required {field}")
            else: result[field] = chosen
    entity_reqs = [r for r in requirements if r.get("kind") == "ENTITY"]
    result["require_entities"] = bool(entity_reqs)
    if not entity_reqs:
        result["entities"] = []
    else:
        try:
            for req in entity_reqs: record("require_entities",req,True)
            bound = _entities(result["entities"])
            authoritative = []
            for req in entity_reqs:
                value = req.get("value") or {}
                ids = value.get("canonical_ids") or ([value["canonical_id"]] if value.get("canonical_id") is not None else [])
                if ids and value.get("identity_verified"):
                    authoritative.append(dict(role=value.get("role") or result["analytical_role"],namespace=value.get("namespace") or "MLBAM",ids=ids))
            exports = inputs.get("_canonical_identity_exports") or []
            if bound and _entities(authoritative or exports) != bound:
                issues.append("canonical identities are not backed by Requirement or Binder")
            if result["analytical_role"] is None: issues.append("canonical identity has no authoritative analytical role")
            for req in entity_reqs:
                record("entities", req, [e.model_dump() for e in bound], "Binder canonical MLBAM identity export")
        except ValueError as error: issues.append(str(error))
    for req in requirements:
        if req.get("kind") != "MEASURE": continue
        value = req.get("value") or {}
        key = value.get("key") if isinstance(value, dict) else None
        if not key:
            issues.append("required Measure is unresolved"); continue
        result["measure_keys"].append(key)
        record("measures", req, key, "governed Measure definition")
    if not result["measure_keys"]:
        for req,text in zip(analyses,texts):
            for spec in resolve_texts([text]):
                result["measure_keys"].append(spec.key)
                record("measures", req, spec.key, "finite governed Measure recognition")
    if result["analytical_role"] is None and any(re.search(r"\bplayers?\b|球员|球員", text,re.I) for text in texts):
        issues.append("generic PLAYER analytical role is unproven")
    result["measure_keys"] = sorted(set(result["measure_keys"]))
    result["measure"] = " ".join(result["measure_keys"])
    context = " ".join(texts) or " ".join(str(k).replace("_", " ") for k in result["measure_keys"])
    constraints = result["execution_constraints"]
    primary,secondary = [],[]
    source_text = source_review_text if source_review_text is not None else inputs.get("bound_user_intent")
    if source_text:
        source_years = {int(y) for y in re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)",source_text)}
        bound_years = {int(r["value"]) for r in requirements if r.get("kind") == "SEASON" and isinstance(r.get("value"),int) and not isinstance(r.get("value"),bool)}
        for req in requirements:
            if req.get("kind") == "TIME":
                dates = req.get("value") if isinstance(req.get("value"),dict) else req.get("resolved_scope") or {}
                try: endpoints = [date.fromisoformat(str(dates[k])) for k in ("start","end") if dates.get(k)]
                except (ValueError,TypeError):
                    endpoints=[];issues.append("unresolved authoritative date endpoints")
                if endpoints: bound_years.update(range(min(d.year for d in endpoints),max(d.year for d in endpoints)+1))
        if source_years - bound_years:
            issues.append("user-source temporal clause has no authoritative bound scope")
        if len(source_years) == 1 and result['season'] is not None and result['season'] not in source_years:
            issues.append('selected season contradicts the original source year')
        population_phrases = {
            'REGULAR_SEASON':r'regular[ -]seasons?|常规赛|常規賽|例行赛|例行賽',
            'POSTSEASON':r'postseason|playoffs|季后赛|季後賽',
            'WORLD_SERIES':r'world series|世界大赛|世界大賽',
            'SPRING_TRAINING':r'spring training|春训|春訓',
        }
        explicit_codes = set()
        for name, pattern in population_phrases.items():
            if re.search(pattern,source_text,re.I):
                explicit_codes.update(CATALOG.resolve_population(name))
        if explicit_codes and set(CATALOG.resolve_population(result['population'] or '')) != explicit_codes:
            issues.append('user-source competition scope is not fully represented')
        source_range = re.search(r"\b(?:from|between)\s+(\d{4}-\d{2}-\d{2})\s+(?:through|to|and)\s+(\d{4}-\d{2}-\d{2})",source_text,re.I)
        if source_range:
            expected = DateRange(start=source_range[1],end=source_range[2])
            observed = DateRange.model_validate(result["date_range"]) if result["date_range"] else None
            if window(result["season"],expected) != window(result["season"],observed):
                issues.append("user-source effective date window is not fully represented")
    # Terms already represented by an authoritative structured obligation. The source
    # review subtracts them so a full natural-language question is not misread as lost
    # analytical residue while a genuinely unaccounted modifier still blocks.
    accounted_terms: list[str] = []
    for req in requirements:
        kind, value = req.get("kind"), req.get("value")
        if kind == "ENTITY" and isinstance(value, dict):
            for key in ("mention", "canonical_name"):
                if value.get(key): accounted_terms.append(str(value[key]))
        elif kind in {"GAME_POPULATION", "SEASON"} and value not in (None, ""):
            accounted_terms.append(str(value))
            if kind == 'SEASON': accounted_terms.extend(['season','seasons','year','years'])
            if kind == 'GAME_POPULATION' and str(value).upper() == 'REGULAR_SEASON':
                accounted_terms.extend(['regular season','regular-season','regular seasons','regular-seasons'])
        elif kind == "MEASURE" and isinstance(value, dict):
            for key in ("label", "key"):
                if value.get(key): accounted_terms.append(str(value[key]))
            spec = MEASURES.get(value.get('key'))
            if spec: accounted_terms.extend(spec.aliases)
            if str(value.get('key', '')).startswith('avg_'):
                accounted_terms.extend(['average','mean'])
        elif kind == "TIME":
            dates = value if isinstance(value, dict) else req.get("resolved_scope") or {}
            for key in ("start", "end"):
                if dates.get(key): accounted_terms.append(str(dates[key]))
        elif kind == "CONSTRAINT" and isinstance(value, dict):
            if req.get("derivation_kind") == "governed_semantic_candidate":
                # Source spans are provenance, never permission to consume a whole clause.
                # Recompute from current authoritative parents, not candidate-supplied
                # coverage metadata. The finite parser owns its own compatible matches;
                # only genuinely novel candidate wording needs subtraction.
                parents = [p for p in requirements
                           if p.get("id") in (req.get("parent_requirement_ids") or [])]
                for parent in parents:
                    if parent.get("kind") not in {"ANALYSIS", "EVIDENCE"}:
                        continue
                    parent_value = parent.get("value")
                    parent_text = str(parent.get("bound_source_clause") or
                                      ((parent_value or {}).get("objective") if isinstance(parent_value, dict)
                                       else parent_value) or parent.get("statement") or "")
                    assessment = assess_candidate(parent_text, value, parents)
                    if assessment.admitted:
                        accounted_terms.extend(assessment.accounted_spans)
            for span in value.get("ignored_spans") or []:
                # Defence in depth: an interpreter-declared non-answer span is only
                # subtracted from the source review when it is independently safe to
                # ignore (audit GA-02). The authority path rejects an unsafe candidate
                # first; this keeps a future caller from poisoning the accounted terms.
                if str(span).strip() and ignored_span_is_safe(str(span), requirements):
                    accounted_terms.append(str(span))
    # Backend-confirmed user semantic deltas are separate from interpreter attribution.
    # They can account only an exact cited candidate/parent region, after independent
    # authority resolution. An option selection, not a model restatement, supplies
    # permission to discharge a region. All other delta actions leave source residue.
    semantic_deltas = inputs.get('_semantic_deltas') or []
    confirmed_regions: dict[str, list[str]] = {}
    confirmed_fields: dict[str, set[str]] = {}
    confirmed_wording: dict[str, list[str]] = {}
    for delta in semantic_deltas:
        if delta.get('action') != 'DISCHARGE_RESIDUE':
            continue
        candidate = next((r for r in requirements if r.get('id') == delta.get('candidate_id')
                          and r.get('derivation_kind') == 'governed_semantic_candidate'), None)
        parent = next((r for r in requirements if r.get('id') == delta.get('requirement_id')
                       and r.get('kind') in {'ANALYSIS', 'EVIDENCE'}), None)
        span = delta.get('source_span')
        if (not candidate or not parent or not isinstance(candidate.get('value'), dict)
                or not isinstance(span, str) or not span.strip()
                or span not in (candidate['value'].get('source_spans') or [])
                or parent['id'] not in (candidate.get('parent_requirement_ids') or [])
                or span not in str(parent.get('bound_source_clause') or parent.get('value') or '')
                or not delta.get('user_answer') or not delta.get('source_goal_id')):
            issues.append('user clarification has no valid authoritative target')
            continue
        # Mask confirmed *source text* before finite extraction. Adding it to global
        # accounted_terms would run after finite parsing and could still bind a
        # condition the user just explicitly discharged, or erase a sibling clause.
        confirmed_regions.setdefault(parent['id'], []).append(span)
        confirmed_wording.setdefault(span, []).append(str(delta.get('confirmed_residue') or ''))
        confirmed_fields.setdefault(span, set()).update(
            f.get('field') for f in candidate['value'].get('filters') or [] if f.get('field'))
    # Planner/proposed entity display names are hints, not source-consumption proof.
    # Only independently bound Requirement surfaces and trusted Binder exports may
    # account for user text; otherwise a display label could erase an unknown filter.
    if entity_reqs:
        for dataset in inputs.get('_canonical_identity_exports') or []:
            for name in dataset.get('names') or []:
                accounted_terms.append(str(name))
    # The source review must always cover the *whole* authoritative wording of the current
    # goal/partition. It must never inherit another requirement's binding fields: an
    # interpreter ANALYSIS whose ``source_text`` names only one clause would otherwise
    # narrow the review and silently drop the remaining mandatory clauses (audit GA-01).
    source_reqs = [{
        "id": "source_review", "kind": "ANALYSIS", "source": "user", "required": True,
        "value": {"objective": source_text}, "statement": source_text,
        "bound_source_clause": "", "completion": "GROUNDED", "_source_review": True,
    }] if source_text and requirements else []
    for req in [*requirements,*source_reqs]:
        if req.get("kind") not in {"CONSTRAINT", "ANALYSIS"} or req.get("completion") == "EXPLANATION": continue
        value = req.get("value")
        if req.get("kind") == "ANALYSIS":
            value = ((value or {}).get("objective") or req.get("statement")) if isinstance(value, dict) else value or req.get("statement")
        value = req.get('bound_source_clause') or value
        if isinstance(value, str) and (req.get('_source_review') or req.get('id') in confirmed_regions):
            regions = ([region for owned in confirmed_regions.values() for region in owned]
                       if req.get('_source_review') else confirmed_regions[req['id']])
            # Overlapping confirmed regions are masked longest-first; removing a
            # narrow span first must not make a later broad confirmation ineffective.
            for confirmed in sorted(set(regions), key=len, reverse=True):
                if confirmed not in value:
                    continue
                # User confirmation discharges only the opaque meaning, never an
                # independently finite obligation embedded in a broad citation.
                # Retain exact consumed tokens for *other* fields in the source
                # review (e.g. inning or count beside an unknown hand phrase).
                mask = [' '] * len(confirmed)
                observed = constraint_coverage(confirmed)
                for clause in observed.get('clause_states') or []:
                    for consumed in clause.get('consumed_spans') or []:
                        token = str(consumed.get('text') or '')
                        if not token or token not in confirmed:
                            continue
                        obligations = constraint_coverage(token).get('obligations') or {}
                        fields = {f.get('field') for f in obligations.get('filters') or []}
                        if (not obligations or fields & confirmed_fields.get(confirmed, set())
                                or any(token in wording for wording in confirmed_wording.get(confirmed, []))):
                            continue
                        start = confirmed.index(token)
                        mask[start:start + len(token)] = token
                masked = ''.join(mask)
                # A finite parser may establish a compound scope without a
                # stand-alone token proof. Fail closed rather than silently
                # losing any such extra dimension during broad masking.
                surviving = constraint_coverage(masked).get('obligations') or {}
                discharged = constraint_coverage(' '.join(confirmed_wording.get(confirmed, []))).get('obligations') or {}
                for key, items in (observed.get('obligations') or {}).items():
                    if key not in {'filters', 'group_by', 'ordering', 'tie_break_ordering',
                                   'qualification', 'limit', 'population'}:
                        continue
                    before_items = items if isinstance(items, list) else [items]
                    after_items = surviving.get(key) or []
                    after_items = after_items if isinstance(after_items, list) else [after_items]
                    for item in before_items:
                        field = item.get('field') if isinstance(item, dict) else None
                        if (item not in after_items and field not in confirmed_fields.get(confirmed, set())
                                and item not in (discharged.get(key) if isinstance(discharged.get(key), list)
                                                 else [discharged.get(key)])):
                            issues.append('confirmed region contains another finite obligation')
                if (observed.get('unsupported') and not all(
                        str(item) in ' '.join(confirmed_wording.get(confirmed, []))
                        for item in observed['unsupported'])):
                    issues.append('confirmed region contains unsupported source meaning')
                value = value.replace(confirmed, masked, 1)
        # Lifecycle-owned cache: identical resolver inputs with unchanged authoritative
        # context do not execute again on Tool retries or replans. New information is
        # explicit in the context digest, and earlier operational entries survive.
        history = inputs.get('_resolution_state')
        context_id = digest(dict(role=result['analytical_role'], measures=result['measure_keys'],
                                 terms=sorted(set(accounted_terms)),
                                 authority=[{k:r.get(k) for k in ('id','kind','value')} for r in requirements],
                                 semantic_deltas=semantic_deltas,
                                 identities=inputs.get('_canonical_identity_exports') or [],
                                 synonyms=_SYNONYM_NORMALIZATION,
                                 fields=sorted(CATALOG.table('pitch').fields),
                                 resolver_version='lossless-v1'))
        clause_id = digest(dict(requirement=req.get('id'), source_review=bool(req.get('_source_review')), value=value))
        attempt_id = digest([clause_id,context_id])
        cache = history.setdefault('coverage', {}) if history is not None else {}
        if attempt_id in cache:
            from copy import deepcopy
            coverage = deepcopy(cache[attempt_id])
            history['duplicates_suppressed'] = history.get('duplicates_suppressed', 0) + sum(
                len(c.get('attempts', [])) for c in coverage.get('clause_states', []))
        else:
            coverage = constraint_coverage(value, result["analytical_role"], context, analysis=req.get("kind") == "ANALYSIS", accounted_terms=accounted_terms)
            if history is not None:
                from copy import deepcopy
                previous = [a for a in history.setdefault('attempts', []) if a.get('clause_id') == clause_id]
                for entry in coverage.get('clause_states', []):
                    for a in entry.get('attempts', []):
                        history['attempts'].append({**a, 'clause_id':clause_id, 'context_id':context_id,
                            'attempt_id':digest([clause_id,context_id,a.get('resolver'),a.get('input')]),
                            'retry_reason':'new authoritative context' if previous else 'initial resolution'})
                cache[attempt_id] = deepcopy(coverage)
        if history is not None:
            history.setdefault('current', {})[clause_id] = dict(requirement_id=req.get('id'),
                value=value, state=coverage['state'], unresolved=coverage.get('unresolved'),
                unsupported=coverage.get('unsupported'), context_id=context_id)
        if req.get("_source_review"):
            # Whole user wording includes presentation/research instructions. The
            # conservative residue test already waives only task/presentation language;
            # every remaining semantic modifier is preserved and blocks the contract.
            coverage = {**coverage, "non_analytical_residue": []}
        # Coverage is derived from clause states, never asserted by an adapter. A clause
        # that is unresolved/unsupported/invalid cannot be part of a COMPLETE requirement.
        blocking_states = {TEMPORARILY_UNRESOLVED, UNSUPPORTED, INVALID}
        if coverage["state"] == "COMPLETE" and any(
            str(entry.get("state")) in blocking_states for entry in coverage.get("clause_states") or []
        ):
            coverage = {**coverage, "state": "PARTIAL",
                        "unresolved": [*coverage["unresolved"], "clause-state ledger reports an unresolved clause"]}
        # Completeness is checked at the construction boundary as well as reported
        # by the adapter. A dropped clause/key cannot be hidden behind COMPLETE.
        expected_clauses = (set(value) - {"source_spans", "ignored_spans", "attributions"}) if isinstance(value,dict) else set(range(len(_mandatory_clauses(str(value)))))
        represented = {c.get("clause_index", c.get("clause")) for c in coverage["recognized"]}
        ledger = {}
        for entry in coverage["recognized"]:
            for field,item in entry["obligations"].items():
                if field == "analysis": continue
                if field in {"filters", "group_by", "ordering", "ordering_tail", "tie_break_ordering"}:
                    ledger.setdefault(field,[])
                    for member in item:
                        if member not in ledger[field]: ledger[field].append(member)
                else: ledger[field] = item
        if coverage["state"] == "COMPLETE" and ledger != coverage["obligations"]:
            coverage = {**coverage,"state":"PARTIAL","unresolved":[*coverage["unresolved"],"adapter obligation/coverage ledger disagrees"]}
        if coverage["state"] == "COMPLETE" and not req.get("_source_review") and expected_clauses - represented:
            coverage = {**coverage, "state":"PARTIAL", "unresolved":[*coverage["unresolved"], "adapter omitted mandatory clauses"]}
        result["clause_coverage"].append({"requirement_id":req.get("id", ""), **coverage})
        for defect in coverage.get("unsupported") or []:
            result["unresolved_obligations"].append({
                "requirement_id": req.get("id", ""), "state": "UNSUPPORTED", "reason": defect,
                "attempts": [a for entry in coverage.get("clause_states") or [] for a in entry.get("attempts") or []],
            })
        for leftover in coverage.get("unresolved") or []:
            result["unresolved_obligations"].append({
                "requirement_id": req.get("id", ""), "state": "TEMPORARILY_UNRESOLVED", "reason": str(leftover),
                "attempts": [a for entry in coverage.get("clause_states") or [] for a in entry.get("attempts") or []],
            })
        if coverage["state"] != "COMPLETE":
            issues.append(f"incomplete analytical obligation {req.get('id', '')}: {coverage['unresolved']}")
        obligation = coverage["obligations"]
        for field,value in obligation.items():
            record(field, req, value, "finite bound user-source adapter" if req.get("_source_review") else "finite analytical clause adapter", clause=source_text if req.get("_source_review") else None)
            if field in {"qualification", "analytical_role", "season", "date_range", "population"}:
                # A governed semantic candidate already defined the subject role from the
                # interpreter's reading; a brittle text role cue must not override it.
                if field == "analytical_role" and candidate_roles:
                    continue
                assign(field, value, req)
            elif field in {"group_by", "filters"}:
                constraints.setdefault(field, [])
                constraints[field] += [v for v in value if v not in constraints[field]]
            elif field == "ordering": primary.extend(value)
            elif field in {"ordering_tail", "tie_break_ordering"}: secondary.extend(value)
            elif field in constraints and constraints[field] != value: issues.append(f"incompatible required {field}")
            else: constraints[field] = value
    if primary: constraints["ordering"] = [o for i,o in enumerate(primary) if o not in primary[:i]] + [o for i,o in enumerate(secondary) if o not in secondary[:i]]
    elif secondary: constraints["tie_break_ordering"] = [o for i,o in enumerate(secondary) if o not in secondary[:i]]
    # Input membership does not determine output cardinality. Conserve the current
    # Requirements' subject units before applying the legacy scalar domain default.
    from app.semantic.subject_granularity import subject_grouping, subject_requirements
    if sum(r.get('kind') == 'ENTITY' for r in requirements) > 1:
        for req in analyses:
            semantics = req.get('relation_semantics') or {}
            relations = {semantics.get('relation_kind'), *(semantics.get('relation_kinds') or [])}
            if (req.get('comparison') or relations & {'COMPARISON', 'ENUMERATION', 'RANKING'}
                    or subject_requirements(req, requirements)):
                continue
            # The legacy role adapter may identify a statistical domain, but it
            # cannot supply absent output meaning or bypass subject conservation.
            reason = 'statistical subject output granularity is unresolved'
            issues.append(reason)
            result['unresolved_obligations'].append(dict(requirement_id=req.get('id', ''),
                state='TEMPORARILY_UNRESOLVED', reason=reason, attempts=[]))
    for field in subject_grouping(requirements, result['analytical_role']):
        if field not in constraints.setdefault('group_by', []):
            constraints['group_by'].append(field)
        for req in analyses:
            record('group_by', req, [field], 'Requirement-owned individual output unit')
    # Defaults are bound domain semantics, never permissions to choose a subset.
    for field,value in dict(relation="pitch", entities=[], analytical_role=None, season=None, date_range=None,
                            game_types=None, qualification=None, filters=[], group_by=[], ordering=None,
                            tie_break_ordering=[], limit=None, comparison_population=False, require_entities=False).items():
        if field not in provenance:
            provenance[field] = [dict(requirement_id=r.get("id", ""), source_clause=r.get("statement", ""),
                origin="domain", status="DERIVED", value=value, derivation="unrestricted/scalar domain default within bound scope") for r in requirements]
    if result["population"]:
        provenance["game_types"] = [{**p, "value": CATALOG.resolve_population(str(result["population"])),
                                     "derivation":"governed population code set", "status":"DERIVED"} for p in provenance.get("population", [])]
    return result, list(dict.fromkeys(issues))


def contract_for_goal(goal, artifacts, executed):
    """Independent current-Requirement view, also used for compact Scope admission."""
    entity_reqs = [r for r in goal.requirements if r.required and r.kind.value == "ENTITY"]
    entities = []
    for req in entity_reqs:
        value = req.value if isinstance(req.value, dict) else {}
        mention = value.get("mention", "")
        identity = None
        for owner in artifacts:
            if owner.kind != "ENTITY_RESOLUTION" or owner.status != "ACCEPTED": continue
            entry = (owner.structured_data.get("identity") or {}).get(mention)
            if entry and entry.get("level") == "CANONICAL": identity = entry
            for entry in owner.structured_data.get("resolved", []):
                if entry.get("mention") == mention and entry.get("id"): identity = entry
        if not identity or not identity.get("id"): return None
        entities.append(dict(role=value.get("role") or role_from_text(goal.raw_query) or "PLAYER",
                             namespace=value.get("namespace") or "MLBAM", ids=[identity["id"]]))
    inputs = dict(analytical_requirements=[r.model_dump(mode="json") for r in goal.requirements if r.required],
                  raw_query=goal.raw_query, bound_user_intent=goal.raw_query, resolved_time_scope=goal.resolved_time_scope,
                  _canonical_identity_exports=entities, _resolution_state=goal.analytical_resolution_state,
                  _semantic_deltas=[d.model_dump(mode='json') for d in goal.semantic_deltas])
    # Execution may identify a partition, but only membership in independently bound
    # Requirement scope values authorizes it. No Artifact requested field is consulted.
    intent = dict(entities=entities, season=executed.season,
                  date_range=executed.date_range.model_dump(mode="json") if executed.date_range else None)
    bound = requested_contract(intent, inputs, [], source_review_text=goal.analytical_partition_clause or None)
    return bound


def receipt_conforms_goal(artifact, goal, artifacts):
    """The receipt proves execution; current Requirements define the requested fact."""
    if not receipt_usable(artifact): return False
    executed = ExecutionAttestation.model_validate(artifact.structured_data["execution_receipt"]["attestation"])
    bound = contract_for_goal(goal,artifacts,executed)
    if bound is None: return False
    # Independent VALUE Requirements can retain supported claims while another
    # metric is unsupported. This does not relax any population or clause obligation;
    # Judge still evaluates every mandatory VALUE Requirement before COMPLETE.
    proven = {m["key"] for m in executed.measures if m.get("verified") and m.get("key")}
    if bound.measures:
        relevant = tuple(k for k in bound.measures if k in proven)
        if not relevant: return False
        bound = bound.model_copy(update={"measures":relevant})
    return compare(bound, executed).status in {"MATCH", "PARTIAL"}


def _analysis_task_text(requirement) -> str:
    """The user clause that names one independent analytical task.

    Prefer the interpreter's exact source text for the task; fall back to its objective
    and then the bound clause. This is the only partition key; it is never supplied by
    an Artifact.
    """
    value = requirement.value if isinstance(requirement.value, dict) else {}
    return str(
        value.get('source_text') or value.get('objective')
        or requirement.bound_source_clause or requirement.statement or ''
    ).strip()


def _analysis_has_grounding(requirement) -> bool:
    """Semantic-grounding signal for an analytical task.

    True only when the task's own wording names a governed Measure or a subject role.
    This is **necessary but not sufficient**: it never proves clause coverage, authority,
    ``UNDERSTOOD``, coverage ``COMPLETE``, or semantic independence. It exists so a
    deictic/empty fragment ("Show his numbers there") cannot be associated with an
    unrelated Artifact merely because a lexical residue test waived its words.
    """
    text = _analysis_task_text(requirement)
    if not text:
        return False
    if any(m.key for m in resolve_texts([text])):
        return True
    return role_from_text(text) is not None


def _task_mentions_entity(task_text: str, requirement) -> bool:
    """True when a task's own wording names this Entity Requirement.

    A structural attribution signal only; it does not resolve identity and never makes
    the entity authoritative. It lets two clearly separate subjects (each named in its
    own task) be judged independently instead of one ambiguous subject blocking all.
    """
    value = requirement.value if isinstance(requirement.value, dict) else {}
    haystack = (task_text or '').lower()
    for name in (value.get('mention'), value.get('canonical_name')):
        text = str(name or '').strip().lower()
        if text and text in haystack:
            return True
    return False


def _is_independent_analysis_task(requirement) -> bool:
    """Segmentation hint only: may this Requirement be judged as a standalone task?

    This function is deliberately **not** a semantic authority. It never proves
    completeness, Requirement authority, ``UNDERSTOOD``, coverage ``COMPLETE``,
    irrelevance of remaining text, or that the clause is safe to execute. It only
    decides whether a task may be *separated* from its siblings for independent Judge
    evaluation. Every partition is still checked by :func:`receipt_conforms_goal`
    against a contract rebuilt from current Requirements, including that task's own
    source review, so a ``True`` here cannot upgrade an unresolved clause.

    The signal is governed grounding (:func:`_analysis_has_grounding`) or the
    interpreter's ``EXPLANATION`` class. Lexical task verbs are intentionally *not* used:
    a task-shaped fragment ("Report the same split", "Show his numbers there",
    "Explain that") can still depend on omitted context and must fail closed. A bare
    modifier clause must not let a sibling analysis escape a shared obligation.
    """
    if getattr(requirement.completion, 'value', requirement.completion) == 'EXPLANATION':
        return True
    return _analysis_has_grounding(requirement)


def independent_analytical_partitions(goal):
    """Independent analytical task partitions derived from the Requirement graph.

    A partition exists only when the semantic decomposition produced more than one
    ANALYSIS Requirement with distinct user task wording. An Artifact never supplies
    partition membership. Shared scope/constraint Requirements and any
    MEASURE Requirement that is not attributable to exactly one task stay in every
    partition, so a shared or unresolved clause blocks all branches. One compound
    Requirement ("AVG against lefties with two strikes") is never split, because it
    yields a single task group. Approval remains Judge-owned and Requirement-local.
    """
    analyses = [
        r for r in goal.requirements
        if r.required and r.kind.value == 'ANALYSIS'
    ]
    if len(analyses) < 2:
        return []
    # Structural containment guard: if one task's wording is a sub-fragment of another's,
    # the decomposition produced a dependent piece (e.g. a compound "AVG against lefties
    # with two strikes" split into "AVG against lefties" and "with two strikes"). Such a
    # fragment must never become an independent partition. This is language-agnostic and
    # does not decide meaning; it fails closed.
    task_texts = [_analysis_task_text(r).lower().strip() for r in analyses]
    for outer in task_texts:
        if not outer:
            continue
        for inner in task_texts:
            if inner and inner != outer and inner in outer:
                return []
    groups: dict[str, list] = {}
    for requirement in analyses:
        # A partition is only legitimate for a task the interpreter explicitly produced
        # from its own user wording and that names a governed measure or subject role. A
        # bare modifier clause ("under a closed roof") is not an independent task, so it
        # must not let a sibling analysis escape its shared obligation.
        if not _is_independent_analysis_task(requirement):
            return []
        key = _analysis_task_text(requirement).lower()
        groups.setdefault(key, []).append(requirement)
    if len(groups) < 2:
        return []
    shared = [r for r in goal.requirements if r.kind.value not in {'ANALYSIS', 'MEASURE', 'EVIDENCE'}]
    measures = [r for r in goal.requirements if r.required and r.kind.value == 'MEASURE']
    entities = [r for r in shared if r.kind.value == 'ENTITY']
    other_shared = [r for r in shared if r.kind.value != 'ENTITY']
    # Conservative per-task entity attribution: only when there are two or more distinct
    # subjects and every task names its own subject. Otherwise every entity is a shared
    # clause and an unresolved identity blocks all branches (the fail-closed default).
    owned_entities: dict[str, list] = {key: [r for r in entities if _task_mentions_entity(key, r)] for key in groups}
    per_task_entities = bool(
        len(entities) >= 2
        and all(owned_entities[key] for key in groups)
        and all(any(r in owned_entities[key] for key in groups) for r in entities)
    )
    attributed: dict[str, list] = {}
    for key in groups:
        found = {m.key for m in resolve_texts([key]) if m.key}
        attributed[key] = [r for r in measures if isinstance(r.value, dict) and r.value.get('key') in found]
    attributed_ids = {r.id for members in attributed.values() for r in members}
    partitions = []
    for key, group in groups.items():
        local_measure_ids = {r.id for r in attributed[key]}
        local_entities = owned_entities[key] if per_task_entities else entities
        selected = [*other_shared, *local_entities, *group]
        for measure in measures:
            # A measure attributable to exactly one task is local; an unattributable
            # measure is a global obligation and must be accounted for in every branch.
            if measure.id in local_measure_ids or measure.id not in attributed_ids:
                selected.append(measure)
        seen: set[str] = set()
        selected = [r for r in selected if not (r.id in seen or seen.add(r.id))]
        selected_ids = {r.id for r in selected}
        # Existing proposition dependencies are authoritative for partial blocking.
        props = {p.id: p for p in goal.propositions}
        if any(any(dep not in props or props[dep].requirement_id not in selected_ids for dep in p.depends_on)
               for p in goal.propositions if p.requirement_id in selected_ids):
            continue
        raw = _analysis_task_text(group[0]) or goal.raw_query
        # A semantic partition is not a new user message. Retain the original authority
        # root while narrowing only the source-review clause derived from Requirements.
        local = goal.model_copy(update={'requirements': selected, 'analytical_partition_clause': raw, 'propositions': []})
        partitions.append(local)
    return partitions


def receipt_requirement_ids(artifact, goal, artifacts):
    """Current Requirements admit only their own independently correct evidence."""
    if not receipt_usable(artifact): return set()
    executed = ExecutionAttestation.model_validate(artifact.structured_data['execution_receipt']['attestation'])
    proven = {m['key'] for m in executed.measures if m.get('verified') and m.get('key')}
    # The grounding gate is only needed to disambiguate between sibling analytical tasks.
    # With a single mandatory ANALYSIS the Artifact cannot be confused with another task.
    analysis_count = sum(1 for r in goal.requirements if r.kind.value == 'ANALYSIS' and r.required)
    allowed=set()
    candidates = [goal] if receipt_conforms_goal(artifact,goal,artifacts) else independent_analytical_partitions(goal)
    for local in candidates:
        if local is not goal and not receipt_conforms_goal(artifact,local,artifacts): continue
        for req in local.requirements:
            if req.kind.value == 'MEASURE' and (not isinstance(req.value,dict) or req.value.get('key') not in proven): continue
            if req.kind.value in {'ANALYSIS','EVIDENCE'}:
                if req.kind.value == 'EVIDENCE' or getattr(req.completion,'value',req.completion) == 'EXPLANATION': continue
                keys={m.key for m in resolve_texts([req.bound_source_clause or str(req.value)]) if m.key}
                if keys - proven: continue
                # A statistical task that names neither a governed Measure nor a subject
                # role is not grounded. When several sibling tasks exist, it must not be
                # satisfied by an unrelated Artifact merely because its deictic wording
                # was lexically waived. Grounding is necessary, never a completeness proof.
                if analysis_count > 1 and not keys and not _analysis_has_grounding(req):
                    continue
            allowed.add(req.id)
    props={p.id:p for p in goal.propositions}
    for _ in range(len(props)+1):
        blocked={p.requirement_id for p in props.values() if p.requirement_id in allowed
                 and any(dep not in props or props[dep].requirement_id not in allowed for dep in p.depends_on)}
        if not blocked: break
        allowed -= blocked
    return allowed


def role_from_text(text):
    """Finite explicit subject-role cues; opponent handedness does not set role."""
    text = str(text).lower()
    # Opponent/grouping dimensions are not subject-role declarations.
    text = re.sub(r"投手(?:的)?(?:左右手|惯用手|慣用手|投球手|左右投)|左右投手|(?:opposing )?pitcher[ _]handedness|(?:面对|面對|对阵|對陣|對|对)[左右LRlr]投手?", "", text)
    text = re.sub(r'(?:面对|面對|对阵|對陣)左(?:手)?投手?\s*(?:和|与|與|及|/|vs)\s*右(?:手)?投手?|(?:against|versus|vs)\s+left[ -]handed\s*(?:pitchers?)?\s*(?:and|versus|vs|/)\s*right[ -]handed\s+pitchers?', '', text)
    text = re.sub(r'(?<![A-Za-z])(?:against|versus|vs|facing|restricted to)\s+(?:only\s+)?(?:left|right|l|r)(?:[ -]handed)?\s+pitchers?', '', text)
    batter = bool(re.search(r"(?<![A-Za-z])(?:batting(?!\s+average)|hitting|hitters?|batters?)(?![A-Za-z])|打击(?!率)|打擊(?!率)|打者", text))
    pitcher = bool(re.search(r"(?<![A-Za-z])(?:pitching|pitchers?)(?![A-Za-z])|投球|投手", text))
    if not batter and not pitcher and re.search(r'how did.+(?<![A-Za-z])hit(?![A-Za-z])',text,re.I): batter=True
    if not batter and not pitcher and re.search(r'batting average|打击率|打擊率|击球率|擊球率',text):
        if re.search(r'被打击率|被打擊率',text): pitcher=True
        else: batter=True
    return "BATTER" if batter and not pitcher else "PITCHER" if pitcher and not batter else None


def _mandatory_clauses(text):
    """Finite top-level conjunction splitter, preserving quotes, IN and BETWEEN."""
    parts, start, depth, quote, between = [], 0, 0, None, False
    token = re.compile(r"['\"]|[()]|\bBETWEEN\b|\bAND\b|\b(?:AND ALSO|AS WELL AS)\b|并且|以及|且|[;；]", re.I)
    for match in token.finditer(text):
        word = match.group()
        if word in {"'", '\"'}:
            if word == "'" and quote is None and match.start() > 0 and match.end() < len(text) and text[match.start()-1].isalnum() and text[match.end()].isalnum():
                continue  # name/possessive apostrophe, not a quoted predicate literal
            if quote == word: quote = None
            elif quote is None: quote = word
            continue
        if quote: continue
        if word == "(": depth += 1; continue
        if word == ")": depth -= 1; continue
        if depth: continue
        if word.upper() == "BETWEEN": between = True; continue
        if word.upper() == "AND" and between: between = False; continue
        parts.append(text[start:match.start()].strip()); start = match.end()
    parts.append(text[start:].strip())
    return parts


# ------------------------------------------------------------------ clause states

# Clause-level interpretation states (M2.12R3). These are deliberately distinct from the
# aggregate COMPLETE/PARTIAL/UNRESOLVED contract state so a mandatory clause can never
# disappear into opaque residue while the Requirement is still called covered.
UNDERSTOOD = "UNDERSTOOD"
TEMPORARILY_UNRESOLVED = "TEMPORARILY_UNRESOLVED"
UNSUPPORTED = "UNSUPPORTED"
INVALID = "INVALID"

# Shared Knowledge: stable terminology lives in ``app.semantic.lexicon``. These names
# are re-exported here so existing consumers/tests keep their import path while the actual
# vocabulary has a single definition.
from app.semantic.lexicon import (  # noqa: E402  (import after module docstring constants)
    DOMAIN_ACCOUNTED_PHRASES as _DOMAIN_ACCOUNTED_PHRASES,
    INNING_WORDS as _INNING_WORDS,
    NON_SEMANTIC_WORDS as _NON_SEMANTIC_WORDS,
    ROLE_WORDS as _ROLE_WORDS,
)

# A finite, closed vocabulary of task/presentation words. It is NOT a relevance test by
# keyword absence: only tokens that survive removal of consumed spans, recognized role
# cues and this closed vocabulary are treated as semantically relevant. Anything unknown
# remains unresolved. Defined in ``app.semantic.lexicon`` (classification C: shared
# finite task-language, not the primary understanding mechanism).

# Recognized semantic dimensions that current pitch-level data cannot support. They are
# preserved and reported as UNSUPPORTED, never waived into non-analytical residue.
_UNSUPPORTED_DIMENSIONS = (
    (r"\b(?:day|night)[ -]?games?\b|\bdaytime\b|白天比赛|夜场", "game time-of-day (no governed field)"),
    (r"\b(?:road|away|home)[ -]?games?\b|\bon the road\b|\bat home\b|\baway from home\b|客场|主场", "home/away game context (no governed field)"),
    (r"\bdouble[ -]?headers?\b|双重赛|双头赛", "doubleheader context (no governed field)"),
    (r"\b(?:at|in)\s+(?:the\s+)?[A-Z][A-Za-z .'\-]*(?:Park|Stadium|Field|Dome)\b", "ballpark context (no governed field)"),
    (r"\b(?:temperature|weather|wind)\b|天气|温度|风速", "weather context (no governed field)"),
    (r"\b(?:closed|sealed|open|retractable)[ -]?roof\b|\broof(?:ed)?\b|\bdomed?\b|\bindoor\b|\boutdoor\b|室内|室外|封闭球场", "roof/indoor context (no governed field)"),
)

# Finite Shared Knowledge synonym normalization used only for semantic *resolution*
# attempts. It maps a recognised phrase to its canonical user-facing form; it never
# invents a factual requirement. Handedness phrases are centralised in the lexicon.
from app.semantic.lexicon import HANDEDNESS_PHRASES as _HANDEDNESS_PHRASES  # noqa: E402

_SYNONYM_NORMALIZATION = (*_HANDEDNESS_PHRASES, (r"\bextra[ -]?innings?\b|加时局|延长局", "extra innings"))


def _content_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for match in re.finditer(r"[0-9]+(?:\.[0-9]+)?|[a-z][a-z0-9'\-]*|[\u4e00-\u9fff]+", (text or "").lower()):
        token = match.group(0)
        tokens.add(token)
    return tokens


def residue_is_semantically_relevant(residue: str, *, role_cue: bool = False) -> bool:
    """Conservative relevance test for unparsed residue.

    Absence of a known modifier keyword is NOT proof of irrelevance. Residue is waived
    only when every content token is a closed task/presentation word or a recognised
    role word (whose role obligation is recorded separately). Anything else remains
    semantically relevant and therefore unresolved.
    """
    tokens = _content_tokens(residue)
    tokens -= _NON_SEMANTIC_WORDS
    tokens -= _ROLE_WORDS
    return bool(tokens)


def _inning_number(token: str) -> int | None:
    token = str(token or "").lower()
    if token.isdigit():
        return int(token)
    ordinal = re.fullmatch(r"(\d+)(?:st|nd|rd|th)?", token)
    if ordinal:
        return int(ordinal.group(1))
    return _INNING_WORDS.get(token)


def _consume_inning_restriction(clause: str, part: dict, consume) -> None:
    """Finite support for inning/context restrictions mapped to the governed inning field."""
    def later(m):
        n = _inning_number(m.group(1))
        if n:
            part.setdefault("filters", []).append(dict(op="COMPARE", field="inning", comparator=">=", value=n))

    def exact(m):
        n = _inning_number(m.group(1))
        if n:
            part.setdefault("filters", []).append(dict(op="COMPARE", field="inning", comparator="=", value=n))

    def after(m):
        n = _inning_number(m.group(1))
        if n:
            part.setdefault("filters", []).append(dict(op="COMPARE", field="inning", comparator=">", value=n))

    def before(m):
        n = _inning_number(m.group(1))
        if n:
            part.setdefault("filters", []).append(dict(op="COMPARE", field="inning", comparator="<", value=n))

    def through(m):
        n = _inning_number(m.group(1))
        if n:
            part.setdefault("filters", []).append(dict(op="COMPARE", field="inning", comparator="<=", value=n))

    ordinal = r"(\d+(?:st|nd|rd|th)?|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|[一二三四五六七八九十])"
    consume(rf"\b(?:from\s+)?(?:the\s+)?{ordinal}\s+inning\s+(?:onward|onwards|on|and\s+later|or\s+later)\b", later)
    consume(r"\b(?:during\s+|in\s+|throughout\s+)?extra[ -]?innings?\b", lambda m: part.setdefault("filters", []).append(dict(op="COMPARE", field="inning", comparator=">=", value=10)))
    consume(rf"\bin\s+(?:the\s+)?{ordinal}\s+inning\b", exact)
    consume(rf"\bafter\s+(?:the\s+)?{ordinal}\s+inning\b", after)
    consume(rf"\bbefore\s+(?:the\s+)?{ordinal}\s+inning\b", before)
    consume(rf"\b(?:first|through\s+(?:the\s+)?|up\s+to\s+(?:the\s+)?){ordinal}\s+innings?\b", through)
    consume(rf"第\s*{ordinal}\s*局(?:起|以后|以後|及以后|及以後)", later)
    consume(rf"第\s*{ordinal}\s*局", exact)
    consume(r'(?<![A-Za-z0-9])inning[ _](\d+)(?![A-Za-z0-9])', exact)
    consume(rf"\b(?:in\s+)?innings?\s+{ordinal}\s+(?:and|or)\s+(?:later|higher)\b", later)
    consume(rf'(?<![A-Za-z0-9]){ordinal}\s+inning(?![A-Za-z0-9])', exact)


def _consume_unsupported_dimensions(clause: str, unsupported: list, consume) -> None:
    for pattern, label in _UNSUPPORTED_DIMENSIONS:
        consume(pattern, lambda m, label=label: unsupported.append(label))


def _normalize_synonyms(clause: str) -> str:
    text = clause
    for pattern, canonical in _SYNONYM_NORMALIZATION:
        text = re.sub(pattern, canonical, text, flags=re.I)
    return text


def _consume_domain_filters(clause_text, part, consume):
    """Closed count/handedness vocabulary, including meaningful short and CJK tokens."""
    from app.semantic.lexicon import COUNT_WORDS, SIDE_VALUES

    def filt(field, comparator, value):
        part.setdefault('filters', []).append(dict(op='COMPARE',field=field,comparator=comparator,value=value))
    side = lambda text: SIDE_VALUES.get(str(text).lower(), 'R')
    consume(r"(?<![A-Za-z])(?:against|versus|vs|facing|restricted to)\s+(?:only\s+)?(left|right|L|R)(?:[ -]handed)?\s+pitchers?", lambda m:filt('p_throws','=',side(m[1])))
    consume(r"(?<![A-Za-z])(?:for\s+)?(?:only\s+)?(L|R|left|right)(?:[ -]handed)?\s+(batters?|hitters?|pitchers?)(?![A-Za-z])", lambda m:filt('p_throws' if m[2].lower().startswith('pitch') else 'stand','=',side(m[1])))
    consume(r"(?:面对|面對|对阵|對陣|對|对)?([左右LR])(?:投手|投)", lambda m:filt('p_throws','=',side(m[1])))
    consume(r"(?:仅|僅)?([左右LR])(?:打者|打)", lambda m:filt('stand','=',side(m[1])))
    consume(r'(?<![A-Za-z])(?:ball|strike)[ _]count\s*=\s*([0-3])(?![0-9])', lambda m:filt('balls' if m.group().lower().startswith('ball') else 'strikes','=',int(m[1])))
    nums = COUNT_WORDS
    def num(t): return int(t) if t.isdigit() else nums[t.lower()]
    consume(r"(?<![A-Za-z])(?:with|on|at|when)?\s*(zero|one|two|three|[0-3])\s+(strikes?|balls?)(?![A-Za-z])", lambda m:filt('strikes' if m[2].lower().startswith('strike') else 'balls','=',num(m[1])))
    consume(r"(?<![A-Za-z])(?:when\s+)?(balls|strikes)\s+(?:are|is|=)\s*(zero|one|two|three|[0-3])(?![A-Za-z0-9])", lambda m:filt(m[1].lower(),'=',num(m[2])))
    consume(r"([零一两兩二三0-3])(?:个|個)?(好球|坏球|壞球)", lambda m:filt('strikes' if m[2]=='好球' else 'balls','=',num(m[1])))


def _run_resolvable_consumers(clause_text: str, part: dict):
    spans = []
    def consume(pattern, action, flags=re.I):
        for m in re.finditer(pattern, clause_text, flags):
            if any(a < m.end() and m.start() < b for a,b in spans): continue
            try:
                action(m)
                spans.append(m.span())
            except (ValueError,TypeError,KeyError,SyntaxError): continue
    _consume_domain_filters(clause_text, part, consume)
    _consume_inning_restriction(clause_text, part, consume)
    remainder = ''.join(' ' if any(a <= j < b for a,b in spans) else ch for j,ch in enumerate(clause_text))
    return spans, remainder


def _resolve_clause(clause: str, *, role=None) -> tuple[dict, list[dict], str]:
    """Bounded, operationally-recorded resolution attempts for one unresolved clause.

    Resolvers are finite: Shared Knowledge synonym normalization, SchemaCatalog field
    binding, and capability/Measure recognition. Each attempt records what was tried,
    what was learned and whether it resolved. Repeated identical attempts within one
    clause are suppressed. No factual obligation is invented.
    """
    attempts: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def record(resolver: str, payload: str, outcome: str, learned: str = "") -> None:
        key = (resolver, payload)
        if key in seen:
            return
        seen.add(key)
        attempts.append(dict(resolver=resolver, input=payload, outcome=outcome, learned=learned))

    part: dict[str, Any] = {}
    # Attempt 1: Shared Knowledge synonym normalization.
    normalized = _normalize_synonyms(clause)
    record("shared_knowledge.synonym", clause, "changed" if normalized != clause else "no_match", normalized if normalized != clause else "")
    # A resolver success accounts only for its consumed spans. Re-run the finite
    # chain on the remainder, never on already consumed content.
    remainder = normalized
    for _ in range(4):
        spans, remaining = _run_resolvable_consumers(remainder, part)
        record('schema.field_binding', remainder, 'partial' if spans else 'no_match', ','.join(sorted(part)))
        attempts[-1].update(consumed_spans=[dict(start=a,end=b,text=remainder[a:b]) for a,b in spans],
                            remaining=remaining, obligations=dict(part))
        remainder = remaining.strip(' ,.:，。')
        if not residue_is_semantically_relevant(remainder):
            attempts[-1]['outcome'] = 'resolved'
            return part, attempts, UNDERSTOOD
        if not spans: break
    # Attempt 3: capability Measure recognition (learning only; not an obligation by itself).
    measures = resolve_texts([clause])
    record("capability.measure", clause, "matched" if measures else "no_match", ",".join(m.key for m in measures))
    fields = [f for f in CATALOG.table("pitch").fields if re.search(rf"\b{re.escape(f)}\b", clause, re.I)]
    record("schema.field_presence", clause, "field_found" if fields else "no_field", ",".join(fields))
    attempts.append(dict(resolver='semantic.remainder', input=remainder, outcome='unresolved',
                         remaining=remainder, consumed_spans=[], obligations=dict(part)))
    return part, attempts, TEMPORARILY_UNRESOLVED


# Keep accounting terms in the same surface form as residue after the finite
# clause adapter removes its existing harmless connective grammar.
_ACCOUNTING_CONNECTIVES = r"\b(?:with|among|only|results?|by|the|a|an)\b"


def constraint_coverage(value, role=None, measure_context="", *, analysis=False, accounted_terms=()):
    """Finite extraction with mandatory-clause coverage, not substring success.

    Structured clauses are traversed individually. Prose spans are accounted for;
    unresolved semantic residue survives in the requested contract and blocks it.
    """
    obligations, recognized, unresolved = {}, [], []
    clauses_state: list[dict[str, Any]] = []
    unsupported: list[str] = []
    def merge(part):
        for key,item in part.items():
            if key in {"filters", "group_by", "ordering", "ordering_tail", "tie_break_ordering"}:
                obligations.setdefault(key, [])
                for x in item:
                    if x not in obligations[key]: obligations[key].append(x)
            elif key in obligations and obligations[key] != item:
                unresolved.append(f"conflicting {key}")
            else: obligations[key] = item
    if isinstance(value, dict):
        allowed = {"qualification", "basis", "minimum", "filters", "group_by", "ordering", "tie_break_ordering", "limit", "analytical_role", "season", "date_range", "population", "game_types", "source_spans", "ignored_spans", "attributions"}
        for key,item in value.items():
            if key in {"source_spans", "ignored_spans", "attributions"}:
                # Semantic-candidate provenance: the exact user spans the interpretation
                # came from / consciously treated as task wording. Validated by the
                # candidate proof, not an obligation.
                continue
            # Finite sample-size encodings emitted by semantic interpreters. Unknown
            # keys still remain UNSUPPORTED; this is not an open dict adapter.
            sample_key = re.fullmatch(r'(?:min_(pa|bbe|pitches|games|results)|(pa|bbe|pitches|games|results)_min)',key,re.I)
            if sample_key:
                try:
                    q=qualification({'basis':next(x for x in sample_key.groups() if x).upper(),'minimum':item})
                    merge({'qualification':q})
                    recognized.append(dict(clause=key,obligations={'qualification':q}))
                    clauses_state.append(dict(clause=key,state=UNDERSTOOD,detail='',attempts=[]))
                except (ValueError,TypeError):
                    unresolved.append({key:item})
                    clauses_state.append(dict(clause=key,state=TEMPORARILY_UNRESOLVED,detail='invalid sample minimum',attempts=[]))
                continue
            if key not in allowed:
                unresolved.append({key:item})
                clauses_state.append(dict(clause=key, state=UNSUPPORTED, detail="unsupported obligation key", attempts=[]))
                continue
            try:
                if key in {"basis", "minimum"}:
                    q = qualification(value)
                    merge({"qualification":q})
                elif key == "qualification": merge({key:qualification(item)})
                elif key == "filters": validate_predicates(item); merge({key:item})
                elif key == "group_by":
                    if not isinstance(item, (list,tuple)) or any("GROUP_BY" not in CATALOG.field("pitch", f).ops for f in item): raise ValueError("unsupported grouping")
                    merge({key:list(item)})
                elif key == "limit":
                    if isinstance(item,bool) or item is not None and (not isinstance(item,int) or not 1 <= item <= 500): raise ValueError("unsupported top-N")
                    merge({key:item})
                elif key == "analytical_role":
                    if item not in {"BATTER", "PITCHER"}: raise ValueError("unresolved role")
                    merge({key:item})
                else: merge({key:item})
                recognized.append(dict(clause=key, obligations={"qualification":q} if key in {"basis", "minimum"} else {key:qualification(item)} if key == "qualification" else {key:item}))
                clauses_state.append(dict(clause=key, state=UNDERSTOOD, detail="", attempts=[]))
            except (ValueError,TypeError,KeyError) as error:
                unresolved.append({key:str(error)})
                clauses_state.append(dict(clause=key, state=TEMPORARILY_UNRESOLVED, detail=str(error), attempts=[]))
    elif value is not None:
        text = str(value).replace("_", " ")
        # Field spellings stay canonical for finite field/operator predicates.
        raw = str(value)
        clauses = _mandatory_clauses(raw)
        fields = "|".join(sorted(CATALOG.table("pitch").fields, key=len, reverse=True))
        for index,clause in enumerate(clauses):
            recognized_start = len(recognized)
            part, spans = {}, []
            def consume(pattern, action, flags=re.I):
                for m in re.finditer(pattern, clause, flags):
                    if any(a < m.end() and m.start() < b for a,b in spans): continue
                    try: action(m); spans.append(m.span())
                    except (ValueError,TypeError,KeyError,SyntaxError) as error: unresolved.append(str(error))
            consume(r"(?:\b(PA|BBE|PITCHES|GAMES|RESULTS|PLATE APPEARANCES)\s*(?:>=|≥|AT LEAST|MINIMUM|MIN)\s*\d+\b|(?:AT LEAST|MINIMUM|MIN|至少|>=|≥)\s*(?:有\s*)?\d+\s*(?:个|個|次)?\s*(?:PA\b|BBE\b|PITCHES\b|GAMES\b|RESULTS\b|PLATE APPEARANCES\b|打席))", lambda m: part.update(qualification=qualification(m.group())))
            def predicate(m):
                field,op,token = m.group(1),m.group(2),m.group(3)
                spec = CATALOG.field("pitch",field)
                val = float(token.strip("'\"")) if spec.type in {"int","float"} else token.strip("'\"")
                p = dict(op="COMPARE",field=field,comparator=op,value=val)
                validate_predicates([p]); part.setdefault("filters",[]).append(p)
            consume(rf"\b({fields})\s*(>=|<=|!=|=|>|<)\s*('(?:[^']*)'|\"[^\"]*\"|[A-Za-z_]+|-?\d+(?:\.\d+)?)", predicate)
            def in_predicate(m):
                import ast
                tokens = ast.literal_eval("["+m.group(2)+"]")
                p = dict(op="IN",field=m.group(1),values=tokens)
                validate_predicates([p]); part.setdefault("filters",[]).append(p)
            consume(rf"\b({fields})\s+IN\s*\(([^()]*)\)", in_predicate)
            dimensions = {"p_throws":[r"(?:opposing )?pitcher[ _]handedness", r"p_throws", r"投手(?:的)?(?:左右手|惯用手|慣用手|投球手|左右投)",r"左右投手"],
                          "stand":[r"batter[ _]handedness",r"stand",r"打者(?:的)?(?:左右手|惯用手|慣用手|左右打)"],
                          "pitch_type":[r"pitch[ _]type",r"球种",r"球種"]}
            for f,spec in CATALOG.table("pitch").fields.items():
                if "GROUP_BY" in spec.ops: dimensions.setdefault(f,[]).append(re.escape(f).replace("_", "[ _]"))
            split_outputs = "|".join(re.escape(alias).replace("_", "[ _]") for spec in MEASURES.values() for alias in spec.aliases)
            if re.search(r'分别|分別|拆分|分组|分組|separately|split|break down',clause,re.I):
                def side_contrast(m):
                    part.setdefault('group_by',[]).append('p_throws')
                    if not re.search(r'(?:by|按|依|根据|根據)[ _]*(?:(?:opposing )?pitcher[ _]handedness|p_throws|投手(?:的)?(?:惯用手|慣用手|左右手|投球手))',clause,re.I):
                        part.setdefault('filters',[]).append(dict(op='IN',field='p_throws',values=['L','R']))
                consume(r'(?:面对|面對|对阵|對陣)?左(?:手)?投手?\s*(?:和|与|與|及|/|vs)\s*右(?:手)?投手?',side_contrast)
                consume(r'(?:against|versus|vs)?\s*left[ -]handed\s*(?:pitchers?)?\s*(?:and|versus|vs|/)\s*right[ -]handed\s+pitchers?',side_contrast)
                consume(r'(?:vs|versus|against)?\s*LHP\s*(?:and|vs|versus|/)\s*(?:vs|versus|against)?\s*RHP',side_contrast)
            for field,forms in dimensions.items():
                pattern = rf"(?:(?:split|break down|group(?:ed|ing)?)(?:[ _]by|\s+(?:results|metrics|data|them|{split_outputs})\s+by)?[ _]+(?:{'|'.join(forms)})|(?:按|依|根据|根據)\s*(?:{'|'.join(forms)})\s*(?:分组|分組|分项|分項|拆分|分开|分開))"
                consume(pattern, lambda m,f=field: part.setdefault("group_by",[]).append(f))
            if part.get('group_by'): consume(r'\bseparately\b',lambda m:None)
            def per_group(m):
                phrase = m.group(1).lower().replace("-", " ").replace("_", " ")
                names = {f.replace("_", " "):f for f,spec in CATALOG.table("pitch").fields.items() if "GROUP_BY" in spec.ops}
                names.update({"pitcher":"pitcher_id", "batter":"batter_id", "pitch type":"pitch_type", "pitcher handedness":"p_throws", "batter handedness":"stand"})
                grouped=[]
                while phrase.strip():
                    phrase = phrase.strip()
                    found = next((name for name in sorted(names,key=len,reverse=True) if phrase == name or phrase.startswith(name+" ")),None)
                    if not found: raise ValueError("unsupported per-group dimension")
                    grouped.append(names[found]);phrase=phrase[len(found):]
                part.setdefault("group_by",[]).extend(grouped)
            consume(r"\bin each\s+([A-Za-z][A-Za-z _-]*?)\s+group\b", per_group)
            def opponent(m):
                side = "R" if m.group(1).lower() == "right" else "L"
                part.setdefault("filters",[]).append(dict(op="COMPARE",field="p_throws",comparator="=",value=side))
            consume(r"\b(?:against|versus|vs)\s+(?:only\s+)?(right|left)[ -]handed pitchers?(?:\s+only)?", opponent)
            unsupported_local: list[str] = []
            _consume_unsupported_dimensions(clause, unsupported_local, consume)
            _consume_inning_restriction(clause, part, consume)
            _consume_domain_filters(clause, part, consume)
            consume(r'(打者|投手)中',lambda m:part.update(analytical_role='BATTER' if m[1]=='打者' else 'PITCHER'))
            consume(r'(?<!\d)(?:from|between|从|從)\s*(\d{4}-\d{2}-\d{2})\s*(?:through|to|and|至|到)\s*(\d{4}-\d{2}-\d{2})(?:\s+(?:inclusive|including both endpoints))?',
                    lambda m:part.update(date_range={'start':m[1],'end':m[2]}))
            identity = {"PITCHER":"pitcher_id", "BATTER":"batter_id"}.get(str(role_from_text(clause) or role).upper())
            # Resolve context only from other authoritative clauses/Measures.
            metrics = resolve_texts([re.split(r"\b(?:with at least|among|minimum|at least)\b|至少", clause, maxsplit=1)[0]])
            if not metrics: metrics = resolve_texts([measure_context])
            def ranking(m):
                if not identity: raise ValueError("ranking role is unresolved")
                if identity not in part.setdefault("group_by",[]): part["group_by"].insert(0,identity)
                top = re.search(r"(?:(?:top|leading)\s+|前\s*)(\d+)",m.group(),re.I)
                if top: part["limit"] = int(top[1])
                elif re.search(r'leader(?!board)|最多|最少|最高|最低',m.group(),re.I): part['limit']=1
                if len(metrics) == 1: part["ordering"] = [dict(metric=metrics[0].key,direction="ASC" if re.search(r'lowest|最少|最低',clause,re.I) else "DESC")]
                elif re.search(r"by|highest|lowest|most",m.group(),re.I): raise ValueError("ranking Measure is unresolved")
            consume(r"(?<![A-Za-z])(?:top\s+\d+|leading\s+\d+|leaderboard|leader|rank(?:ing)?|most|highest|lowest)(?:\s+(?:batters?|hitters?|pitchers?))?(?![A-Za-z])|前\s*\d+\s*名|最多|最少|最高|最低", ranking)
            def explicit_direction(m):
                if len(metrics) != 1: raise ValueError('ordering Measure is unresolved')
                part['ordering']=[dict(metric=metrics[0].key,direction='ASC' if m.group().lower() in {'ascending','asc','从低到高','從低到高'} else 'DESC')]
            consume(r'(?:ties?|tie[ -]break(?:er)?)(?:\s+broken)?(?:\s+by)?\s+(?:ascending|asc)\s+(?:(?:batter|pitcher)\s+)?MLBAM(?:\s+ID)?', lambda m: part.update(ordering_tail=[dict(field=identity,direction='ASC')]) if identity else (_ for _ in ()).throw(ValueError('tie role unresolved')))
            consume(r'(?<![A-Za-z])(?:descending|ascending|desc|asc)(?![A-Za-z])|从高到低|從高到低|从低到高|從低到高',explicit_direction)
            role_cue = role_from_text(clause)
            if role_cue: part["analytical_role"] = role_cue
            residue = "".join(" " if any(a <= j < b for a,b in spans) else ch for j,ch in enumerate(clause)).strip(" ,.:，。")
            # Harmless connective grammar is finite; semantic words never disappear.
            residue = re.sub(_ACCOUNTING_CONNECTIVES, "", residue, flags=re.I).strip(" ,.:，。")
            # Governed Measures named in the clause are already structured (MEASURE
            # Requirements / requested measures). Removing their surface tokens here does
            # not waive any remaining modifier; it prevents a named metric from being
            # misread as unparsed residue.
            if residue:
                for measure in resolve_texts([residue]):
                    if measure.matched and measure.text:
                        residue = re.sub(re.escape(measure.text), " ", residue, flags=re.I)
                residue = re.sub(r"\s{2,}", " ", residue).strip(" ,.:，。")
            # Terms already represented by authoritative structured obligations (entity
            # names, season values, Measure labels, population terms, the analysis
            # objective itself) and finite multilingual domain/question phrases do not
            # need to be re-interpreted as residue. Year-like tokens are verified
            # separately by the authoritative temporal-scope check.
            if residue:
                # Longest surface first so a multi-word interpreter span that contains an
                # entity name is removed before the shorter entity term breaks it.
                for term in sorted(accounted_terms, key=lambda t: len(str(t or "")), reverse=True):
                    term = str(term or "").strip()
                    if not term:
                        continue
                    # ASCII terms use word boundaries so a short label ('pa') cannot
                    # corrupt an unrelated word ('southpaws'). CJK terms are matched
                    # as substrings because there are no ASCII word boundaries.
                    if all(ord(ch) < 128 for ch in term):
                        variants = {term, re.sub(_ACCOUNTING_CONNECTIVES, " ", term, flags=re.I)}
                        for variant in variants:
                            variant = re.sub(r"\s{2,}", " ", variant).strip()
                            if variant:
                                residue = re.sub(rf"(?<![A-Za-z0-9]){re.escape(variant)}(?![A-Za-z0-9])", " ", residue, flags=re.I)
                    else:
                        residue = re.sub(re.escape(term), " ", residue)
                for pattern in _DOMAIN_ACCOUNTED_PHRASES:
                    residue = re.sub(pattern, " ", residue)
                residue = re.sub(r"\s{2,}", " ", residue).strip(" ,.:，。")
            clause_state, detail, clause_attempts = UNDERSTOOD, "", []
            if spans:
                clause_attempts.append(dict(resolver='finite.clause_consumption',input=clause,outcome='consumed',
                    consumed_spans=[dict(start=a,end=b,text=clause[a:b]) for a,b in spans],
                    remaining=residue,obligations=dict(part)))
            if residue and residue_is_semantically_relevant(residue, role_cue=bool(role_cue)):
                # A factual modifier survived extraction. Try a bounded resolver chain and,
                # if it still cannot be structured, preserve it as temporarily unresolved.
                resolved_part, residual_attempts, state = _resolve_clause(residue, role=role)
                clause_attempts.extend(residual_attempts)
                if resolved_part:
                    for key, item in resolved_part.items():
                        if key in {"filters", "group_by", "ordering", "ordering_tail", "tie_break_ordering"}:
                            part.setdefault(key, [])
                            part[key].extend(x for x in item if x not in part[key])
                        else:
                            part.setdefault(key, item)
                if state != UNDERSTOOD:
                    remainder = next((a['remaining'] for a in reversed(clause_attempts) if 'remaining' in a), residue)
                    clause_state, detail = state, remainder
                    unresolved.append(remainder)
            elif residue:
                # Pure task/presentation language: an analytical objective, not a fact.
                recognized.append(dict(clause=residue, obligations={"analysis":"bound analytical objective"}))
            if len(part.get('filters', [])) > 1 and re.search(r'(?<![A-Za-z])or(?![A-Za-z])|或者|或',clause,re.I):
                clause_state, detail = TEMPORARILY_UNRESOLVED, 'unrepresented disjunction: '+clause
                unresolved.append(detail)
            if unsupported_local:
                clause_state = UNSUPPORTED
                detail = "; ".join(unsupported_local)
                unsupported.extend(unsupported_local)
            if part: recognized.append(dict(clause=clause, obligations=part)); merge(part)
            if clause_state == UNDERSTOOD and len(recognized) == recognized_start:
                # Metric-only clauses and fully accounted source terms are represented
                # by existing authoritative obligations, not silently omitted indices.
                recognized.append(dict(clause=clause,obligations={'analysis':'represented by governed metric or authoritative terms'}))
            for entry in recognized[recognized_start:]: entry["clause_index"] = index
            clauses_state.append(dict(clause=clause, clause_index=index, state=clause_state,
                consumed_spans=[dict(start=a,end=b,text=clause[a:b]) for a,b in spans],
                generated_obligations=part, remaining_spans=[detail] if detail else [],
                accounted_residue=residue if clause_state == UNDERSTOOD else '',
                detail=detail, attempts=clause_attempts))
    else:
        unresolved.append("missing mandatory analytical clause")
    blocking = bool(unresolved or unsupported)
    state = "PARTIAL" if blocking and recognized else "UNRESOLVED" if blocking else "COMPLETE"
    return dict(state=state, recognized=recognized, unresolved=unresolved, unsupported=unsupported,
                obligations=obligations, clause_states=clauses_state)


def constraint_obligations(value, role="BATTER", measure_context=""):
    coverage = constraint_coverage(value, role, measure_context)
    return coverage["obligations"] if coverage["state"] == "COMPLETE" and coverage["obligations"] else None
