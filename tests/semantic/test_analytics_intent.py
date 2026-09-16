import unittest

from app.models.contracts import (CountConstraint, NumericConstraint,
                                  PitchTypeConstraint, PopulationConstraint,
                                  QualificationConstraint, RankingConstraint)
from app.semantic.analytics_intent import (extract_analytical_constraints,
                                           location_clarification_options)
from app.semantic.field_mapping import ZONE_UPPER_OUTSIDE, ZONE_UPPER_THIRD

TARGET = ("Find the top 5 MLB hitters by Exit Velocity when, after reaching two strikes, "
          "they faced fastballs above 95 mph located near the upper edge of the strike "
          "zone during 2023.")


def _numeric(query: str) -> NumericConstraint:
    intent = extract_analytical_constraints(query)
    return next(item for item in intent.constraints if isinstance(item, NumericConstraint))


def _ranking(query: str) -> RankingConstraint:
    intent = extract_analytical_constraints(query)
    return next(item for item in intent.constraints if isinstance(item, RankingConstraint))


def _count(query: str) -> CountConstraint:
    intent = extract_analytical_constraints(query)
    return next(item for item in intent.constraints if isinstance(item, CountConstraint))


def _population(query: str) -> PopulationConstraint:
    intent = extract_analytical_constraints(query)
    return next(item for item in intent.constraints if isinstance(item, PopulationConstraint))


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
        self.assertIn(ZONE_UPPER_OUTSIDE, values)
        self.assertEqual(len(options), 3)

    def test_no_analytical_cues_yields_no_constraints(self):
        intent = extract_analytical_constraints("How did Aaron Judge perform at the plate?")
        self.assertEqual(intent.constraints, ())
        self.assertFalse(intent.location_wording_requested)

    # -- Block 1: preserve explicit intent -----------------------------------

    def test_inclusive_pitch_velocity_threshold_is_preserved(self):
        velocity = _numeric("top 5 by exit velocity against fastballs >= 95 mph")
        self.assertEqual((velocity.key, velocity.operator, velocity.value),
                         ("pitch_velocity", "GTE", 95.0))

    def test_exit_velocity_threshold_maps_to_exit_velocity(self):
        velocity = _numeric("top 5 by exit velocity with exit velocity above 95 mph")
        self.assertEqual((velocity.key, velocity.operator), ("exit_velocity", "GT"))

    def test_exit_velocity_threshold_is_not_inferred_from_the_number_alone(self):
        constraints = extract_analytical_constraints(
            "top 5 by exit velocity with exit velocity >= 95 mph").constraints
        self.assertTrue(any(isinstance(c, NumericConstraint) and c.key == "exit_velocity"
                            for c in constraints))
        self.assertFalse(any(isinstance(c, NumericConstraint) and c.key == "pitch_velocity"
                             for c in constraints))

    def test_pitch_velocity_wording_maps_to_pitch_velocity(self):
        velocity = _numeric("top 5 by exit velocity against pitch velocity >= 95 mph")
        self.assertEqual(velocity.key, "pitch_velocity")

    def test_explicit_maximum_aggregation_is_preserved(self):
        self.assertEqual(_ranking("top 5 by maximum exit velocity").aggregation, "MAX")
        self.assertEqual(_ranking("top 5 by max EV").aggregation, "MAX")

    def test_explicit_average_aggregation_is_preserved(self):
        self.assertEqual(_ranking("top 5 by average exit velocity").aggregation, "AVG")
        self.assertEqual(_ranking("top 5 by avg EV").aggregation, "AVG")

    def test_bare_ranking_defaults_to_average_not_maximum(self):
        self.assertEqual(_ranking("top 5 by exit velocity").aggregation, "AVG")

    def test_explicit_0_2_stays_exactly_balls_0_strikes_2(self):
        count = _count("0-2 fastballs top 5 by exit velocity")
        self.assertEqual((count.balls, count.strikes), ((0,), 2))

    def test_two_strikes_stays_generic(self):
        count = _count("two strikes fastballs top 5 by exit velocity")
        self.assertEqual((count.balls, count.strikes), ((0, 1, 2, 3), 2))

    def test_explicit_count_does_not_widen_to_generic_two_strikes(self):
        count = _count("0-2 fastballs top 5 by exit velocity")
        self.assertNotEqual(count.balls, (0, 1, 2, 3))

    def test_multiple_explicit_counts_union_the_ball_values(self):
        count = _count("0-2, 1-2 and 2-2 fastballs top 5 by exit velocity")
        self.assertEqual((count.balls, count.strikes), ((0, 1, 2), 2))

    def test_date_range_is_never_read_as_a_count(self):
        intent = extract_analytical_constraints("top 5 exit velocity in 2023-12-31 to 2024-01-01")
        self.assertFalse(any(isinstance(item, CountConstraint) for item in intent.constraints))

    # -- Block 2: qualification thresholds -----------------------------------

    def test_explicit_minimum_batted_balls_is_captured(self):
        intent = extract_analytical_constraints("top 5 by exit velocity minimum 20 batted balls")
        qualification = next(item for item in intent.constraints
                             if isinstance(item, QualificationConstraint))
        self.assertEqual(qualification.min_batted_balls, 20)

    def test_explicit_qualifying_events_threshold_is_captured(self):
        intent = extract_analytical_constraints("top 5 by exit velocity minimum 10 qualifying events")
        qualification = next(item for item in intent.constraints
                             if isinstance(item, QualificationConstraint))
        self.assertEqual(qualification.min_batted_balls, 10)

    def test_no_qualification_constraint_when_absent(self):
        intent = extract_analytical_constraints("top 5 by exit velocity")
        self.assertFalse(any(isinstance(item, QualificationConstraint) for item in intent.constraints))

    # -- Block 3: explicit population ----------------------------------------

    def test_analytical_query_carries_an_explicit_default_population(self):
        population = _population("top 5 by exit velocity")
        self.assertEqual(population.game_types, ("REGULAR_SEASON",))
        self.assertEqual(population.event_population, "BATTED_BALL")

    def test_postseason_population_is_explicit(self):
        self.assertEqual(_population("top 5 by exit velocity in the postseason").game_types,
                         ("POSTSEASON",))

    def test_spring_training_population_is_explicit(self):
        self.assertEqual(_population("top 5 by exit velocity in spring training").game_types,
                         ("SPRING_TRAINING",))

    def test_regular_season_population_is_explicit(self):
        self.assertEqual(_population("top 5 by exit velocity in the regular season").game_types,
                         ("REGULAR_SEASON",))

    def test_measured_contact_population_is_explicit(self):
        self.assertEqual(_population("top 5 by exit velocity on all measured contact").event_population,
                         "MEASURED_CONTACT")

    def test_all_pitches_population_is_explicit(self):
        self.assertEqual(_population("top 5 by exit velocity across all pitches").event_population,
                         "ALL_PITCHES")

    # -- Block 4: zones 11-12 wording ----------------------------------------

    def test_just_above_the_zone_is_not_silently_mapped_to_zones_11_12(self):
        intent = extract_analytical_constraints(
            "top 5 by exit velocity just above the strike zone in 2023")
        self.assertTrue(intent.location_wording_requested)
        self.assertFalse(any(getattr(item, "definition", "") == ZONE_UPPER_OUTSIDE
                             for item in intent.constraints))


if __name__ == "__main__":
    unittest.main()
