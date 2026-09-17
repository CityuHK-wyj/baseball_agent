import unittest

from app.context.registry_source import MetricRegistrySource
from app.context.service import ContextItem, ContextRequest, ContextService, StaticContextSource
from app.models.metrics import MetricDefinition, SourceMapping
from app.semantic.metric_registry import MetricRegistry


def registry() -> MetricRegistry:
    return MetricRegistry(
        definitions=(
            MetricDefinition(metric_key="exit_velocity", display_name="Exit Velocity",
                             description="Batted ball speed", required_data_keys=("launch_speed",)),
            MetricDefinition(metric_key="era", display_name="ERA", description="Earned run average",
                             required_data_keys=("earned_runs",)),
        ),
        mappings=(SourceMapping(metric_key="exit_velocity", source_kind="POSTGRES",
                                location="statcast_pitches"),))


class MetricRegistrySourceTests(unittest.TestCase):
    def test_retrieves_metric_context_items(self):
        source = MetricRegistrySource(registry())
        service = ContextService((source,))
        package = service.retrieve(ContextRequest(request_id="q", kinds=("METRIC",), query="velocity"))
        self.assertEqual([entry.item_id for entry in package.items], ["exit_velocity"])
        self.assertEqual(package.items[0].kind, "METRIC")
        self.assertEqual(package.items[0].provenance_ref, "metric:exit_velocity")

    def test_empty_query_returns_all_metrics(self):
        service = ContextService((MetricRegistrySource(registry()),))
        package = service.retrieve(ContextRequest(request_id="q", kinds=("METRIC",)))
        self.assertEqual({entry.item_id for entry in package.items}, {"exit_velocity", "era"})


class CrossRunIsolationTests(unittest.TestCase):
    def setUp(self):
        self.global_source = StaticContextSource("REFERENCE", (ContextItem(
            item_id="league-state", kind="REFERENCE", title="League progress",
            content="season 60% complete", source="reference"),))
        self.run_a = StaticContextSource("COMPLETION_REPORT", (ContextItem(
            item_id="report-a", kind="COMPLETION_REPORT", title="Run A report",
            content="private to run A", source="reports", scope_run="run-a"),))
        self.run_b = StaticContextSource("COMPLETION_REPORT", (ContextItem(
            item_id="report-b", kind="COMPLETION_REPORT", title="Run B report",
            content="private to run B", source="reports", scope_run="run-b"),))
        self.service = ContextService((self.global_source, self.run_a, self.run_b))

    def test_run_scoped_items_do_not_leak_across_runs(self):
        package = self.service.retrieve(ContextRequest(request_id="q", run_id="run-a"))
        ids = {entry.item_id for entry in package.items}
        self.assertIn("report-a", ids)
        self.assertIn("league-state", ids)
        self.assertNotIn("report-b", ids)

    def test_global_knowledge_is_visible_to_every_run(self):
        for run_id in ("run-a", "run-b", ""):
            with self.subTest(run_id=run_id):
                package = self.service.retrieve(ContextRequest(request_id="q", run_id=run_id))
                self.assertIn("league-state", {entry.item_id for entry in package.items})

    def test_unscoped_request_sees_no_run_scoped_items(self):
        package = self.service.retrieve(ContextRequest(request_id="q"))
        ids = {entry.item_id for entry in package.items}
        self.assertNotIn("report-a", ids)
        self.assertNotIn("report-b", ids)
        self.assertIn("league-state", ids)


if __name__ == "__main__":
    unittest.main()
