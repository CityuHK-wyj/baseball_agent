"""Strict, closed typed contract for privileged deterministic SQL execution.

This is the *action boundary*. Open-world cognition (semantic understanding and
planning) may be permissive and partially unstructured, but the moment work is about to
touch PostgreSQL or DuckDB it must be compiled into this fully typed request:

    Open-world plan
        -> SQL request compilation
        -> strict typed SQLAnalysisRequest
        -> domain validation
        -> FieldMappingRegistry
        -> deterministic SQL generation
        -> SQL security guard
        -> baseball_readonly

The contract deliberately contains *only* validated structured data. It cannot express
arbitrary SQL, arbitrary column names, arbitrary tables, free-form SQL fragments or
LLM-authored identifiers. A request that cannot be compiled is *not* an objective
failure: it returns a structured recovery code to the Planner.
"""

from typing import Literal

from pydantic import Field, model_validator

from app.models.contracts import (Constraint, Contract, CountConstraint, Entity,
                                  LocationConstraint, NumericConstraint,
                                  PitchTypeConstraint, PopulationConstraint, TimeRange)

SQLSourceKind = Literal["POSTGRES", "PARQUET"]

# Constraint kinds a SQLAnalysisRequest may carry. Everything else is rejected rather
# than translated into SQL the Planner did not explicitly ask for.
_ALLOWED_FILTERS = (NumericConstraint, CountConstraint, PitchTypeConstraint,
                    LocationConstraint, PopulationConstraint)

# The full deterministic metric vocabulary the local SQL boundary can serve. A metric
# outside this set is reported as UNKNOWN_LOCAL_METRIC and control returns to the
# Planner (which may re-plan, use another metric or fall back to web).
LOCAL_SQL_METRICS: tuple[str, ...] = ("exit_velocity", "pitch_velocity")


class SQLAnalysisRequest(Contract):
    """The only object a local SQL builder is allowed to consume."""

    request_id: str = Field(min_length=1)
    source_kind: SQLSourceKind
    metric: str = Field(min_length=1)
    aggregation: Literal["AVG", "MAX", "MIN", "SUM"]
    direction: Literal["ASC", "DESC"]
    limit: int = Field(ge=1, le=10000)
    entities: tuple[Entity, ...] = ()
    time_range: TimeRange | None = None
    filters: tuple[Constraint, ...] = ()
    qualification_min_batted_balls: int = Field(ge=0)
    grouping: tuple[str, ...] = ("batter",)
    population: PopulationConstraint | None = None

    @model_validator(mode="after")
    def only_typed_filters(self):
        for item in self.filters:
            if not isinstance(item, _ALLOWED_FILTERS):
                raise ValueError(
                    f"SQLAnalysisRequest rejects non-SQL-shaped filter {type(item).__name__}")
        if self.metric not in LOCAL_SQL_METRICS:
            raise ValueError(f"SQLAnalysisRequest metric is not a local metric: {self.metric!r}")
        return self

    def requires(self) -> tuple[str, ...]:
        """Stable capability keys, mirroring the Router's constraint capability keys."""
        keys: list[str] = []
        for item in self.filters:
            if isinstance(item, NumericConstraint):
                keys.append(item.key)
            elif isinstance(item, CountConstraint):
                keys.append("count")
            elif isinstance(item, PitchTypeConstraint):
                keys.append("pitch_type")
            elif isinstance(item, LocationConstraint):
                keys.append(f"pitch_location:{item.definition}")
            elif isinstance(item, PopulationConstraint):
                keys.append("population")
        return tuple(dict.fromkeys(keys))


# Structured compilation failure codes. These are *planning signals*, not failures.
SQL_COMPILATION_CODES: tuple[str, ...] = (
    "MISSING_SQL_SEMANTICS",
    "UNKNOWN_LOCAL_METRIC",
    "UNSUPPORTED_LOCAL_ANALYTICS",
    "INSUFFICIENT_LOCAL_COVERAGE",
)
