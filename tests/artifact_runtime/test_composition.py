"""Artifact-to-artifact composition: any tool output can feed another tool."""

import unittest
from datetime import date

from app.artifact_runtime.scope import Scope
from app.models.artifact_runtime import Need
from app.models.contracts import TimeRange
from app.models.entities import CanonicalEntity
from app.semantic.entity_resolver import EntityDictionary, EntityResolver
from app.tools.entity_lookup import EntityLookup
from app.tools.batting import BattingLine

from tests.artifact_runtime.fakes import FakeWeb, RecordingExecutor, ScriptedInterpreter, fake_roster
from tests.artifact_runtime.harness import build_test_runtime


def _local_ir(*, entity_set=True, window=True):
    return {
        "query_id": "q", "source_kind": "POSTGRES", "table": "statcast_pitches",
        "selections": [
            {"alias": "batter", "kind": "GROUP_KEY", "field": "batter_id"},
            {"alias": "rate", "kind": "DERIVED", "expression": {
                "op": "PCT", "left": {"op": "AGG", "alias": "hh"},
                "right": {"op": "AGG", "alias": "m"}}},
            {"alias": "hh", "kind": "AGGREGATE", "aggregate": {
                "op": "COUNT_IF", "alias": "hh", "condition": {
                    "kind": "COMPARE", "field": "launch_speed", "operator": "GTE",
                    "value": 95.0}}},
            {"alias": "m", "kind": "AGGREGATE", "aggregate": {
                "op": "COUNT_NON_NULL", "field": "launch_speed", "alias": "m"}},
        ],
        "entity_set": {"field": "batter_id", "export_ref": ""} if entity_set else None,
        "order_by": "rate", "limit": 10,
        "window": ({"start": "2025-04-01", "end": "2025-09-30"} if window else None),
    }


def _lines():
    return (
        BattingLine("A Hitter", "660271", "New York", 100, 5, 10, 20, 0.28, 0.36, 0.50, 0.86),
        BattingLine("B Hitter", "123456", "New York", 90, 3, 8, 22, 0.25, 0.33, 0.44, 0.77),
    )


class ArtifactCompositionTests(unittest.TestCase):
    def test_roster_to_local_analytics_uses_canonical_ids(self):
        need_roster = Need(need_id="need-roster", objective="roster",
                           proposed_capability="roster",
                           preferred_capabilities=("PLAYER_ID_SET",),
                           parameters={"team": "New York Yankees"})
        need_sql = Need(need_id="need-sql", objective="hard-hit rate",
                        proposed_capability="local_analytics",
                        preferred_capabilities=("STATISTICAL_RESULT",),
                        parameters={"analytical_query": _local_ir()},
                        depends_on=("need-roster",),
                        required_scope=Scope(entities=("New York Yankees",),
                                             population="players",
                                             time_range=TimeRange(start=date(2025, 4, 1),
                                                                  end=date(2025, 9, 30))))
        runtime, executor = build_test_runtime(needs=(need_roster, need_sql),
                                               executor=RecordingExecutor(rows=((660271, 0.62, 40, 65), (123456, 0.58, 30, 52))),
                                               player_names={"660271": "A Hitter"})
        result = runtime.send_message(runtime.start_conversation(), "Yankees hard-hit")
        self.assertEqual(result.status, "COMPLETE")
        sql = executor.statements[0]
        self.assertIn("batter_id IN (660271, 123456)", sql)
        self.assertNotIn("New York", sql)

    def test_team_population_does_not_use_city_names(self):
        # "New York" is shared by two clubs; the roster provider refuses it, so no SQL
        # runs and the runtime does not silently mix populations.
        need_roster = Need(need_id="need-roster", objective="roster",
                           proposed_capability="roster",
                           preferred_capabilities=("PLAYER_ID_SET",),
                           parameters={"team": "New York"})
        need_sql = Need(need_id="need-sql", objective="hard-hit rate",
                        proposed_capability="local_analytics",
                        preferred_capabilities=("STATISTICAL_RESULT",),
                        parameters={"analytical_query": _local_ir()},
                        depends_on=("need-roster",))
        runtime, executor = build_test_runtime(needs=(need_roster, need_sql))
        result = runtime.send_message(runtime.start_conversation(), "New York hard-hit")
        self.assertEqual(executor.statements, [])
        self.assertIn(result.status, ("LIMITED", "FAILED"))

    def test_batting_honors_an_explicit_date_range(self):
        from tests.artifact_runtime.fakes import FakeBattingClient
        client = FakeBattingClient(season_lines=_lines(), range_lines=_lines())
        need = Need(need_id="b", objective="last 30 days", proposed_capability="batting_stats",
                    preferred_capabilities=("STATISTICAL_RESULT",),
                    parameters={"start": "2025-08-31", "end": "2025-09-30", "metric": "OPS"})
        runtime, _ = build_test_runtime(needs=(need,), batting_client=client)
        runtime.send_message(runtime.start_conversation(), "recent")
        self.assertEqual(client.range_calls, [("2025-08-31", "2025-09-30")])
        self.assertEqual(client.season_calls, [])

    def test_batting_consumes_a_player_id_set(self):
        from tests.artifact_runtime.fakes import FakeBattingClient
        client = FakeBattingClient(season_lines=_lines())
        need_roster = Need(need_id="r", objective="roster", proposed_capability="roster",
                           preferred_capabilities=("PLAYER_ID_SET",),
                           parameters={"team": "New York Yankees"})
        need_bat = Need(need_id="b", objective="batting", proposed_capability="batting_stats",
                        preferred_capabilities=("STATISTICAL_RESULT",),
                        parameters={"season": 2025, "metric": "OPS"},
                        depends_on=("r",))
        runtime, _ = build_test_runtime(needs=(need_roster, need_bat), batting_client=client)
        result = runtime.send_message(runtime.start_conversation(), "Yankees OPS")
        batting = [a for a in result.artifacts if a.kind == "batting_stats"]
        self.assertTrue(batting)
        self.assertEqual({row["name"] for row in batting[0].structured_data["rows"]},
                         {"A Hitter", "B Hitter"})

    def test_local_analytics_feeds_compute(self):
        need_sql = Need(need_id="need-sql", objective="hard-hit rate",
                        proposed_capability="local_analytics",
                        preferred_capabilities=("STATISTICAL_RESULT",),
                        parameters={"analytical_query": _local_ir(entity_set=False)})
        need_compute = Need(need_id="need-mean", objective="mean rate",
                            proposed_capability="compute",
                            preferred_capabilities=("DERIVED_MEASURE",),
                            parameters={"op": "MEAN"}, depends_on=("need-sql",))
        runtime, _ = build_test_runtime(needs=(need_sql, need_compute), executor=RecordingExecutor(rows=((660271, 0.62, 40, 65), (123456, 0.58, 30, 52))))
        result = runtime.send_message(runtime.start_conversation(), "mean rate")
        derived = [a for a in result.artifacts if a.kind == "derived"]
        self.assertTrue(derived)
        self.assertIsNotNone(derived[0].structured_data["value"])

    def test_web_to_entity_resolution_to_sql(self):
        dictionary = EntityDictionary((
            CanonicalEntity(entity_key="MLBAM:660271", entity_type="PLAYER",
                            display_name="Shohei Ohtani", aliases=("Ohtani",)),))
        lookup = EntityLookup(dictionary, EntityResolver(dictionary))
        web = FakeWeb([{"url": "https://example.test/a",
                        "title": "Report", "fetched": True,
                        "text": "Shohei Ohtani changed his approach."}])
        needs = (
            Need(need_id="web", objective="approach change", proposed_capability="web_research",
                 preferred_capabilities=("WEB_EVIDENCE",)),
            Need(need_id="entities", objective="extract players",
                 proposed_capability="evidence_entities",
                 preferred_capabilities=("PLAYER_ID_SET",), depends_on=("web",)),
            Need(need_id="sql", objective="hard-hit rate",
                 proposed_capability="local_analytics",
                 preferred_capabilities=("STATISTICAL_RESULT",),
                 parameters={"analytical_query": _local_ir()}, depends_on=("entities",)),
        )
        runtime, executor = build_test_runtime(needs=needs, web=web, knowledge_items=None,
                                               executor=RecordingExecutor(rows=((660271, 0.62, 40, 65), (123456, 0.58, 30, 52))))
        runtime._entity_lookup = lookup  # noqa: SLF001 - test wiring
        result = runtime.send_message(runtime.start_conversation(), "why is Ohtani different")
        self.assertTrue(executor.statements, result.trace.steps)
        self.assertIn("batter_id IN (660271)", executor.statements[0])
        self.assertEqual(result.status, "COMPLETE")

    def test_db_analysis_can_drive_web_research(self):
        need_sql = Need(need_id="sql", objective="hard-hit rate",
                        proposed_capability="local_analytics",
                        preferred_capabilities=("STATISTICAL_RESULT",),
                        parameters={"analytical_query": _local_ir(entity_set=False)})
        need_web = Need(need_id="web", objective="explain the change",
                        proposed_capability="web_research",
                        preferred_capabilities=("WEB_EVIDENCE",), depends_on=("sql",))
        web = FakeWeb([{"url": "https://example.test/a", "title": "Story",
                        "fetched": True, "text": "Reported mechanical change."}])
        runtime, _ = build_test_runtime(needs=(need_sql, need_web), web=web, executor=RecordingExecutor(rows=((660271, 0.62, 40, 65), (123456, 0.58, 30, 52))))
        result = runtime.send_message(runtime.start_conversation(), "explain")
        web_artifacts = [a for a in result.artifacts if a.kind == "web_evidence"]
        self.assertTrue(web_artifacts)
        self.assertTrue(web_artifacts[0].lineage)


class PlannerInputNormalizationTests(unittest.TestCase):
    def test_capability_shaped_parameters_are_normalized(self):
        from app.artifact_runtime.planner import normalize_tool_inputs
        inputs = normalize_tool_inputs({
            "TEAM_NAME": "New York Yankees",
            "SEARCH_QUERY": "roster query",
            "DATE_RANGE": {"start": "2025-04-01", "end": "2025-09-30"},
            "ANALYTICAL_QUERY": {"query_id": "q"},
        })
        self.assertEqual(inputs["team"], "New York Yankees")
        self.assertEqual(inputs["query"], "roster query")
        self.assertEqual(inputs["start"], "2025-04-01")
        self.assertEqual(inputs["end"], "2025-09-30")
        self.assertEqual(inputs["analytical_query"], {"query_id": "q"})

    def test_roster_accepts_capability_shaped_parameters(self):
        need = Need(need_id="r", objective="resolve the roster", proposed_capability="roster",
                    preferred_capabilities=("PLAYER_ID_SET",),
                    parameters={"TEAM_NAME": "New York Yankees",
                                "SEARCH_QUERY": "roster"})
        runtime, _ = build_test_runtime(needs=(need,))
        result = runtime.send_message(runtime.start_conversation(), "roster")
        roster = [a for a in result.artifacts if a.kind == "team_roster"]
        self.assertTrue(roster)
        self.assertIn("660271", [p["player_id"] for p in roster[0].structured_data["players"]])


class ReplanningTests(unittest.TestCase):
    def test_invalid_ir_is_a_recovery_signal_not_a_crash(self):
        bad_ir = _local_ir(entity_set=False)
        bad_ir["filters"] = [{"kind": "COMPARE", "field": "not_a_field",
                              "operator": "EQ", "value": "x"}]
        need = Need(need_id="bad", objective="bad", proposed_capability="local_analytics",
                    preferred_capabilities=("STATISTICAL_RESULT",),
                    parameters={"analytical_query": bad_ir})
        runtime, executor = build_test_runtime(needs=(need,))
        result = runtime.send_message(runtime.start_conversation(), "bad query")
        self.assertEqual(executor.statements, [])
        self.assertIn(result.status, ("LIMITED", "FAILED"))
        self.assertTrue(any("UNKNOWN_FIELD" in step for step in result.trace.steps))

    def test_planner_can_add_a_corrected_need_after_a_recovery(self):
        from app.artifact_runtime.planner import ScriptedPlanner

        bad = _local_ir(entity_set=False)
        bad["filters"] = [{"kind": "COMPARE", "field": "ghost_field",
                          "operator": "EQ", "value": "x"}]
        bad_need = Need(need_id="bad", objective="bad", proposed_capability="local_analytics",
                        preferred_capabilities=("STATISTICAL_RESULT",),
                        parameters={"analytical_query": bad}, criticality="OPTIONAL")
        good_need = Need(need_id="good", objective="good", proposed_capability="local_analytics",
                         preferred_capabilities=("STATISTICAL_RESULT",),
                         parameters={"analytical_query": _local_ir(entity_set=False)})

        class ReplanningPlanner(ScriptedPlanner):
            def __init__(self):
                super().__init__((bad_need,))
                self.added = 0

            def add_needs(self, **kwargs):
                if kwargs.get("gaps") and self.added == 0:
                    self.added += 1
                    return (good_need,)
                return ()

        runtime, executor = build_test_runtime(
            needs=(bad_need,), executor=RecordingExecutor(rows=((660271, 0.62, 40, 65),)))
        runtime._planner = ReplanningPlanner()  # noqa: SLF001
        result = runtime.send_message(runtime.start_conversation(), "replan")
        self.assertTrue(executor.statements)
        self.assertIn("COMPLETE", result.status)


if __name__ == "__main__":
    unittest.main()
