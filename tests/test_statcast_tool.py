import json
import unittest
from datetime import date

from app.models.contracts import (AnalysisObjective, CategoryConstraint, CountConstraint,
                                  LocationConstraint, NumericConstraint, PitchTypeConstraint,
                                  RankingConstraint, TimeRange)
from app.models.planning import AgentTask
from app.semantic.field_mapping import (BATTER_RELATIVE_UPPER_EDGE, FieldMappingRegistry,
                                        ZONE_UPPER_THIRD)
from app.semantic.requirement_decomposer import RuleBasedRequirementDecomposer
from app.tools.results import ToolResult
from app.tools.statcast import ParquetStatcastTool, PostgresStatcastTool


class RecordingExecutor:
    def __init__(self, rows=(), observed=((date(2023, 4, 1), date(2023, 10, 1)),)):
        self.rows = [tuple(row) for row in rows]
        self.observed = [tuple(row) for row in observed]
        self.statements = []

    def execute_with_rows(self, sql):
        self.statements.append(sql)
        if "MIN(" in sql:
            return ToolResult.ok(len(self.observed)), self.observed
        if "player_dictionary" in sql:
            return ToolResult.ok(0), []
        return ToolResult.ok(len(self.rows)), self.rows


def analytics_objective(**location):
    constraints = [
        CategoryConstraint(key="date_range", values=("2023-01-01", "2023-12-31"),
                           origin="SYSTEM_INFERRED"),
        CountConstraint(strikes=2, origin="SYSTEM_INFERRED"),
        NumericConstraint(key="pitch_velocity", operator="GT", value=95.0, unit="mph",
                          origin="SYSTEM_INFERRED"),
        PitchTypeConstraint(family="fastball", origin="SYSTEM_INFERRED"),
        RankingConstraint(metric_key="exit_velocity", direction="DESC", limit=5,
                          origin="SYSTEM_INFERRED"),
    ]
    if location:
        constraints.append(LocationConstraint(origin="USER_CONFIRMED", **location))
    return AnalysisObjective(objective_id="o1", raw_query="top 5 exit velocity",
                             description="top 5 exit velocity", constraints=tuple(constraints))


def requirement():
    return RuleBasedRequirementDecomposer(id_factory=lambda prefix: f"{prefix}-1").decompose(
        analytics_objective(definition=ZONE_UPPER_THIRD))[0]


def task():
    return AgentTask(task_id="t1", objective_ref="o1", requirement_refs=("requirement-1",),
                     description="ranked exit velocity")


class StatcastToolTests(unittest.TestCase):
    def test_generated_sql_is_read_only_and_uses_physical_columns(self):
        executor = RecordingExecutor(rows=[(592450, 12, 95.4, 108.1)])
        tool = ParquetStatcastTool([requirement()], FieldMappingRegistry(), executor)
        result = tool.execute(task())
        self.assertEqual(result.status, "OK")
        main, observed = executor.statements
        # Physical columns only; semantic keys never leak into the query.
        self.assertIn("release_speed", main)
        self.assertIn("launch_speed", main)
        self.assertIn("strikes", main)
        self.assertIn("pitch_type", main)
        self.assertIn("zone", main)
        self.assertIn("GROUP BY batter", main)
        self.assertIn("ORDER BY AVG(launch_speed) DESC", main)
        self.assertIn("LIMIT 5", main)
        self.assertNotIn("pitch_velocity", main)
        self.assertNotIn("exit_velocity", main)
        self.assertIn("SELECT", main)
        self.assertIn("read_parquet(", main)

    def test_ranking_aggregation_is_explicit_not_silently_decided(self):
        objective_max = analytics_objective(definition=ZONE_UPPER_THIRD)
        # Replace the ranking with an explicit MAX aggregation.
        from app.models.contracts import RankingConstraint
        constraints = tuple(
            RankingConstraint(metric_key="exit_velocity", aggregation="MAX", direction="DESC",
                              limit=5, origin="SYSTEM_INFERRED")
            if getattr(item, "kind", "") == "RANKING" else item
            for item in objective_max.constraints)
        objective_max = objective_max.model_copy(update={"constraints": constraints})
        requirement_ = RuleBasedRequirementDecomposer(id_factory=lambda p: f"{p}-1").decompose(
            objective_max)[0]
        executor = RecordingExecutor(rows=[(1, 1, 90.0, 95.0)])
        tool = ParquetStatcastTool([requirement_], FieldMappingRegistry(), executor)
        tool.execute(task())
        self.assertIn("ORDER BY MAX(launch_speed) DESC", executor.statements[0])
        self.assertIn("AVG(launch_speed) AS avg_metric", executor.statements[0])
        self.assertIn("MAX(launch_speed) AS max_metric", executor.statements[0])

    def test_fastball_maps_to_explicit_code_set(self):
        executor = RecordingExecutor(rows=[(1, 1, 90.0, 90.0)])
        tool = ParquetStatcastTool([requirement()], FieldMappingRegistry(), executor)
        tool.execute(task())
        main = executor.statements[0]
        self.assertIn("pitch_type IN ('FF', 'SI', 'FC', 'FA')", main)

    def test_source_without_sz_fields_cannot_silently_degrade(self):
        requirement_ = RuleBasedRequirementDecomposer(id_factory=lambda p: f"{p}-1").decompose(
            analytics_objective(definition=BATTER_RELATIVE_UPPER_EDGE))[0]

        class OldSchemaParquetTool(ParquetStatcastTool):
            _COLUMNS = frozenset(ParquetStatcastTool._COLUMNS) - {"sz_top", "sz_bot", "p_throws"}

        tool = OldSchemaParquetTool([requirement_], FieldMappingRegistry(), RecordingExecutor())
        result = tool.execute(task())
        self.assertEqual(result.status, "ERROR")
        self.assertEqual(result.error_code, "MISSING_PHYSICAL_FIELDS")
        self.assertIn("sz_top", result.safe_error_summary)

    def test_parquet_batter_relative_edge_emits_physical_band(self):
        requirement_ = RuleBasedRequirementDecomposer(id_factory=lambda p: f"{p}-1").decompose(
            analytics_objective(definition=BATTER_RELATIVE_UPPER_EDGE))[0]
        executor = RecordingExecutor(rows=[(592450, 9, 101.8, 115.5)])
        tool = ParquetStatcastTool([requirement_], FieldMappingRegistry(), executor)
        result = tool.execute(task())
        self.assertEqual(result.status, "OK")
        self.assertIn("plate_z >= sz_top - 0.25", executor.statements[0])

    def test_artifact_carries_provenance_and_matches_requirement(self):
        executor = RecordingExecutor(rows=[(592450, 12, 95.4, 108.1), (660271, 9, 94.0, 107.0)])
        tool = ParquetStatcastTool([requirement()], FieldMappingRegistry(), executor)
        result = tool.execute(task())
        self.assertIsNotNone(result.artifact)
        self.assertEqual(result.artifact.provenance.source_kind, "PARQUET")
        self.assertEqual(result.artifact.provenance.source, "parquet-archive")
        self.assertEqual(result.artifact.descriptor.data_keys, ("exit_velocity", "batter"))
        self.assertEqual(result.artifact.row_count, 2)
        payload = json.loads(result.payload.decode())
        self.assertEqual(payload["columns"][0], "batter")
        self.assertEqual(len(payload["rows"]), 2)

    def test_observed_range_is_truthful(self):
        executor = RecordingExecutor(rows=[(592450, 12, 95.4, 108.1)])
        tool = ParquetStatcastTool([requirement()], FieldMappingRegistry(), executor)
        result = tool.execute(task())
        self.assertEqual(result.artifact.observed_time_range.start, date(2023, 4, 1))
        self.assertEqual(result.artifact.observed_time_range.end, date(2023, 10, 1))

    def test_count_constraint_applies_strikes(self):
        executor = RecordingExecutor(rows=[(1, 1, 90.0, 90.0)])
        tool = ParquetStatcastTool([requirement()], FieldMappingRegistry(), executor)
        tool.execute(task())
        main = executor.statements[0]
        self.assertIn("strikes = 2", main)
        self.assertNotIn("balls IN", main)

    def test_postgres_batter_relative_edge_emits_physical_band(self):
        requirement_ = RuleBasedRequirementDecomposer(id_factory=lambda p: f"{p}-1").decompose(
            analytics_objective(definition=BATTER_RELATIVE_UPPER_EDGE))[0]
        executor = RecordingExecutor(rows=[(592450, 9, 101.8, 115.5)])
        tool = PostgresStatcastTool([requirement_], FieldMappingRegistry(), executor)
        result = tool.execute(task())
        self.assertEqual(result.status, "OK")
        main = executor.statements[0]
        self.assertIn("plate_z >= sz_top - 0.25", main)
        self.assertNotIn("zone IN", main)

    def test_postgres_tool_uses_batter_id_and_read_only_table(self):
        executor = RecordingExecutor(rows=[(592450, 12, 95.4, 108.1)])
        tool = PostgresStatcastTool([requirement()], FieldMappingRegistry(), executor)
        result = tool.execute(task())
        self.assertEqual(result.status, "OK")
        main = executor.statements[0]
        self.assertIn("FROM statcast_pitches", main)
        self.assertIn("GROUP BY batter_id", main)
        self.assertIn("SELECT batter_id AS batter", main)
        self.assertNotIn("INSERT", main)
        self.assertNotIn("UPDATE", main)
        self.assertNotIn("DELETE", main)

    def test_postgres_tool_resolves_batter_names_from_player_dictionary(self):
        class NameExecutor(RecordingExecutor):
            def execute_with_rows(self, sql):
                self.statements.append(sql)
                if "MIN(" in sql:
                    return ToolResult.ok(len(self.observed)), self.observed
                if "player_dictionary" in sql:
                    return ToolResult.ok(2), [(592450, "Aaron Judge"), (669261, "Jack Suwinski")]
                return ToolResult.ok(len(self.rows)), self.rows

        executor = NameExecutor(rows=[(592450, 9, 101.8, 115.5), (669261, 3, 107.2, 108.7)])
        tool = PostgresStatcastTool([requirement()], FieldMappingRegistry(), executor)
        result = tool.execute(task())
        payload = json.loads(result.payload.decode())
        names = {row[0]: row[1] for row in payload["rows"]}
        self.assertEqual(names["592450"], "Aaron Judge")
        self.assertEqual(names["669261"], "Jack Suwinski")


if __name__ == "__main__":
    unittest.main()
