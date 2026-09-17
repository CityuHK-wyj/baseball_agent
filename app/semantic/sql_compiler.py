"""Compile an open-world task/requirement into the strict SQL action contract.

The Planner may express a rich, partially structured objective. Before any privileged
deterministic database operation this module compiles the corresponding Requirement into
a :class:`SQLAnalysisRequest`. Compilation is deterministic and fails *closed*: it never
invents a metric, never widens a population and never emits SQL.

A compilation failure returns a structured recovery code so the Planner can re-plan, try
another metric, fall back to web research or ask for clarification. Compilation failure
is a planning signal, not an objective failure.
"""

from dataclasses import dataclass

from app.models.contracts import (ArtifactRequirement, CategoryConstraint, CountConstraint,
                                  LocationConstraint, NumericConstraint,
                                  PitchTypeConstraint, PopulationConstraint,
                                  RankingConstraint, TimeRange)
from app.models.sql_request import LOCAL_SQL_METRICS, SQLAnalysisRequest

# Category keys that are metadata/routing state rather than SQL filters.
_METADATA_CATEGORY_KEYS = frozenset({"source", "source_kind", "season", "entity_key"})

_FILTER_TYPES = (NumericConstraint, CountConstraint, PitchTypeConstraint,
                 LocationConstraint, PopulationConstraint)


@dataclass(frozen=True)
class SQLCompilationResult:
    request: SQLAnalysisRequest | None
    code: str = ""
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.request is not None


def compile_requirement(requirement: ArtifactRequirement,
                        source_kind: str = "PARQUET") -> SQLCompilationResult:
    """Compile one Requirement into a strict ``SQLAnalysisRequest`` or a recovery code."""
    constraints = requirement.descriptor.constraints
    ranking = next((item for item in constraints if isinstance(item, RankingConstraint)), None)
    if ranking is None:
        return SQLCompilationResult(
            None, "MISSING_SQL_SEMANTICS",
            "the local SQL boundary needs a validated ranking metric; none was compiled")
    if ranking.metric_key not in LOCAL_SQL_METRICS:
        return SQLCompilationResult(
            None, "UNKNOWN_LOCAL_METRIC",
            f"'{ranking.metric_key}' is not a local metric the SQL boundary can serve")

    filters = tuple(item for item in constraints if isinstance(item, _FILTER_TYPES))
    for item in constraints:
        if isinstance(item, CategoryConstraint) and item.key not in _METADATA_CATEGORY_KEYS \
                and item.key != "date_range":
            return SQLCompilationResult(
                None, "UNSUPPORTED_LOCAL_ANALYTICS",
                f"category constraint '{item.key}' has no deterministic SQL compilation")

    population = next((item for item in constraints if isinstance(item, PopulationConstraint)), None)
    qualification = 0
    if requirement.qualification_rule is not None and requirement.qualification_rule.min_batted_balls:
        qualification = int(requirement.qualification_rule.min_batted_balls)

    time_range = requirement.descriptor.time_range
    if time_range is None:
        window = next((item for item in constraints
                       if isinstance(item, CategoryConstraint) and item.key == "date_range"
                       and len(item.values) == 2), None)
        if window is not None:
            time_range = TimeRange(start=window.values[0], end=window.values[1])

    try:
        request = SQLAnalysisRequest(
            request_id=requirement.requirement_id,
            source_kind=source_kind if source_kind in ("POSTGRES", "PARQUET") else "PARQUET",
            metric=ranking.metric_key,
            aggregation=ranking.aggregation,
            direction=ranking.direction,
            limit=ranking.limit,
            entities=requirement.descriptor.entities,
            time_range=time_range,
            filters=filters,
            qualification_min_batted_balls=qualification,
            population=population,
        )
    except ValueError as error:  # pragma: no cover - defensive, contract enforced above
        return SQLCompilationResult(None, "UNSUPPORTED_LOCAL_ANALYTICS", str(error))
    return SQLCompilationResult(request)
