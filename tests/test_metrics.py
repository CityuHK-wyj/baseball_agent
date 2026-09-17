import unittest

from app.models.metrics import MetricDefinition, SourceMapping
from app.semantic.metric_registry import MetricRegistry


def definition(key: str, **changes) -> MetricDefinition:
    fields = dict(metric_key=key, display_name=key.upper(), description=f"{key} description",
                  required_data_keys=("launch_speed",), unit="")
    return MetricDefinition(**(fields | changes))


def mapping(key: str, **changes) -> SourceMapping:
    fields = dict(metric_key=key, source_kind="POSTGRES", location="statcast_pitches")
    return SourceMapping(**(fields | changes))


class MetricRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = MetricRegistry(
            definitions=(definition("exit_velocity"), definition("era"), definition("wrc_plus")),
            mappings=(mapping("exit_velocity"), mapping("era", source_kind="WEB", location="fangraphs"),
                      mapping("wrc_plus", source_kind="FEATURE", computation="CALCULATED")))

    def test_lookup_and_mapping(self):
        self.assertEqual(self.registry.get("era").metric_key, "era")
        self.assertEqual(self.registry.mapping_for("wrc_plus").computation, "CALCULATED")
        self.assertIsNone(self.registry.mapping_for("unknown"))

    def test_search_is_deterministic_and_case_insensitive(self):
        self.assertEqual([item.metric_key for item in self.registry.search("")], ["exit_velocity", "era", "wrc_plus"])
        self.assertEqual([item.metric_key for item in self.registry.search("ERA")], ["era"])
        self.assertEqual(self.registry.search("not-a-metric"), ())

    def test_duplicate_keys_and_unknown_mappings_fail(self):
        with self.assertRaises(ValueError):
            MetricRegistry(definitions=(definition("era"), definition("era")))
        with self.assertRaises(ValueError):
            MetricRegistry(definitions=(definition("era"),), mappings=(mapping("unknown"),))

    def test_metric_definition_is_immutable(self):
        item = definition("era")
        with self.assertRaises(Exception):
            item.metric_key = "other"


if __name__ == "__main__":
    unittest.main()
