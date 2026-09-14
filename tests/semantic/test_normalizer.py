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


if __name__ == "__main__":
    unittest.main()
