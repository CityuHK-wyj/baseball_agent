"""Safe Analytical IR: a closed, validated representation of database analysis.

An LLM may draft this structure, but every identifier, operator, enum value and
aggregate is validated against the SchemaCatalog before compilation. Arbitrary SQL and
Python expressions are unrepresentable.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.capabilities.analytics.catalog import CATALOG, SchemaCatalog


class IRError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Condition(_Base):
    op: Literal["COMPARE", "BETWEEN", "IN", "IS_NULL", "IS_NOT_NULL", "AND", "OR", "NOT"]
    field: str | None = None
    comparator: Literal["=", "!=", ">", ">=", "<", "<="] | None = None
    value: Any = None
    low: Any = None
    high: Any = None
    values: list[Any] | None = None
    conditions: list["Condition"] | None = None


Condition.model_rebuild()


class EntityFilter(_Base):
    role: Literal["BATTER", "PITCHER"]
    ids: list[int]
    names: list[str] = Field(default_factory=list)

    @field_validator("ids", mode="before")
    @classmethod
    def integral_ids(cls,values):
        for value in values:
            if isinstance(value,bool): raise ValueError("boolean is not a canonical identity")
        return values


class Aggregate(_Base):
    name: str
    function: Literal["COUNT", "COUNT_NON_NULL", "COUNT_IF", "COUNT_DISTINCT", "AVG", "SUM", "MIN", "MAX", "ANY_VALUE"]
    field: str | None = None
    condition: Condition | None = None


class Operand(_Base):
    kind: Literal["aggregate", "derived", "literal", "field"]
    name: str | None = None
    value: Any = None


class Derived(_Base):
    name: str
    op: Literal["ADD", "SUB", "MUL", "DIV", "SAFE_DIV", "PCT", "DIFF", "RATE"]
    left: Operand
    right: Operand


class SortSpec(_Base):
    name: str
    direction: Literal["ASC", "DESC"] = "DESC"


class Qualification(_Base):
    basis: str = "PA"
    minimum: int = 1

    @field_validator("minimum",mode="before")
    @classmethod
    def integral_minimum(cls,value):
        if isinstance(value,bool): raise ValueError("boolean is not a qualification minimum")
        return value


class DateWindow(_Base):
    start: date | None = None
    end: date | None = None


class AnalyticalQuery(_Base):
    relation: str = "pitch"
    analytical_role: Literal["BATTER", "PITCHER"] | None = None
    entity_filters: list[EntityFilter] = Field(default_factory=list)
    filters: list[Condition] = Field(default_factory=list)
    population: str | None = None
    game_types: list[str] = Field(default_factory=list)
    season: int | None = None
    date_range: DateWindow | None = None
    group_by: list[str] = Field(default_factory=list)
    aggregates: list[Aggregate] = Field(default_factory=list)
    derived: list[Derived] = Field(default_factory=list)
    sort: list[SortSpec] = Field(default_factory=list)
    limit: int | None = None
    qualification: Qualification | None = None
    intent_summary: str = ""

    @field_validator("season","limit",mode="before")
    @classmethod
    def integral_scope(cls,value):
        if isinstance(value,bool): raise ValueError("boolean is not an integral analytical scope")
        return value


_AGG_FUNCS = {"COUNT", "COUNT_NON_NULL", "COUNT_IF", "COUNT_DISTINCT", "AVG", "SUM", "MIN", "MAX", "ANY_VALUE"}
_DERIVED_OPS = {"ADD", "SUB", "MUL", "DIV", "SAFE_DIV", "PCT", "DIFF", "RATE"}
_COMPARATORS = {"=", "!=", ">", ">=", "<", "<="}
_MAX_LIMIT = 500


def _check_field(catalog: SchemaCatalog, table: str, field: str, op: str) -> None:
    try:
        spec = catalog.field(table, field)
    except Exception as exc:  # noqa: BLE001
        raise IRError("UNKNOWN_FIELD", f"field '{field}' is not in the SchemaCatalog") from exc
    if op in {"COUNT", "COUNT_NON_NULL", "COUNT_DISTINCT", "COUNT_IF"}:
        # Counting rows where a column is present/absent is valid for any real column.
        return
    if op not in spec.ops:
        raise IRError("UNSUPPORTED_OPERATION", f"field '{field}' does not allow {op}")


def canonical_value(spec, value, field):
    """Governed type/domain normalization shared by IR and finite predicates."""
    if value is None: return None
    if spec.type in {"int", "float"}:
        if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value):
            raise IRError("UNSUPPORTED_OPERATION", f"field {field} requires a finite numeric value")
        if spec.type == "int" and (int(value) != value or isinstance(value,float) and abs(value) > 2**53):
            raise IRError("UNSUPPORTED_OPERATION", f"field {field} requires an exact integral value")
        if spec.minimum is not None and value < spec.minimum or spec.maximum is not None and value > spec.maximum:
            raise IRError("UNSUPPORTED_OPERATION", f"field {field} value is outside the governed domain")
        return int(value) if int(value) == value else value
    if spec.type == "date":
        try: return date.fromisoformat(str(value)).isoformat()
        except ValueError as error: raise IRError("UNSUPPORTED_OPERATION", f"field {field} requires an ISO date") from error
    if not isinstance(value,str) or spec.enum_values and value not in spec.enum_values:
        raise IRError("UNSUPPORTED_OPERATION", f"value {value!r} is not valid for {field}")
    return value


def _check_value(spec, value: Any, field: str) -> None:
    canonical_value(spec,value,field)


def _validate_condition(catalog: SchemaCatalog, table: str, condition: Condition) -> None:
    if condition.op in {"AND", "OR"}:
        if not condition.conditions:
            raise IRError("INVALID_IR", f"{condition.op} requires nested conditions")
        for nested in condition.conditions:
            _validate_condition(catalog, table, nested)
        return
    if condition.op == "NOT":
        if not condition.conditions:
            raise IRError("INVALID_IR", "NOT requires a nested condition")
        for nested in condition.conditions:
            _validate_condition(catalog, table, nested)
        return
    if not condition.field:
        raise IRError("INVALID_IR", f"{condition.op} requires a field")
    _check_field(catalog, table, condition.field, "FILTER")
    spec = catalog.field(table, condition.field)
    if condition.op == "COMPARE":
        if condition.comparator not in _COMPARATORS:
            raise IRError("INVALID_IR", "COMPARE requires a comparator")
        _check_value(spec, condition.value, condition.field)
    elif condition.op == "BETWEEN":
        if condition.low is None or condition.high is None:
            raise IRError("INVALID_IR", "BETWEEN requires low and high")
        _check_value(spec, condition.low, condition.field)
        _check_value(spec, condition.high, condition.field)
    elif condition.op == "IN":
        if not condition.values:
            raise IRError("INVALID_IR", "IN requires values")
        for value in condition.values:
            _check_value(spec, value, condition.field)
    elif condition.op in {"IS_NULL", "IS_NOT_NULL"}:
        pass
    else:
        raise IRError("INVALID_IR", f"unsupported condition op '{condition.op}'")


def validate_query(query: AnalyticalQuery, catalog: SchemaCatalog | None = None, *, comparison_plan: bool = False) -> AnalyticalQuery:
    """Validate the IR against the catalog. Returns the query or raises IRError."""
    catalog = catalog or CATALOG
    try:
        table_spec = catalog.table(query.relation)
    except Exception as exc:  # noqa: BLE001
        raise IRError("UNKNOWN_FIELD", f"unknown relation '{query.relation}'") from exc

    if not query.aggregates and not query.group_by:
        raise IRError("INVALID_IR", "an analytical query needs at least one aggregate or group_by field")

    for filt in query.entity_filters:
        if filt.role == "BATTER":
            _check_field(catalog, query.relation, "batter_id", "FILTER")
        else:
            _check_field(catalog, query.relation, "pitcher_id", "FILTER")
        if not filt.ids or not all(isinstance(i, int) for i in filt.ids):
            raise IRError("INVALID_IR", "entity filters require integer ids")

    if query.season is not None: _check_value(catalog.field(query.relation,"season"),query.season,"season")
    for binding in query.entity_filters:
        field="batter_id" if binding.role == "BATTER" else "pitcher_id"
        for value in binding.ids: _check_value(catalog.field(query.relation,field),value,field)

    if query.date_range and query.date_range.start and query.date_range.end and query.date_range.start > query.date_range.end:
        raise IRError("INVALID_IR", "date_range start must not exceed end")

    if query.population:
        codes = catalog.resolve_population(query.population)
        if not codes:
            raise IRError("UNSUPPORTED_OPERATION", f"unknown population '{query.population}'")

    for code in query.game_types:
        if code not in table_spec.fields["game_type"].enum_values:
            raise IRError("UNSUPPORTED_OPERATION", f"invalid game_type '{code}'")

    for field in query.group_by:
        _check_field(catalog, query.relation, field, "GROUP_BY")

    for condition in query.filters:
        _validate_condition(catalog, query.relation, condition)

    names: set[str] = set()
    for agg in query.aggregates:
        if agg.function not in _AGG_FUNCS:
            raise IRError("UNSUPPORTED_OPERATION", f"unsupported aggregate '{agg.function}'")
        if agg.function == "COUNT_IF":
            if agg.condition is None:
                raise IRError("INVALID_IR", "COUNT_IF requires a condition")
            _validate_condition(catalog, query.relation, agg.condition)
            if agg.field not in (None, "*"):
                _check_field(catalog, query.relation, agg.field, "COUNT_IF")
        elif agg.function in {"COUNT", "COUNT_NON_NULL", "COUNT_DISTINCT"}:
            if agg.field not in (None, "*") and agg.field is not None:
                _check_field(catalog, query.relation, agg.field, "COUNT_NON_NULL" if agg.function != "COUNT_DISTINCT" else "COUNT_DISTINCT")
        else:
            if agg.field in (None, "*"):
                raise IRError("INVALID_IR", f"{agg.function} requires a field")
            _check_field(catalog, query.relation, agg.field, "AGG")
        if agg.name in names:
            raise IRError("INVALID_IR", f"duplicate output name '{agg.name}'")
        names.add(agg.name)

    for derived in query.derived:
        if derived.op not in _DERIVED_OPS:
            raise IRError("UNSUPPORTED_OPERATION", f"unsupported derived op '{derived.op}'")
        for operand in (derived.left, derived.right):
            if operand.kind == "aggregate" and operand.name not in {a.name for a in query.aggregates}:
                raise IRError("INVALID_IR", f"derived '{derived.name}' references unknown aggregate '{operand.name}'")
            if operand.kind == "derived" and operand.name not in {d.name for d in query.derived}:
                raise IRError("INVALID_IR", f"derived '{derived.name}' references unknown derived '{operand.name}'")
            if operand.kind == "field":
                _check_field(catalog, query.relation, operand.name or "", "DERIVE")
            if operand.kind == "literal" and not isinstance(operand.value, (int, float)):
                raise IRError("INVALID_IR", "literal operands must be numeric")
        if derived.name in names:
            raise IRError("INVALID_IR", f"duplicate output name '{derived.name}'")
        names.add(derived.name)

    _check_derived_cycles(query)

    valid_names = names | set(query.group_by)
    for sort in query.sort:
        if sort.name not in valid_names:
            raise IRError("INVALID_IR", f"sort references unknown output '{sort.name}'")

    if query.qualification:
        if query.qualification.basis not in catalog.table(query.relation).fields and query.qualification.basis not in {
            "PA", "BBE", "PITCHES", "GAMES", "RESULTS"
        }:
            raise IRError("INVALID_IR", f"unknown qualification basis '{query.qualification.basis}'")
        if query.qualification.minimum < 0:
            raise IRError("INVALID_IR", "qualification minimum must be >= 0")

    max_limit = 5000 if comparison_plan else _MAX_LIMIT
    if query.limit is not None and (query.limit < 1 or query.limit > max_limit):
        raise IRError("INVALID_IR", f"limit must be between 1 and {max_limit}")

    return query


def _check_derived_cycles(query: AnalyticalQuery) -> None:
    deps: dict[str, set[str]] = {d.name: set() for d in query.derived}
    for derived in query.derived:
        for operand in (derived.left, derived.right):
            if operand.kind == "derived" and operand.name in deps:
                deps[derived.name].add(operand.name)
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(name: str) -> None:
        if name in done:
            return
        if name in visiting:
            raise IRError("INVALID_IR", f"derived expression cycle involving '{name}'")
        visiting.add(name)
        for dep in deps.get(name, set()):
            visit(dep)
        visiting.discard(name)
        done.add(name)

    for name in deps:
        visit(name)


def intent_to_prompt(intent: dict[str, Any]) -> str:
    """Render an open analytical intent for the IR builder prompt (no SQL)."""
    lines = [f"Objective: {intent.get('objective', '')}"]
    if intent.get("measure"):
        lines.append(f"Requested measures: {intent['measure']}")
    if intent.get("population"):
        lines.append(f"Population: {intent['population']}")
    if intent.get("season"):
        lines.append(f"Season: {intent['season']}")
    if intent.get("date_range"):
        lines.append(f"Date range: {intent['date_range']}")
    if intent.get("entities"):
        lines.append(f"Entities: {intent['entities']}")
    if intent.get("notes"):
        lines.append(f"Notes: {intent['notes']}")
    return "\n".join(lines)
