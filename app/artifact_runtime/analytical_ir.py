"""Safe Analytical IR.

An expressive but closed intermediate representation for local analytics. The planner
(LLM) may describe *what* calculation is needed, but never emits SQL, arbitrary field
names or arbitrary Python. The IR is validated against the trusted
:class:`~app.artifact_runtime.schema_catalog.SchemaCatalog` and compiled by a deterministic
compiler into SQL that then passes the AST read-only guard.

Bounded operations: Source, Filter, Entity-set filter, Period/Conditional aggregation,
GroupBy, Aggregate, DerivedExpression, Sort, Limit. No arbitrary SQL fragments, no
arbitrary identifiers, no arbitrary expressions.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.artifact_runtime import _Envelope
from app.models.contracts import TimeRange

# Only deterministic, model-controlled identifier slots that must be valid SQL identifiers
# and nothing else. This is the primary defense against alias/identifier injection; the
# compiler re-checks and the AST read-only guard remains an additional boundary.
IDENTIFIER_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]{0,63}$"
Identifier = Annotated[str, Field(pattern=IDENTIFIER_PATTERN)]

AggregationOp = Literal["COUNT", "COUNT_NON_NULL", "COUNT_IF", "AVG", "SUM", "MIN", "MAX"]
BinaryOp = Literal["ADD", "SUB", "MUL", "DIV", "SAFE_DIV", "PCT", "DIFF", "RATE"]
ComparisonOp = Literal["EQ", "NE", "GT", "GTE", "LT", "LTE"]


class Compare(_Envelope):
    kind: Literal["COMPARE"] = "COMPARE"
    field: str
    operator: ComparisonOp
    value: Any = None


class Between(_Envelope):
    kind: Literal["BETWEEN"] = "BETWEEN"
    field: str
    low: Any = None
    high: Any = None


class InSet(_Envelope):
    kind: Literal["IN"] = "IN"
    field: str
    values: tuple[Any, ...] = ()


class NullCheck(_Envelope):
    kind: Literal["NULL"] = "NULL"
    field: str
    negate: bool = False  # True -> IS NOT NULL


class And(_Envelope):
    kind: Literal["AND"] = "AND"
    conditions: tuple["Condition", ...] = ()


class Or(_Envelope):
    kind: Literal["OR"] = "OR"
    conditions: tuple["Condition", ...] = ()


class Not(_Envelope):
    kind: Literal["NOT"] = "NOT"
    condition: "Condition"


Condition = Annotated[Union[Compare, Between, InSet, NullCheck, And, Or, Not],
                      Field(discriminator="kind")]

And.model_rebuild()
Or.model_rebuild()
Not.model_rebuild()


class Aggregate(_Envelope):
    op: AggregationOp
    field: str = ""
    condition: "Condition | None" = None
    alias: Identifier
    period: str = ""  # optional period label -> conditional aggregation


class AggregateRef(_Envelope):
    op: Literal["AGG"] = "AGG"
    alias: str


class NumberOperand(_Envelope):
    op: Literal["NUMBER"] = "NUMBER"
    value: float


class BinaryOperand(_Envelope):
    op: BinaryOp
    left: "DerivedExpr"
    right: "DerivedExpr"


DerivedExpr = Annotated[Union[AggregateRef, NumberOperand, BinaryOperand],
                        Field(discriminator="op")]

BinaryOperand.model_rebuild()


class Selection(_Envelope):
    alias: Identifier
    kind: Literal["GROUP_KEY", "AGGREGATE", "DERIVED"]
    field: str = ""
    aggregate: Aggregate | None = None
    expression: DerivedExpr | None = None


class EntitySetFilter(_Envelope):
    field: str
    export_ref: str


class PeriodSelector(_Envelope):
    label: str
    time_range: TimeRange


class AnalyticalQuery(BaseModel):
    """One bounded analytical request. Runtime-mutable only through re-planning."""

    model_config = ConfigDict(extra="forbid")

    query_id: str
    source_kind: Literal["POSTGRES", "PARQUET"]
    table: str
    selections: tuple[Selection, ...] = Field(min_length=1)
    filters: tuple[Condition, ...] = ()
    entity_set: EntitySetFilter | None = None
    order_by: str = ""
    direction: Literal["ASC", "DESC"] = "DESC"
    limit: int | None = None
    min_rows: int | None = None
    date_field: str = ""
    periods: tuple[PeriodSelector, ...] = ()
    window: TimeRange | None = None

    @field_validator("order_by")
    @classmethod
    def _validate_order_by_syntax(cls, value: str) -> str:
        import re
        if value and not re.match(IDENTIFIER_PATTERN, value):
            raise ValueError(f"order_by {value!r} is not a valid identifier")
        return value

    @model_validator(mode="after")
    def _validate_structure(self):
        aliases = [item.alias for item in self.selections]
        if len(aliases) != len(set(aliases)):
            raise ValueError("duplicate selection aliases")
        labels = [item.label for item in self.periods]
        if len(labels) != len(set(labels)):
            raise ValueError("duplicate period labels")
        if self.periods and not self.date_field:
            raise ValueError("period selectors require date_field")
        return self

    def alias_names(self) -> tuple[str, ...]:
        return tuple(item.alias for item in self.selections)


def make_compare(field: str, operator: ComparisonOp, value: Any) -> Compare:
    return Compare(field=field, operator=operator, value=value)


def make_time_range(start: str | date, end: str | date) -> TimeRange:
    start_date = date.fromisoformat(start) if isinstance(start, str) else start
    end_date = date.fromisoformat(end) if isinstance(end, str) else end
    return TimeRange(start=start_date, end=end_date)
