import json
import unittest
from datetime import date

from app.models.contracts import (AnalysisObjective, CategoryConstraint, CountConstraint,
                                  CountState, LocationConstraint, NumericConstraint,
                                  PitchTypeConstraint, PopulationConstraint,
                                  QualificationConstraint, RankingConstraint, TimeRange)
from app.models.planning import AgentTask
from app.semantic.constraints import normalize_constraints
from app.semantic.field_mapping import (BATTER_RELATIVE_UPPER_EDGE, FieldMappingRegistry,
                                        ZONE_UPPER_OUTSIDE, ZONE_UPPER_THIRD)
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


def requirement(**location):
    return RuleBasedRequirementDecomposer(id_factory=lambda prefix: f"{prefix}-1").decompose(
        analytics_objective(**(location or {"definition": ZONE_UPPER_THIRD})))[0]


def requirement_with(constraints):
    objective = analytics_objective()
    return RuleBasedRequirementDecomposer(id_factory=lambda prefix: f"{prefix}-1").decompose(
        objective.model_copy(update={"constraints": tuple(objective.constraints) + tuple(constraints)}))[0]


def task():
    return AgentTask(task_id="t1", objective_ref="o1", requirement_refs=("requirement-1",),
                     description="ranked exit velocity")


class StatcastToolTests(unittest.TestCase):
    def test_adapter_configuration_cannot_override_frozen_qualification(self):
        required = requirement_with([QualificationConstraint(min_batted_balls=20)])
        executor = RecordingExecutor(rows=[(1, 30, 90.0, 95.0)])
        result = ParquetStatcastTool([required], FieldMappingRegistry(), executor,
                                    min_batted_balls=1).execute(task())
        self.assertIn("HAVING COUNT(*) >= 20", executor.statements[0])
        self.assertEqual(json.loads(result.payload)["min_batted_balls"], 20)

    def test_population_is_independent_of_ranking_metric_and_terminal_events(self):
        import duckdb
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute("""CREATE TABLE pitches AS
            SELECT 1 AS batter, DATE '2023-06-01' AS game_date,
                   96.0 AS release_speed, events, description, launch_speed, game_type
            FROM (VALUES
                ('single', 'hit_into_play', 100.0, 'R'),
                ('sac_fly', 'hit_into_play', 90.0, 'R'),
                ('field_error', 'hit_into_play', 110.0, 'R'),
                ('strikeout', 'swinging_strike', NULL, 'R'),
                ('walk', 'ball', NULL, 'R'),
                ('hit_by_pitch', 'hit_by_pitch', NULL, 'R'),
                ('truncated_pa', 'foul', 100.0, 'R'),
                (NULL, 'foul', 80.0, 'R'),
                ('field_out', 'hit_into_play', NULL, 'R'),
                ('single', 'hit_into_play', 100.0, 'S'),
                ('single', 'hit_into_play', 100.0, 'UNKNOWN'),
                ('single', 'hit_into_play', 100.0, NULL),
                ('sac_bunt', 'hit_into_play', 80.0, 'R'))
            AS sample(events, description, launch_speed, game_type)""")

        class FixtureExecutor:
            def execute_with_rows(self, sql):
                rows = connection.execute(sql).fetchall()
                return ToolResult.ok(len(rows)), rows

        class FixtureTool(ParquetStatcastTool):
            def _from_clause(self):
                return "pitches"

        for metric, expected in (("pitch_velocity", (5, 6, 10)),
                                 ("exit_velocity", (4, 6, 6))):
            for population, count in zip(("BATTED_BALL", "MEASURED_CONTACT", "ALL_PITCHES"), expected):
                with self.subTest(metric=metric, population=population):
                    objective = AnalysisObjective(objective_id="o1", raw_query="population audit",
                        description="population audit", constraints=(
                            RankingConstraint(metric_key=metric, limit=5),
                            PopulationConstraint(event_population=population),
                            QualificationConstraint(min_batted_balls=1)))
                    required = RuleBasedRequirementDecomposer(id_factory=lambda p: f"{p}-1").decompose(objective)[0]
                    result = FixtureTool([required], FieldMappingRegistry(), FixtureExecutor()).execute(task())
                    self.assertEqual(result.status, "OK")
                    self.assertEqual(json.loads(result.payload)["rows"][0][2], count)

    def test_entity_date_and_valid_count_bounds_filter_actual_rows(self):
        import duckdb
        from app.models.contracts import Entity

        original = requirement()
        descriptor = original.descriptor.model_copy(update={
            "entities": (Entity(namespace="MLBAM", entity_type="PLAYER", identifier="1"),),
            "constraints": tuple(c for c in original.descriptor.constraints if c.key != "date_range"),
        })
        scoped = original.model_copy(update={"descriptor": descriptor})
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute("""CREATE TABLE pitches AS
            SELECT batter, game_date, balls, 2 AS strikes, 96.0 AS release_speed,
                   'FF' AS pitch_type, 100.0 AS launch_speed, 1 AS zone
            FROM (VALUES (1, DATE '2023-06-01', 0), (1, DATE '2023-06-01', 1),
                         (1, DATE '2023-06-01', 2), (1, DATE '2023-06-01', 3),
                         (1, DATE '2023-06-01', 4), (1, DATE '2023-06-01', NULL),
                         (1, DATE '2022-06-01', 0), (2, DATE '2023-06-01', 0))
                 AS sample(batter, game_date, balls)""")

        class FixtureExecutor:
            def execute_with_rows(self, sql):
                rows = connection.execute(sql).fetchall()
                return ToolResult.ok(len(rows)), rows

        class FixtureTool(ParquetStatcastTool):
            def _from_clause(self):
                return "pitches"

        result = FixtureTool([scoped], FieldMappingRegistry(), FixtureExecutor(),
                             min_batted_balls=1).execute(task())
        self.assertEqual(json.loads(result.payload)["rows"], [["1", "", 4, 100.0, 100.0]])

    def test_unmapped_entity_cannot_be_stamped_onto_league_results(self):
        from app.models.contracts import Entity
        original = requirement()
        scoped = original.model_copy(update={"descriptor": original.descriptor.model_copy(update={
            "entities": (Entity(namespace="LOCAL", entity_type="TEAM", identifier="NYY"),)})})
        executor = RecordingExecutor(rows=[(1, 3, 100.0, 100.0)])
        result = ParquetStatcastTool([scoped], FieldMappingRegistry(), executor).execute(task())
        self.assertNotEqual(result.status, "OK")
        self.assertEqual(executor.statements, [])

    def test_upper_edge_band_excludes_pitches_above_batter_zone(self):
        import duckdb

        requirement_ = RuleBasedRequirementDecomposer(id_factory=lambda p: f"{p}-1").decompose(
            analytics_objective(definition=BATTER_RELATIVE_UPPER_EDGE))[0]
        requirement_ = requirement_.model_copy(update={"qualification_rule":
            requirement_.qualification_rule.model_copy(update={"min_batted_balls": 1})})
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute("""CREATE TABLE pitches AS
            SELECT batter, plate_z, 3.5 AS sz_top, 1.5 AS sz_bot,
                   2 AS strikes, 1 AS balls, 96.0 AS release_speed,
                   'FF' AS pitch_type, 100.0 AS launch_speed,
                   DATE '2023-06-01' AS game_date
            FROM (VALUES (1, 3.25), (2, 3.5), (3, 3.24),
                         (4, 3.51), (5, 6.0)) AS sample(batter, plate_z)""")

        class FixtureExecutor:
            def execute_with_rows(self, sql):
                rows = connection.execute(sql).fetchall()
                return ToolResult.ok(len(rows)), rows

        class FixtureTool(ParquetStatcastTool):
            def _from_clause(self):
                return "pitches"

        result = FixtureTool([requirement_], FieldMappingRegistry(), FixtureExecutor(),
                             min_batted_balls=1).execute(task())
        self.assertEqual(result.status, "OK")
        self.assertEqual({row[0] for row in json.loads(result.payload)["rows"]}, {"1", "2"})

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
        self.assertIn("balls IN (0, 1, 2, 3)", main)

    def test_compound_count_preserves_the_exact_union_not_a_cartesian_product(self):
        objective = analytics_objective()
        without_count = tuple(c for c in objective.constraints
                              if not isinstance(c, CountConstraint))
        mixed = CountConstraint(
            states=(CountState(balls=0, strikes=2), CountState(balls=1, strikes=1)),
            balls=(), origin="USER_EXPLICIT")
        normalized = normalize_constraints((*without_count, mixed))
        requirement_ = RuleBasedRequirementDecomposer(id_factory=lambda p: f"{p}-1").decompose(
            objective.model_copy(update={"constraints": normalized}))[0]
        executor = RecordingExecutor(rows=[(1, 1, 90.0, 90.0)])
        ParquetStatcastTool([requirement_], FieldMappingRegistry(), executor).execute(task())
        main = executor.statements[0]
        self.assertIn("(balls = 0 AND strikes = 2)", main)
        self.assertIn("(balls = 1 AND strikes = 1)", main)
        self.assertNotIn("balls IN", main)
        self.assertNotIn("strikes IN", main)

    def test_empty_source_result_is_zero_rows_not_a_query_failure(self):
        class EmptyExecutor(RecordingExecutor):
            def execute_with_rows(self, sql):
                self.statements.append(sql)
                if "MIN(" in sql:
                    return ToolResult.ok(len(self.observed)), self.observed
                if "player_dictionary" in sql:
                    return ToolResult.ok(0), []
                return ToolResult(status="EMPTY"), ()

        result = ParquetStatcastTool([requirement()], FieldMappingRegistry(),
                                     EmptyExecutor()).execute(task())
        self.assertEqual(result.status, "OK")
        self.assertEqual(result.artifact.row_count, 0)

    def test_default_qualification_is_frozen_not_hidden(self):
        executor = RecordingExecutor(rows=[(1, 30, 90.0, 95.0)])
        tool = ParquetStatcastTool([requirement()], FieldMappingRegistry(), executor)
        result = tool.execute(task())
        self.assertIn("HAVING COUNT(*) >= 3", executor.statements[0])
        self.assertEqual(json.loads(result.payload)["min_batted_balls"], 3)

    def test_explicit_qualification_threshold_is_used_by_the_query(self):
        requirement_ = requirement_with([QualificationConstraint(min_batted_balls=20)])
        executor = RecordingExecutor(rows=[(1, 30, 90.0, 95.0)])
        tool = ParquetStatcastTool([requirement_], FieldMappingRegistry(), executor)
        result = tool.execute(task())
        self.assertIn("HAVING COUNT(*) >= 20", executor.statements[0])
        self.assertNotIn("HAVING COUNT(*) >= 3", executor.statements[0])
        self.assertEqual(json.loads(result.payload)["min_batted_balls"], 20)

    def test_default_population_is_regular_season_batted_balls(self):
        requirement_ = requirement_with([PopulationConstraint()])
        executor = RecordingExecutor(rows=[(1, 30, 90.0, 95.0)])
        ParquetStatcastTool([requirement_], FieldMappingRegistry(), executor).execute(task())
        main = executor.statements[0]
        self.assertIn("game_type IN ('R')", main)
        self.assertIn("description = 'hit_into_play'", main)

    def test_postseason_population_uses_postseason_codes(self):
        requirement_ = requirement_with(
            [PopulationConstraint(game_types=("POSTSEASON",), event_population="BATTED_BALL")])
        executor = RecordingExecutor(rows=[(1, 30, 90.0, 95.0)])
        ParquetStatcastTool([requirement_], FieldMappingRegistry(), executor).execute(task())
        self.assertIn("game_type IN ('F', 'D', 'L', 'W')", executor.statements[0])

    def test_measured_contact_population_does_not_require_terminal_events(self):
        requirement_ = requirement_with(
            [PopulationConstraint(event_population="MEASURED_CONTACT")])
        executor = RecordingExecutor(rows=[(1, 30, 90.0, 95.0)])
        ParquetStatcastTool([requirement_], FieldMappingRegistry(), executor).execute(task())
        self.assertNotIn("events IS NOT NULL", executor.statements[0])
        self.assertIn("launch_speed IS NOT NULL", executor.statements[0])

    def test_source_without_game_type_fails_closed(self):
        requirement_ = requirement_with([PopulationConstraint()])

        class NoGameTypeParquetTool(ParquetStatcastTool):
            _COLUMNS = frozenset(ParquetStatcastTool._COLUMNS) - {"game_type"}

        tool = NoGameTypeParquetTool([requirement_], FieldMappingRegistry(), RecordingExecutor())
        result = tool.execute(task())
        self.assertEqual(result.status, "ERROR")
        self.assertEqual(result.error_code, "MISSING_PHYSICAL_FIELDS")
        self.assertIn("game_type", result.safe_error_summary)

    def test_zones_11_12_are_a_zone_set_not_an_above_sz_top_predicate(self):
        requirement_ = requirement_with([LocationConstraint(definition=ZONE_UPPER_OUTSIDE,
                                                            origin="USER_CONFIRMED")])
        executor = RecordingExecutor(rows=[(1, 1, 90.0, 90.0)])
        tool = ParquetStatcastTool([requirement_], FieldMappingRegistry(), executor)
        result = tool.execute(task())
        self.assertEqual(result.status, "OK")
        main = executor.statements[0]
        self.assertIn("zone IN (11, 12)", main)
        self.assertNotIn("plate_z > sz_top", main)
        self.assertNotIn("sz_top - 0.25", main)

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
