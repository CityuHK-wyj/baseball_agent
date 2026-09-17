"""Clarification policy: assume, research, and only then ask."""

import unittest

from app.semantic.hybrid_parser import HybridSemanticParser
from app.semantic.normalizer import SemanticNormalizer
from app.models.entities import CanonicalEntity
from app.semantic.entity_resolver import EntityDictionary, EntityResolver
from app.semantic.objective_extractor import RuleBasedObjectiveExtractor


def _normalizer() -> SemanticNormalizer:
    dictionary = EntityDictionary((
        CanonicalEntity(entity_key="MLBAM:660271", entity_type="PLAYER",
                        display_name="Shohei Ohtani", aliases=("Ohtani",)),
        CanonicalEntity(entity_key="MLBAM:592450", entity_type="PLAYER",
                        display_name="Aaron Judge", aliases=("Judge",)),
    ))
    ids = lambda prefix: f"{prefix}-1"  # noqa: E731
    return SemanticNormalizer(RuleBasedObjectiveExtractor(id_factory=ids),
                              EntityResolver(dictionary, id_factory=ids), dictionary,
                              id_factory=ids, semantic_parser=HybridSemanticParser())


class ClarificationPolicyTests(unittest.TestCase):
    def test_vague_analytical_goal_does_not_require_metric_clarification(self):
        result = _normalizer().normalize("最近30天Ohtani和Judge谁打得更好？")
        self.assertFalse(result.needs_clarification)
        self.assertTrue(result.understanding.analysis_strategy)

    def test_material_location_ambiguity_still_clarifies(self):
        result = _normalizer().normalize(
            "top 5 by exit velocity on high fastballs at least 95 mph in 2023")
        self.assertTrue(result.needs_clarification)
        self.assertTrue(any(item.kind == "CONSTRAINT" for item in result.clarifications))

    def test_non_material_ambiguities_do_not_block(self):
        # A model-reported non-location ambiguity is informational; deterministic date
        # handling owns temporal meaning and must not force a clarification.
        result = _normalizer().normalize("top 5 by exit velocity in 2023")
        self.assertFalse(result.needs_clarification)


if __name__ == "__main__":
    unittest.main()
