"""Deterministic compiler: Safe Analytical IR -> SQL.

Every identifier is validated against the trusted SchemaCatalog before it reaches SQL.
Values are rendered as validated literals (numbers, quoted/escaped strings, date
literals); the IR cannot express a raw SQL fragment. The resulting statement still
passes the AST read-only guard at execution time (defense in depth).

A compilation failure returns a structured recovery code so the planner can re-plan
instead of silently dropping the unsupported portion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable

from app.models.artifact_runtime import ArtifactExport
from app.artifact_runtime.analytical_ir import (Aggregate, AnalyticalQuery, And, Between, Compare,
                                       Condition, InSet, Not, NullCheck, Or, Selection)
from app.artifact_runtime.schema_catalog import SOURCE_COVERAGE, SchemaCatalog

_NUMERIC_TYPES = frozenset({"DOUBLE", "FLOAT", "INTEGER", "BIGINT", "NUMERIC", "REAL"})
_MAX_ENTITY_SET = 5000
_MAX_LIMIT = 1000
_MAX_IN_VALUES = 500


@dataclass(frozen=True)
class IRCompilationResult:
    sql: str = ""
    code: str = ""
    detail: str = ""
    applied_fields: tuple[str, ...] = field(default_factory=tuple)
    referenced_exports: tuple[str, ...] = field(default_factory=tuple)
    applied_window: tuple[str, str] | None = None
    game_types: tuple[str, ...] = field(default_factory=tuple)
    ir_digest: str = ""
    coverage_status: str = "FULL"  # FULL | PARTIAL | NONE
    qualification: str = ""
    aggregation: str = ""
    select_count: int = 0

    @property
    def ok(self) -> bool:
        return bool(self.sql) and not self.code


def _quote_text(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _render_literal(field_type: str, value: Any) -> str | None:
    if value is None:
        return None
    if field_type == "DATE":
        if isinstance(value, datetime):
            return f"DATE '{value.date().isoformat()}'"
        if isinstance(value, date):
            return f"DATE '{value.isoformat()}'"
        if isinstance(value, str):
            try:
                return f"DATE '{date.fromisoformat(value).isoformat()}'"
            except ValueError:
                return None
        return None
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            return None
        return repr(int(number)) if number.is_integer() and field_type != "DOUBLE" \
            else repr(number)
    if isinstance(value, str):
        return _quote_text(value)
    return None


class _Compiler:
    def __init__(self, query: AnalyticalQuery, catalog: SchemaCatalog, *,
                 resolve_export: Callable[[str], ArtifactExport | None] | None,
                 relation_sql: str | None) -> None:
        self.query = query
        self.catalog = catalog
        self.resolve_export = resolve_export
        self.relation_sql = relation_sql
        self.applied: list[str] = []
        self.used_exports: list[str] = []
        self.aggregate_sql: dict[str, str] = {}

    # -- helpers -----------------------------------------------------------
    def fail(self, code: str, detail: str) -> IRCompilationResult:
        return IRCompilationResult(code=code, detail=detail)

    def field(self, name: str):
        # Controlled semantic resolution: a planner may name a catalog field directly or a
        # canonical semantic handle (for example ``exit_velocity``); only a trusted
        # catalog field survives and the physical name is what reaches SQL. Unknown names
        # still fail with UNKNOWN_FIELD.
        from app.artifact_runtime.field_resolver import resolve_field
        spec = resolve_field(self.catalog, self.query.source_kind, self.query.table, name)
        if spec is None:
            return None
        self.applied.append(spec.name)
        return spec

    @staticmethod
    def _allowed(spec, operation: str) -> bool:
        # Catalog allowed-operations are advisory metadata only; enforce them so an
        # accepted field/operation pair cannot be silently unsupported.
        if not spec.allowed_operations:
            return True
        if operation in spec.allowed_operations:
            return True
        # IN / null checks apply to any comparable field.
        if operation in ("IN", "IS_NULL", "NOT_NULL", "BETWEEN") and spec.role in (
                "DIMENSION", "POPULATION", "DATE", "MEASURE", "IDENTIFIER"):
            return operation not in ("BETWEEN",) or spec.role in ("DATE", "MEASURE")
        return False

    def _check_op(self, spec, operation: str) -> IRCompilationResult | None:
        if self._allowed(spec, operation):
            return None
        return self.fail("UNSUPPORTED_OPERATION",
                         f"operation {operation} is not allowed for field {spec.name!r} "
                         f"(role {spec.role})")

    def render_condition(self, condition: Condition) -> tuple[str | None, IRCompilationResult | None]:
        if isinstance(condition, Compare):
            spec = self.field(condition.field)
            if spec is None:
                return None, self.fail("UNKNOWN_FIELD", f"unknown field {condition.field!r}")
            error = self._check_op(spec, condition.operator)
            if error is not None:
                return None, error
            operator = {"EQ": "=", "NE": "<>", "GT": ">", "GTE": ">=",
                        "LT": "<", "LTE": "<="}[condition.operator]
            value = _render_literal(spec.data_type, condition.value)
            if value is None:
                return None, self.fail("UNSUPPORTED_OPERATION",
                                       f"unrenderable value for {condition.field!r}")
            return f"{spec.name} {operator} {value}", None
        if isinstance(condition, Between):
            spec = self.field(condition.field)
            if spec is None:
                return None, self.fail("UNKNOWN_FIELD", f"unknown field {condition.field!r}")
            error = self._check_op(spec, "BETWEEN")
            if error is not None:
                return None, error
            low = _render_literal(spec.data_type, condition.low)
            high = _render_literal(spec.data_type, condition.high)
            if low is None or high is None:
                return None, self.fail("UNSUPPORTED_OPERATION",
                                       f"unrenderable range for {condition.field!r}")
            return f"{spec.name} BETWEEN {low} AND {high}", None
        if isinstance(condition, InSet):
            spec = self.field(condition.field)
            if spec is None:
                return None, self.fail("UNKNOWN_FIELD", f"unknown field {condition.field!r}")
            error = self._check_op(spec, "IN")
            if error is not None:
                return None, error
            if not condition.values or len(condition.values) > _MAX_IN_VALUES:
                return None, self.fail("UNSUPPORTED_OPERATION",
                                       f"IN set for {condition.field!r} has invalid size")
            rendered = [_render_literal(spec.data_type, item) for item in condition.values]
            if any(item is None for item in rendered):
                return None, self.fail("UNSUPPORTED_OPERATION",
                                       f"unrenderable IN value for {condition.field!r}")
            return f"{spec.name} IN (" + ", ".join(rendered) + ")", None
        if isinstance(condition, NullCheck):
            spec = self.field(condition.field)
            if spec is None:
                return None, self.fail("UNKNOWN_FIELD", f"unknown field {condition.field!r}")
            error = self._check_op(spec, "NOT_NULL" if condition.negate else "IS_NULL")
            if error is not None:
                return None, error
            return f"{spec.name} IS {'NOT ' if condition.negate else ''}NULL", None
        if isinstance(condition, (And, Or)):
            parts: list[str] = []
            for child in condition.conditions:
                rendered, error = self.render_condition(child)
                if error is not None:
                    return None, error
                parts.append(f"({rendered})")
            joiner = " AND " if isinstance(condition, And) else " OR "
            return joiner.join(parts) if parts else "1 = 1", None
        if isinstance(condition, Not):
            rendered, error = self.render_condition(condition.condition)
            if error is not None:
                return None, error
            return f"NOT ({rendered})", None
        return None, self.fail("INVALID_IR", "unknown condition kind")

    def render_aggregate(self, aggregate: Aggregate) -> tuple[str | None, IRCompilationResult | None]:
        op = aggregate.op
        condition_sql = None
        if aggregate.condition is not None:
            condition_sql, error = self.render_condition(aggregate.condition)
            if error is not None:
                return None, error

        period_sql = None
        if aggregate.period:
            period = next((item for item in self.query.periods
                           if item.label == aggregate.period), None)
            if period is None:
                return None, self.fail("INVALID_IR",
                                       f"unknown period {aggregate.period!r}")
            date_spec = self.field(self.query.date_field)
            if date_spec is None:
                return None, self.fail("UNKNOWN_FIELD",
                                       f"unknown date field {self.query.date_field!r}")
            period_sql = (f"{date_spec.name} >= DATE '{period.time_range.start.isoformat()}' "
                          f"AND {date_spec.name} <= DATE '{period.time_range.end.isoformat()}'")

        predicates = [item for item in (condition_sql, period_sql) if item]
        if len(predicates) == 1:
            guard = predicates[0]
        elif predicates:
            guard = " AND ".join(f"({item})" for item in predicates)
        else:
            guard = None

        # Every accepted condition/period combination has numerical semantics. COUNT is
        # row-based only when unconditional; otherwise it counts qualifying rows instead
        # of silently dropping the restriction.
        if op == "COUNT":
            if guard:
                return f"SUM(CASE WHEN {guard} THEN 1 ELSE 0 END)", None
            return "COUNT(*)", None
        if op == "COUNT_IF":
            if guard is None:
                return None, self.fail("INVALID_IR", "COUNT_IF requires a condition")
            return f"SUM(CASE WHEN {guard} THEN 1 ELSE 0 END)", None

        spec = self.field(aggregate.field)
        if spec is None:
            return None, self.fail("UNKNOWN_FIELD", f"unknown field {aggregate.field!r}")
        error = self._check_op(spec, op)
        if error is not None:
            return None, error
        if op in ("AVG", "SUM") and spec.data_type not in _NUMERIC_TYPES:
            return None, self.fail("UNSUPPORTED_OPERATION",
                                   f"{op} requires a numeric field; {aggregate.field!r} is "
                                   f"{spec.data_type}")
        if op in ("MIN", "MAX") and spec.data_type not in _NUMERIC_TYPES | {"DATE"}:
            return None, self.fail("UNSUPPORTED_OPERATION",
                                   f"{op} is unsupported for {aggregate.field!r}")
        target = f"CASE WHEN {guard} THEN {spec.name} END" if guard else spec.name
        if op == "COUNT_NON_NULL":
            return f"COUNT({target})", None
        return f"{op}({target})", None

    def render_derived(self, expression) -> tuple[str | None, IRCompilationResult | None]:
        from app.artifact_runtime.analytical_ir import (AggregateRef, BinaryOperand, NumberOperand)
        if isinstance(expression, AggregateRef):
            if expression.alias not in self.aggregate_sql:
                return None, self.fail("INVALID_IR",
                                       f"derived expression references unknown aggregate "
                                       f"{expression.alias!r}")
            return self.aggregate_sql[expression.alias], None
        if isinstance(expression, NumberOperand):
            return repr(float(expression.value)), None
        if isinstance(expression, BinaryOperand):
            left, error = self.render_derived(expression.left)
            if error is not None:
                return None, error
            right, error = self.render_derived(expression.right)
            if error is not None:
                return None, error
            op = expression.op
            if op == "ADD":
                return f"({left} + {right})", None
            if op == "SUB":
                return f"({left} - {right})", None
            if op == "MUL":
                return f"({left} * {right})", None
            if op == "DIFF":
                return f"({left} - {right})", None
            if op in ("DIV", "SAFE_DIV", "RATE"):
                return f"(CAST({left} AS DOUBLE) / NULLIF({right}, 0))", None
            if op == "PCT":
                return f"(100.0 * CAST({left} AS DOUBLE) / NULLIF({right}, 0))", None
            return None, self.fail("UNSUPPORTED_OPERATION", f"unknown binary op {op!r}")
        return None, self.fail("INVALID_IR", "unknown derived expression")

    def entity_clause(self) -> tuple[str | None, IRCompilationResult | None]:
        if self.query.entity_set is None:
            return None, None
        spec = self.field(self.query.entity_set.field)
        if spec is None:
            return None, self.fail("UNKNOWN_FIELD",
                                   f"unknown entity field {self.query.entity_set.field!r}")
        if spec.role != "IDENTIFIER":
            return None, self.fail("UNSUPPORTED_OPERATION",
                                   f"{spec.name} is not an identifier field")
        if self.resolve_export is None:
            return None, self.fail("MISSING_ENTITY_SET", "no export resolver is available")
        export = self.resolve_export(self.query.entity_set.export_ref)
        if export is None:
            return None, self.fail("MISSING_ENTITY_SET",
                                   f"export {self.query.entity_set.export_ref!r} is unavailable")
        ids = _extract_ids(export)
        if not ids:
            return None, self.fail("MISSING_ENTITY_SET",
                                   f"export {export.export_type!r} has no usable ids")
        if len(ids) > _MAX_ENTITY_SET:
            return None, self.fail("ENTITY_SET_TOO_LARGE",
                                   f"entity set has {len(ids)} ids")
        self.used_exports.append(export.export_id)
        return f"{spec.name} IN (" + ", ".join(str(item) for item in ids) + ")", None

    def coverage_error(self) -> IRCompilationResult | None:
        entry = SOURCE_COVERAGE.get(self.query.source_kind)
        if entry is None or self.query.window is None:
            return None
        _name, (start, end) = entry
        coverage = (date.fromisoformat(start), date.fromisoformat(end))
        window = self.query.window
        if window.end < coverage[0] or window.start > coverage[1]:
            return self.fail(
                "INSUFFICIENT_SOURCE_COVERAGE",
                f"{self.query.source_kind} covers {start}..{end}, "
                f"requested {window.start}..{window.end}")
        return None

    def _coverage_status(self) -> str:
        entry = SOURCE_COVERAGE.get(self.query.source_kind)
        if entry is None or self.query.window is None:
            return "FULL"
        _name, (start, end) = entry
        coverage = (date.fromisoformat(start), date.fromisoformat(end))
        window = self.query.window
        if coverage[0] <= window.start and window.end <= coverage[1]:
            return "FULL"
        return "PARTIAL"

    def _qualification_denominator(self) -> str:
        """The HAVING denominator qualifies the intended sample, not raw row count.

        For "average X over at least N measured events", the denominator is the count of
        non-null measured events (then conditional counts, then rows), never total rows.
        """
        preference = {"COUNT_NON_NULL": 0, "COUNT_IF": 1, "COUNT": 2}
        best: tuple[int, str] | None = None
        for selection in self.query.selections:
            if selection.kind != "AGGREGATE" or selection.aggregate is None:
                continue
            rank = preference.get(selection.aggregate.op)
            if rank is None:
                continue
            sql = self.aggregate_sql.get(selection.alias)
            if not sql:
                continue
            if best is None or rank < best[0]:
                best = (rank, sql)
        return best[1] if best is not None else "COUNT(*)"

    def _collect_game_types(self) -> tuple[str, ...]:
        values: list[str] = []

        def walk(condition) -> None:
            field = getattr(condition, "field", "")
            if field == "game_type":
                if getattr(condition, "kind", "") == "IN":
                    values.extend(str(item) for item in condition.values)
                else:
                    value = getattr(condition, "value", None)
                    if value is not None:
                        values.append(str(value))
            for child in getattr(condition, "conditions", ()) or ():
                walk(child)
            inner = getattr(condition, "condition", None)
            if inner is not None:
                walk(inner)

        for condition in self.query.filters:
            walk(condition)
        return tuple(dict.fromkeys(values))

    def _compile_qualification(self) -> tuple[str, IRCompilationResult | None]:
        """Translate a typed qualification into a deterministic HAVING predicate.

        The denominator is the *measured* sample the semantics require, never a generic
        row count. Unsupported bases/fields are rejected rather than approximated.
        """
        spec = self.query.qualification
        if spec is None:
            if self.query.min_rows is None:
                return "", None
            if self.query.min_rows < 0:
                return "", self.fail("INVALID_IR", "min_rows must be non-negative")
            return f"{self._qualification_denominator()} >= {int(self.query.min_rows)}", None
        if spec.minimum < 0:
            return "", self.fail("INVALID_IR", "qualification minimum must be non-negative")
        minimum = int(spec.minimum)
        if spec.basis == "MEASURED":
            if not spec.field:
                return "", self.fail("INVALID_IR", "MEASURED qualification needs a field")
            field_spec = self.field(spec.field)
            if field_spec is None:
                return "", self.fail("UNKNOWN_FIELD",
                                     f"unknown qualification field {spec.field!r}")
            if field_spec.role not in ("MEASURE", "DATE", "IDENTIFIER"):
                return "", self.fail(
                    "UNSUPPORTED_OPERATION",
                    f"{spec.field!r} (role {field_spec.role}) is not a measurable denominator")
            return f"COUNT({field_spec.name}) >= {minimum}", None
        if spec.basis == "GAMES":
            field_spec = self.field("game_pk")
            if field_spec is None:
                return "", self.fail("UNSUPPORTED_OPERATION",
                                     "GAMES qualification needs a game_pk field")
            return f"COUNT(DISTINCT {field_spec.name}) >= {minimum}", None
        if spec.basis in ("ROWS", "EVENTS"):
            return f"COUNT(*) >= {minimum}", None
        if spec.basis == "ENTITIES_PER_GROUP":
            return f"{self._qualification_denominator()} >= {minimum}", None
        return "", self.fail("UNSUPPORTED_OPERATION",
                             f"unknown qualification basis {spec.basis!r}")

    def compile(self) -> IRCompilationResult:
        if self.catalog.table(self.query.source_kind, self.query.table) is None:
            return self.fail("UNKNOWN_TABLE",
                             f"{self.query.table!r} is not in the {self.query.source_kind} catalog")
        limit = self.query.limit
        if limit is not None and (limit < 1 or limit > _MAX_LIMIT):
            return self.fail("INVALID_LIMIT", f"limit {limit} is out of range")
        if self.query.periods and not self.query.date_field:
            return self.fail("INVALID_IR", "period selectors require a date field")
        coverage = self.coverage_error()
        if coverage is not None:
            return coverage

        # First pass: register aggregate SQL so derived expressions can reference aliases.
        aggregates: list[tuple[Selection, Aggregate]] = []
        for selection in self.query.selections:
            if selection.kind == "AGGREGATE":
                if selection.aggregate is None or selection.aggregate.alias != selection.alias:
                    return self.fail("INVALID_IR",
                                     f"aggregate selection {selection.alias!r} is malformed")
                aggregates.append((selection, selection.aggregate))
        for _selection, aggregate in aggregates:
            sql, error = self.render_aggregate(aggregate)
            if error is not None:
                return error
            self.aggregate_sql[aggregate.alias] = sql

        select_items: list[str] = []
        group_keys: list[str] = []
        import re
        from app.artifact_runtime.analytical_ir import IDENTIFIER_PATTERN
        for selection in self.query.selections:
            if not re.match(IDENTIFIER_PATTERN, selection.alias):
                # The model validates this; re-check so an in-process construction can
                # never smuggle SQL structure through an identifier slot.
                return self.fail("INVALID_IR",
                                 f"alias {selection.alias!r} is not a valid identifier")
            if selection.kind == "GROUP_KEY":
                spec = self.field(selection.field)
                if spec is None:
                    return self.fail("UNKNOWN_FIELD", f"unknown group field {selection.field!r}")
                select_items.append(f"{spec.name} AS {selection.alias}")
                group_keys.append(spec.name)
            elif selection.kind == "AGGREGATE":
                select_items.append(f"{self.aggregate_sql[selection.alias]} AS {selection.alias}")
            elif selection.kind == "DERIVED":
                if selection.expression is None:
                    return self.fail("INVALID_IR", f"derived {selection.alias!r} has no expression")
                sql, error = self.render_derived(selection.expression)
                if error is not None:
                    return error
                select_items.append(f"{sql} AS {selection.alias}")
            else:
                return self.fail("INVALID_IR", f"unknown selection kind {selection.kind!r}")

        if not any(item.kind != "GROUP_KEY" for item in self.query.selections):
            # A grouping without aggregates is not a useful analytical result.
            return self.fail("INVALID_IR", "query has no aggregate or derived selection")

        where_parts: list[str] = []
        for condition in self.query.filters:
            rendered, error = self.render_condition(condition)
            if error is not None:
                return error
            where_parts.append(f"({rendered})")
        entity_sql, error = self.entity_clause()
        if error is not None:
            return error
        if entity_sql is not None:
            where_parts.append(f"({entity_sql})")

        # The declared window is compiled into an executed predicate, so declared and
        # executed window semantics can never diverge.
        applied_window = None
        if self.query.window is not None:
            date_name = self.query.date_field or "game_date"
            date_spec = self.field(date_name)
            if date_spec is None:
                return self.fail("UNKNOWN_FIELD", f"unknown date field {date_name!r}")
            start = _render_literal(date_spec.data_type, self.query.window.start)
            end = _render_literal(date_spec.data_type, self.query.window.end)
            if start is None or end is None:
                return self.fail("INVALID_IR", "window bounds are not renderable dates")
            where_parts.append(f"({date_spec.name} BETWEEN {start} AND {end})")
            applied_window = (self.query.window.start.isoformat(),
                              self.query.window.end.isoformat())

        relation = self.relation_sql or self.query.table
        sql = f"SELECT {', '.join(select_items)} FROM {relation}"
        if where_parts:
            sql += " WHERE " + " AND ".join(where_parts)
        if group_keys:
            sql += " GROUP BY " + ", ".join(group_keys)
        qualification = ""
        qualification_sql, error = self._compile_qualification()
        if error is not None:
            return error
        if qualification_sql:
            qualification = qualification_sql
            sql += f" HAVING {qualification}"
        order_alias = self.query.order_by or (self.query.selections[-1].alias)
        if order_alias not in self.query.alias_names():
            return self.fail("INVALID_IR", f"order_by {order_alias!r} is not a selected alias")
        sql += f" ORDER BY {order_alias} {self.query.direction}"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        aggregation = ",".join(dict.fromkeys(
            item.aggregate.op for item in self.query.selections
            if item.kind == "AGGREGATE" and item.aggregate is not None))
        import hashlib
        import json
        digest = hashlib.sha256(json.dumps(
            self.query.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()[:16]
        return IRCompilationResult(
            sql=sql, applied_fields=tuple(dict.fromkeys(self.applied)),
            referenced_exports=tuple(dict.fromkeys(self.used_exports)),
            applied_window=applied_window, game_types=self._collect_game_types(),
            ir_digest=digest, coverage_status=self._coverage_status(),
            qualification=qualification, aggregation=aggregation,
            select_count=len(select_items))


def _extract_ids(export: ArtifactExport) -> list[int]:
    value = export.value
    raw: list[Any] = []
    if isinstance(value, dict):
        for key in ("player_ids", "ids", "player_id_set", "values"):
            if key in value:
                raw = list(value[key]) if isinstance(value[key], (list, tuple)) else []
                break
    elif isinstance(value, (list, tuple)):
        raw = list(value)
    elif isinstance(value, (int, str)) and str(value).isdigit():
        raw = [value]
    ids: list[int] = []
    for item in raw:
        candidate = item
        if isinstance(item, dict):
            candidate = item.get("player_id") or item.get("id") or item.get("mlbam_id")
        text = str(candidate)
        if text.isdigit():
            ids.append(int(text))
    return list(dict.fromkeys(ids))


def compile_analytical_query(
        query: AnalyticalQuery, catalog: SchemaCatalog, *,
        resolve_export: Callable[[str], ArtifactExport | None] | None = None,
        relation_sql: str | None = None) -> IRCompilationResult:
    return _Compiler(query, catalog, resolve_export=resolve_export,
                     relation_sql=relation_sql).compile()
