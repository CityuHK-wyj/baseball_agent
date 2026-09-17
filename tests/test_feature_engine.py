import unittest

from app.features.metrics import FeatureEngine, default_computations
from tests.factories import artifact


def engine() -> FeatureEngine:
    counter = iter(f"feature-{index}" for index in range(100))
    return FeatureEngine(id_factory=lambda _p: next(counter))


ROWS = [{"launch_speed": 90.0}, {"launch_speed": 100.0}, {"launch_speed": None}, {"launch_speed": 110.0}]


class FeatureEngineTests(unittest.TestCase):
    def test_computes_metrics_into_a_feature_artifact_with_lineage(self):
        source = artifact("a1")
        result = engine().compute(source, ROWS, ["mean_launch_speed", "max_launch_speed", "batted_ball_count"])
        self.assertEqual(result.values["mean_launch_speed"], 100.0)
        self.assertEqual(result.values["max_launch_speed"], 110.0)
        self.assertEqual(result.values["batted_ball_count"], 4.0)
        feature = result.artifact
        self.assertEqual(feature.descriptor.artifact_type, "FEATURE")
        self.assertEqual(feature.lineage, ("a1",))
        self.assertEqual(feature.provenance.source_kind, "FEATURE")
        self.assertIn("a1", feature.provenance.reference)
        self.assertEqual(set(feature.descriptor.data_keys), set(result.values))
        self.assertEqual(feature.row_count, 4)

    def test_feature_artifact_is_immutable_and_does_not_mutate_input(self):
        source = artifact("a1")
        before = source.model_dump()
        result = engine().compute(source, ROWS, ["mean_launch_speed"])
        self.assertEqual(source.model_dump(), before)
        with self.assertRaises(Exception):
            result.artifact.artifact_id = "other"

    def test_unknown_computation_and_empty_sample_fail(self):
        with self.assertRaises(KeyError):
            engine().compute(artifact("a1"), ROWS, ["does_not_exist"])
        with self.assertRaises(ValueError):
            engine().compute(artifact("a1"), [{"launch_speed": None}], ["mean_launch_speed"])

    def test_feature_engine_requires_a_table_input(self):
        derived = artifact("a1", descriptor=artifact().descriptor.model_copy(update={"artifact_type": "FEATURE"}))
        with self.assertRaises(ValueError):
            engine().compute(derived, ROWS, ["batted_ball_count"])

    def test_default_computations_are_declared(self):
        self.assertEqual(set(default_computations()),
                         {"mean_launch_speed", "max_launch_speed", "batted_ball_count"})


if __name__ == "__main__":
    unittest.main()
