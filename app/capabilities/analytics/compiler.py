"""Deterministic SQL compiler for the Safe Analytical IR.

Only catalog-validated identifiers reach this module. Output is a single read-only
SELECT. Literals are numbers, enum strings validated against the catalog, or ISO dates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.capabilities.analytics.catalog import CATALOG, QUALIFICATION_BASES, SOURCE_PARQUET, SOURCE_POSTGRES, SchemaCatalog
from app.capabilities.analytics.ir import AnalyticalQuery, Condition, IRError

_IDENTIFIER = re.compile(r"[^0-9a-zA-Z_]")


class _LazyDerived(dict):
    """Mapping-like resolver so derived operands resolve recursively and order-independently."""

    def __init__(self, resolver):
        super().__init__()
        self._resolver = resolver

    def __contains__(self, key):  # type: ignore[override]
        return True

    def __getitem__(self, key):  # type: ignore[override]
        return self._resolver(key)


def sanitize(name: str) -> str:
    cleaned = _IDENTIFIER.sub("_", name or "")
    if not cleaned:
        cleaned = "value"
    if cleaned[0].isdigit():
        cleaned = "c_" + cleaned
    return cleaned[:48]


def render_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, date):
        return f"'{value.isoformat()}'"
    text = str(value).replace("'", "''")
    return f"'{text}'"


@dataclass
class CompiledQuery:
    sql: str
    source: str
    table: str
    applied_fields: list[str]
    output_columns: list[str]
    group_columns: list[str]


class SQLCompiler:
    def __init__(self, catalog: SchemaCatalog | None = None, *, parquet_archive: str = ""):
        self.catalog = catalog or CATALOG
        self.parquet_archive = parquet_archive

    def _physical(self, source: str, table: str, logical: str) -> str:
        if logical == "season":
            return "EXTRACT(YEAR FROM game_date)"
        column = self.catalog.physical_column(source, table, logical)
        if not column:
            raise IRError("UNKNOWN_FIELD", f"field '{logical}' is not available on {source}")
        return f'"{column}"'

    def _condition(self, source: str, table: str, condition: Condition) -> str:
        if condition.op == "AND":
            return "(" + " AND ".join(self._condition(source, table, c) for c in (condition.conditions or [])) + ")"
        if condition.op == "OR":
            return "(" + " OR ".join(self._condition(source, table, c) for c in (condition.conditions or [])) + ")"
        if condition.op == "NOT":
            inner = condition.conditions or []
            return "(NOT (" + " AND ".join(self._condition(source, table, c) for c in inner) + "))"
        column = self._physical(source, table, condition.field or "")
        if condition.op == "COMPARE":
            return f"{column} {condition.comparator} {render_literal(condition.value)}"
        if condition.op == "BETWEEN":
            return f"{column} BETWEEN {render_literal(condition.low)} AND {render_literal(condition.high)}"
        if condition.op == "IN":
            values = ", ".join(render_literal(v) for v in (condition.values or []))
            return f"{column} IN ({values})"
        if condition.op == "IS_NULL":
            return f"{column} IS NULL"
        if condition.op == "IS_NOT_NULL":
            return f"{column} IS NOT NULL"
        raise IRError("INVALID_IR", f"cannot compile condition '{condition.op}'")

    def _aggregate_expr(self, source: str, table: str, agg) -> tuple[str, str]:
        field = agg.field
        if agg.function == "COUNT" and field in (None, "*"):
            return "COUNT(*)", agg.name
        if agg.function == "COUNT_IF":
            cond = self._condition(source, table, agg.condition)
            return f"SUM(CASE WHEN {cond} THEN 1 ELSE 0 END)", agg.name
        column = self._physical(source, table, field or "")
        if agg.function == "COUNT":
            return f"COUNT({column})", agg.name
        if agg.function == "COUNT_NON_NULL":
            return f"COUNT({column})", agg.name
        if agg.function == "COUNT_DISTINCT":
            return f"COUNT(DISTINCT {column})", agg.name
        if agg.function == "ANY_VALUE":
            return f"MIN({column})", agg.name
        return f"{agg.function}({column})", agg.name

    def _operand(self, source: str, table: str, operand, agg_exprs: dict[str, str], derived_exprs: dict[str, str]) -> str:
        if operand.kind == "aggregate":
            return agg_exprs[operand.name]
        if operand.kind == "derived":
            return derived_exprs[operand.name]
        if operand.kind == "literal":
            return render_literal(operand.value)
        if operand.kind == "field":
            return self._physical(source, table, operand.name or "")
        raise IRError("INVALID_IR", f"unknown operand kind '{operand.kind}'")

    def compile(self, query: AnalyticalQuery, source: str) -> CompiledQuery:
        table = query.relation
        applied_fields: list[str] = []
        physical_table = self.catalog.physical_table(source, table, parquet_archive=self.parquet_archive)

        where: list[str] = []
        seen_fields: set[str] = set()

        def note(field: str) -> None:
            if field and field not in seen_fields:
                seen_fields.add(field)
                applied_fields.append(field)

        for filt in query.entity_filters:
            logical = "batter_id" if filt.role == "BATTER" else "pitcher_id"
            column = self._physical(source, table, logical)
            ids = ", ".join(str(int(i)) for i in filt.ids)
            where.append(f"{column} IN ({ids})")
            note(logical)

        codes: list[str] = list(query.game_types)
        if query.population:
            codes.extend(self.catalog.resolve_population(query.population))
        if codes:
            unique = sorted(set(codes))
            column = self._physical(source, table, "game_type")
            where.append(f"{column} IN ({', '.join(render_literal(c) for c in unique)})")
            note("game_type")

        if query.season is not None:
            where.append(f"EXTRACT(YEAR FROM game_date) = {int(query.season)}")
            note("season")

        if query.date_range and (query.date_range.start or query.date_range.end):
            column = self._physical(source, table, "game_date")
            if query.date_range.start:
                where.append(f"{column} >= {render_literal(query.date_range.start)}")
            if query.date_range.end:
                where.append(f"{column} <= {render_literal(query.date_range.end)}")
            note("game_date")

        for condition in query.filters:
            where.append(self._condition(source, table, condition))
            self._collect_fields(condition, note)

        select_parts: list[str] = []
        group_columns: list[str] = []
        for field in query.group_by:
            expr = self._physical(source, table, field)
            alias = sanitize(field)
            select_parts.append(f"{expr} AS {alias}")
            group_columns.append(expr)
            note(field)

        agg_exprs: dict[str, str] = {}
        for agg in query.aggregates:
            expr, name = self._aggregate_expr(source, table, agg)
            alias = sanitize(name)
            agg_exprs[name] = expr
            select_parts.append(f"{expr} AS {alias}")
            if agg.field and agg.field != "*":
                note(agg.field)
            if agg.condition:
                self._collect_fields(agg.condition, note)

        derived_exprs: dict[str, str] = {}
        resolving: set[str] = set()
        by_name = {d.name: d for d in query.derived}

        def resolve_derived(name: str) -> str:
            if name in derived_exprs:
                return derived_exprs[name]
            if name in resolving:
                raise IRError("INVALID_IR", f"derived expression cycle involving '{name}'")
            resolving.add(name)
            derived = by_name[name]
            left = self._operand(source, table, derived.left, agg_exprs, _LazyDerived(resolve_derived))
            right = self._operand(source, table, derived.right, agg_exprs, _LazyDerived(resolve_derived))
            if derived.op == "ADD":
                expr = f"({left} + {right})"
            elif derived.op == "SUB":
                expr = f"({left} - {right})"
            elif derived.op == "MUL":
                expr = f"({left} * {right})"
            elif derived.op in {"DIV", "SAFE_DIV", "RATE"}:
                expr = f"(1.0 * {left} / NULLIF({right}, 0))"
            elif derived.op == "PCT":
                expr = f"(100.0 * {left} / NULLIF({right}, 0))"
            elif derived.op == "DIFF":
                expr = f"({left} - {right})"
            else:
                raise IRError("UNSUPPORTED_OPERATION", f"cannot compile derived op '{derived.op}'")
            resolving.discard(name)
            derived_exprs[name] = expr
            return expr

        for derived in query.derived:
            expr = resolve_derived(derived.name)
            select_parts.append(f"{expr} AS {sanitize(derived.name)}")

        if not select_parts:
            raise IRError("INVALID_IR", "nothing to select")

        sql = f"SELECT {', '.join(select_parts)} FROM {physical_table}"
        if where:
            sql += " WHERE " + " AND ".join(f"({w})" for w in where)
        if group_columns:
            sql += " GROUP BY " + ", ".join(group_columns)
        if query.qualification:
            sql += " HAVING " + self._qualification_sql(source, table, query.qualification)

        if query.sort:
            orders = []
            for spec in query.sort:
                alias = sanitize(spec.name)
                orders.append(f"{alias} {spec.direction} NULLS LAST")
            sql += " ORDER BY " + ", ".join(orders)
        if query.limit:
            sql += f" LIMIT {int(query.limit)}"

        output_columns = [sanitize(f) for f in query.group_by] + [sanitize(a.name) for a in query.aggregates] + [
            sanitize(d.name) for d in query.derived
        ]
        if len(set(output_columns)) != len(output_columns):
            raise IRError("INVALID_IR", "output aliases collide after identifier normalization")
        return CompiledQuery(
            sql=sql,
            source=source,
            table=table,
            applied_fields=applied_fields,
            output_columns=output_columns,
            group_columns=[sanitize(f) for f in query.group_by],
        )

    def _qualification_sql(self, source: str, table: str, qualification) -> str:
        basis = QUALIFICATION_BASES.get(qualification.basis)
        if basis is None:
            raise IRError("INVALID_IR", f"unknown qualification basis '{qualification.basis}'")
        if basis["mode"] == "COUNT":
            expr = "COUNT(*)"
        elif basis["mode"] == "COUNT_DISTINCT":
            expr = f"COUNT(DISTINCT {self._physical(source, table, basis['field'])})"
        else:
            expr = f"COUNT({self._physical(source, table, basis['field'])})"
        return f"{expr} >= {int(qualification.minimum)}"

    def _collect_fields(self, condition: Condition, note) -> None:
        if condition.field:
            note(condition.field)
        for nested in condition.conditions or []:
            self._collect_fields(nested, note)
