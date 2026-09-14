import unittest

from app.agent.routing import Router, ToolCapability
from app.agent.source_mapping import SourceMappingResolver
from app.models.metrics import MetricDefinition, SourceMapping
from app.models.planning import AgentTask
from app.semantic.metric_registry import MetricRegistry


def registry() -> MetricRegistry:
    return MetricRegistry(
        definitions=(MetricDefinition(metric_key="exit_velocity", display_name="EV", description="EV"),
                     MetricDefinition(metric_key="wrc_plus", display_name="wRC+", description="wRC+")),
        mappings=(SourceMapping(metric_key="exit_velocity", source_kind="POSTGRES",
                                location="statcast_pitches"),
                  SourceMapping(metric_key="wrc_plus", source_kind="FEATURE",
                                location="feature_engine", computation="CALCULATED")))


def task(preference: str | None = None) -> AgentTask:
    return AgentTask(task_id="t1", objective_ref="o1", requirement_refs=("r1",),
                     description="get metrics", source_preference=preference)


class SourceMappingResolverTests(unittest.TestCase):
    def test_direct_mapping_resolves_to_a_tool(self):
        resolver = SourceMappingResolver(registry(), {"POSTGRES": "hot"})
        route = resolver.resolve("t1", ("exit_velocity",))
        self.assertEqual(route.mode, "DIRECT")
        self.assertEqual(route.tool, "hot")
        self.assertEqual(route.required_source_kind, "POSTGRES")

    def test_calculated_mapping_uses_the_feature_engine(self):
        route = SourceMappingResolver(registry(), {"POSTGRES": "hot"}).resolve("t1", ("wrc_plus",))
        self.assertEqual(route.mode, "CALCULATED")
        self.assertEqual(route.required_source_kind, "FEATURE")

    def test_unmapped_key_is_blocked(self):
        route = SourceMappingResolver(registry(), {"POSTGRES": "hot"}).resolve("t1", ("mystery_metric",))
        self.assertEqual(route.mode, "NO_MAPPING")
        self.assertEqual(route.unmapped_keys, ("mystery_metric",))

    def test_direct_mapping_without_a_configured_tool_is_blocked(self):
        route = SourceMappingResolver(registry(), {}).resolve("t1", ("exit_velocity",))
        self.assertEqual(route.mode, "NO_MAPPING")


class RouterSourceMappingTests(unittest.TestCase):
    def setUp(self):
        counter = iter(f"id-{index}" for index in range(100))
        self.router = Router(
            (ToolCapability(tool="hot", source_kind="POSTGRES", supported_artifact_types=("TABLE", "FEATURE")),
             ToolCapability(tool="features", source_kind="FEATURE", supported_artifact_types=("TABLE", "FEATURE"))),
            id_factory=lambda _p: next(counter))
        self.resolver = SourceMappingResolver(registry(), {"POSTGRES": "hot"})

    def test_direct_route_selects_the_mapped_source(self):
        route = self.resolver.resolve("t1", ("exit_velocity",))
        decision = self.router.route(task(preference="features"), "TABLE", execution_route=route)
        self.assertEqual(decision.selected_tool, "hot")

    def test_calculated_route_selects_the_feature_source_despite_preference(self):
        route = self.resolver.resolve("t1", ("wrc_plus",))
        decision = self.router.route(task(preference="hot"), "TABLE", execution_route=route)
        self.assertEqual(decision.selected_tool, "features")

    def test_no_mapping_route_blocks_routing(self):
        route = self.resolver.resolve("t1", ("mystery",))
        decision = self.router.route(task(), "TABLE", execution_route=route)
        self.assertIsNone(decision.selected_tool)
        self.assertIn("blocked", decision.rationale)


if __name__ == "__main__":
    unittest.main()
