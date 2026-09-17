import unittest

from app.models.contracts import CategoryConstraint
from app.models.entities import CanonicalEntity
from app.semantic.entity_resolver import EntityDictionary, EntityResolver
from app.semantic.normalizer import SemanticNormalizer
from app.semantic.objective_extractor import RuleBasedObjectiveExtractor


def dictionary() -> EntityDictionary:
    return EntityDictionary((
        CanonicalEntity(entity_key="MLBAM:592450", entity_type="PLAYER",
                        display_name="Aaron Judge", aliases=("Judge", "交通指挥员")),
        CanonicalEntity(entity_key="MLBAM:2", entity_type="PLAYER", display_name="Hernandez"),
        CanonicalEntity(entity_key="MLBAM:3", entity_type="PLAYER", display_name="Hernandez"),
    ))


def normalizer() -> SemanticNormalizer:
    dict_ = dictionary()
    counter = iter(f"id-{index}" for index in range(100))
    ids = lambda prefix: f"{prefix}-{next(counter)}"
    return SemanticNormalizer(RuleBasedObjectiveExtractor(id_factory=ids),
                              EntityResolver(dict_, id_factory=ids), dict_, id_factory=ids)


class SemanticNormalizerTests(unittest.TestCase):
    def test_resolves_entity_and_produces_ready_objectives(self):
        result = normalizer().normalize(
            "How did Aaron Judge perform at the plate?",
            constraints=(CategoryConstraint(key="season", values=("2025",)),))
        self.assertTrue(result.ready)
        self.assertEqual(len(result.objectives), 1)
        entity = result.objectives[0].entities[0]
        self.assertEqual((entity.namespace, entity.identifier), ("MLBAM", "592450"))
        self.assertEqual(result.objectives[0].constraints[0].key, "season")

    def test_full_name_suppresses_overlapping_ambiguous_short_alias(self):
        known = EntityDictionary((
            CanonicalEntity(entity_key="MLBAM:592450", entity_type="PLAYER",
                            display_name="Aaron Judge", aliases=("Aaron", "Judge")),
            CanonicalEntity(entity_key="HIST:hank_aaron", entity_type="PLAYER",
                            display_name="Hank Aaron", aliases=("Aaron",)),))
        subject = SemanticNormalizer(RuleBasedObjectiveExtractor(), EntityResolver(known), known)
        result = subject.normalize("How did Aaron Judge perform?")
        self.assertFalse(result.needs_clarification)
        self.assertEqual(len(result.objectives[0].entities), 1)
        self.assertEqual(result.objectives[0].entities[0].identifier, "592450")

    def test_ambiguous_mention_produces_clarification_and_is_not_ready(self):
        result = normalizer().normalize("How did Hernandez perform?")
        self.assertTrue(result.needs_clarification)
        self.assertFalse(result.ready)
        self.assertIn("Hernandez", result.unresolved_mentions)
        self.assertEqual(result.clarifications[0].kind, "ENTITY")

    def test_nickname_resolves(self):
        result = normalizer().normalize("交通指挥员 的傷病情况", mentions=("交通指挥员",))
        self.assertTrue(result.ready)
        self.assertEqual(result.objectives[0].objective_type, "INJURY")

    def test_query_without_known_entities_still_yields_a_performance_objective(self):
        result = normalizer().normalize("Who led the league in home runs?")
        self.assertTrue(result.ready)
        self.assertEqual(result.objectives[0].objective_type, "PERFORMANCE")
        self.assertEqual(result.objectives[0].entities, ())

    def test_explicit_mentions_override_query_scanning(self):
        result = normalizer().normalize("Tell me about the player", mentions=("Judge",))
        self.assertEqual(result.objectives[0].entities[0].identifier, "592450")

    def test_year_versus_year_produces_two_frozen_windows(self):
        subject = normalizer()
        result = subject.normalize("top 5 by exit velocity in 2023 vs 2024")
        self.assertEqual(len(result.objectives), 2)
        windows = [next(c.values for c in obj.constraints if c.key == "date_range")
                   for obj in result.objectives]
        self.assertEqual(windows, [("2023-01-01", "2023-12-31"), ("2024-01-01", "2024-12-31")])
        self.assertEqual(len({obj.objective_id for obj in result.objectives}), 2)

    def test_recent_versus_previous_produces_two_windows(self):
        from datetime import date
        dict_ = dictionary()
        counter = iter(f"id-{index}" for index in range(100))
        ids = lambda prefix: f"{prefix}-{next(counter)}"
        subject = SemanticNormalizer(RuleBasedObjectiveExtractor(id_factory=ids),
                                     EntityResolver(dict_, id_factory=ids), dict_, id_factory=ids,
                                     today=lambda: date(2026, 9, 16))
        result = subject.normalize("top 5 by exit velocity over the last 30 days vs previous 30 days")
        self.assertEqual(len(result.objectives), 2)
        windows = [next(c.values for c in obj.constraints if c.key == "date_range")
                   for obj in result.objectives]
        self.assertEqual(windows, [("2026-08-18", "2026-09-16"), ("2026-07-19", "2026-08-17")])

    def test_multiple_years_become_a_comparison_without_raising(self):
        result = normalizer().normalize("Judge 2023 and 2024 performance")
        self.assertFalse(result.needs_clarification)
        windows = [next(c.values for c in obj.constraints if c.key == "date_range")
                   for obj in result.objectives]
        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0], ("2023-01-01", "2023-12-31"))
        self.assertEqual(windows[1], ("2024-01-01", "2024-12-31"))


if __name__ == "__main__":
    unittest.main()
