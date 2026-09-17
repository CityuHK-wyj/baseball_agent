"""The strict SQL action boundary: open-world in, closed typed contract out."""

import unittest
from datetime import date

from app.models.contracts import (ArtifactDescriptor, ArtifactRequirement, CategoryConstraint,
                                  QualificationRule, RankingConstraint)
from app.models.planning import AgentTask
from app.models.sql_request import SQLAnalysisRequest
from app.semantic.field_mapping import FieldMappingRegistry
from app.semantic.sql_compiler import compile_requirement
from app.tools.results import ToolResult
from app.tools.statcast import ParquetStatcastTool


class _Executor:
    def __init__(self, rows=(), observed=()):
        self.rows = list(rows)
        self.observed = list(observed)
        self.statements = []

    def execute_with_rows(self, sql):
        self.statements.append(sql)
        if "MIN(" in sql:
            return ToolResult.ok(len(self.observed)), self.observed
        return ToolResult.ok(len(self.rows)), self.rows


def _requirement(**changes) -> ArtifactRequirement:
    constraints = (
        CategoryConstraint(key="date_range", values=("2023-01-01", "2023-12-31")),
        RankingConstraint(metric_key="exit_velocity", aggregation="MAX", direction="DESC", limit=5),
    )
    fields = dict(
        requirement_id="r1", objective_ref="o1", description="rank exit velocity",
        descriptor=ArtifactDescriptor(artifact_type="TABLE", data_keys=("exit_velocity", "batter"),
                                      granularity="player_rank", population_scope="league",
                                      constraints=constraints),
        qualification_rule=QualificationRule(kind="CUSTOM", min_batted_balls=20),
    )
    return ArtifactRequirement(**(fields | changes))


def _task() -> AgentTask:
    return AgentTask(task_id="t1", objective_ref="o1", requirement_refs=("r1",),
                     description="rank exit velocity", task_type="LOCAL_ANALYTICS",
                     objective="rank hitters by maximum exit velocity")


class SQLCompilationTests(unittest.TestCase):
    def test_valid_requirement_compiles_to_a_strict_typed_request(self):
        result = compile_requirement(_requirement(), "PARQUET")
        self.assertTrue(result.ok)
        request = result.request
        self.assertEqual(request.metric, "exit_velocity")
        self.assertEqual(request.aggregation, "MAX")
        self.assertEqual(request.limit, 5)
        self.assertEqual(request.qualification_min_batted_balls, 20)
        self.assertEqual(request.time_range.start.isoformat(), "2023-01-01")
        self.assertEqual(request.source_kind, "PARQUET")

    def test_missing_ranking_is_a_planning_signal_not_a_failure(self):
        descriptor = ArtifactDescriptor(artifact_type="TABLE", data_keys=("exit_velocity",),
                                        granularity="batted_ball", population_scope="league")
        result = compile_requirement(_requirement(descriptor=descriptor), "PARQUET")
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "MISSING_SQL_SEMANTICS")

    def test_unknown_metric_returns_unknown_local_metric(self):
        constraints = (
            RankingConstraint(metric_key="spin_rate", aggregation="AVG", direction="DESC", limit=5),
        )
        result = compile_requirement(
            _requirement(descriptor=ArtifactDescriptor(
                artifact_type="TABLE", data_keys=("spin_rate", "batter"),
                granularity="player_rank", population_scope="league", constraints=constraints)),
            "PARQUET")
        self.assertEqual(result.code, "UNKNOWN_LOCAL_METRIC")

    def test_request_rejects_non_sql_shaped_filters_and_arbitrary_metrics(self):
        with self.assertRaises(Exception):
            SQLAnalysisRequest(request_id="x", source_kind="PARQUET", metric="spin_rate",
                               aggregation="AVG", direction="DESC", limit=5,
                               qualification_min_batted_balls=0)
        with self.assertRaises(Exception):
            SQLAnalysisRequest(
                request_id="x", source_kind="PARQUET", metric="exit_velocity",
                aggregation="AVG", direction="DESC", limit=5,
                qualification_min_batted_balls=0,
                filters=(CategoryConstraint(key="drop_table", values=("yes",)),))

    def test_tool_surfaces_the_structured_recovery_code(self):
        constraints = (
            RankingConstraint(metric_key="spin_rate", aggregation="AVG", direction="DESC", limit=5),
        )
        requirement = _requirement(descriptor=ArtifactDescriptor(
            artifact_type="TABLE", data_keys=("spin_rate", "batter"),
            granularity="player_rank", population_scope="league", constraints=constraints))
        tool = ParquetStatcastTool([requirement], FieldMappingRegistry(), _Executor())
        result = tool.execute(_task())
        self.assertEqual(result.status, "ERROR")
        self.assertEqual(result.error_code, "UNKNOWN_LOCAL_METRIC")

    def test_compiled_request_is_carried_into_the_execution_payload(self):
        executor = _Executor(rows=[(1, 25, 90.0, 95.0)],
                             observed=[(date(2023, 4, 1), date(2023, 10, 1))])
        tool = ParquetStatcastTool([_requirement()], FieldMappingRegistry(), executor)
        result = tool.execute(_task())
        self.assertEqual(result.status, "OK")
        payload = result.payload.decode()
        self.assertIn("compiled_sql_request", payload)
        self.assertIn("exit_velocity", payload)
        # Deterministic SQL generation, never LLM-authored text.
        self.assertTrue(executor.statements[0].startswith("SELECT"))


if __name__ == "__main__":
    unittest.main()
