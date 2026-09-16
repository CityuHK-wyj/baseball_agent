import unittest

from app.models.contracts import (CountConstraint, NumericConstraint, PitchTypeConstraint,
                                  RankingConstraint)
from app.semantic.analytics_intent import (extract_analytical_constraints,
                                           location_clarification_options)
from app.semantic.field_mapping import ZONE_ABOVE_UPPER_EDGE, ZONE_UPPER_THIRD

TARGET = ("Find the top 5 MLB hitters by Exit Velocity when, after reaching two strikes, "
          "they faced fastballs above 95 mph located near the upper edge of the strike "
          "zone during 2023.")


class AnalyticsIntentTests(unittest.TestCase):
    def test_two_strikes_is_typed_not_an_opaque_string(self):
        intent = extract_analytical_constraints(TARGET)
        count = next(item for item in intent.constraints if isinstance(item, CountConstraint))
        self.assertEqual(count.strikes, 2)
        self.assertEqual(count.kind, "COUNT")

    def test_pitch_velocity_is_separate_from_exit_velocity(self):
        intent = extract_analytical_constraints(TARGET)
        velocity = next(item for item in intent.constraints if isinstance(item, NumericConstraint))
        self.assertEqual(velocity.key, "pitch_velocity")
        self.assertEqual((velocity.operator, velocity.value, velocity.unit), ("GT", 95.0, "mph"))
        self.assertNotEqual(velocity.key, "exit_velocity")

    def test_fastball_is_an_explicit_family(self):
        intent = extract_analytical_constraints(TARGET)
        pitch_type = next(item for item in intent.constraints if isinstance(item, PitchTypeConstraint))
        self.assertEqual(pitch_type.family, "fastball")

    def test_ranking_carries_metric_direction_and_limit(self):
        intent = extract_analytical_constraints(TARGET)
        ranking = next(item for item in intent.constraints if isinstance(item, RankingConstraint))
        self.assertEqual((ranking.metric_key, ranking.direction, ranking.limit),
                         ("exit_velocity", "DESC", 5))

    def test_bare_upper_edge_requests_clarification(self):
        intent = extract_analytical_constraints(TARGET)
        self.assertTrue(intent.location_wording_requested)

    def test_explicit_upper_third_is_resolved_without_clarification(self):
        intent = extract_analytical_constraints(
            "top 3 by exit velocity on fastballs above 95 mph in the upper third of the zone in 2023")
        self.assertFalse(intent.location_wording_requested)
        self.assertTrue(any(getattr(item, "definition", "") == ZONE_UPPER_THIRD
                            for item in intent.constraints))

    def test_clarification_options_are_distinct_semantic_definitions(self):
        options = location_clarification_options()
        values = {value for value, _, _ in options}
        self.assertIn(ZONE_UPPER_THIRD, values)
        self.assertIn(ZONE_ABOVE_UPPER_EDGE, values)
        self.assertEqual(len(options), 3)

    def test_no_analytical_cues_yields_no_constraints(self):
        intent = extract_analytical_constraints("How did Aaron Judge perform at the plate?")
        self.assertEqual(intent.constraints, ())
        self.assertFalse(intent.location_wording_requested)


if __name__ == "__main__":
    unittest.main()
