import unittest

from app.models.entities import CanonicalEntity
from app.semantic.entity_resolver import EntityDictionary, EntityResolver


def dictionary() -> EntityDictionary:
    return EntityDictionary((
        CanonicalEntity(entity_key="MLBAM:1", entity_type="PLAYER",
                        display_name="Kiké Hernández", aliases=("Kike Hernandez", "Enrique Hernandez")),
        CanonicalEntity(entity_key="MLBAM:2", entity_type="PLAYER", display_name="Hernandez"),
        CanonicalEntity(entity_key="MLBAM:3", entity_type="PLAYER", display_name="Hernandez"),
        CanonicalEntity(entity_key="MLBAM:592450", entity_type="PLAYER",
                        display_name="Aaron Judge", aliases=("交通指挥员",)),
    ))


def resolver() -> EntityResolver:
    counter = iter(f"id-{index}" for index in range(100))
    return EntityResolver(dictionary(), id_factory=lambda _p: next(counter))


class EntityResolverTests(unittest.TestCase):
    def test_alias_and_nickname_resolve_to_a_canonical_id(self):
        for mention in ("Kike Hernandez", "交通指挥员", "MLBAM:592450"):
            with self.subTest(mention=mention):
                resolution = resolver().resolve(mention)
                self.assertIsNotNone(resolution.canonical)
                self.assertFalse(resolution.needs_clarification)

    def test_ambiguous_display_name_requires_clarification_with_options(self):
        resolution = resolver().resolve("Hernandez")
        self.assertTrue(resolution.needs_clarification)
        self.assertIsNone(resolution.canonical)
        self.assertGreaterEqual(len(resolution.candidates), 2)
        request = resolver().propose_clarification(resolution)
        self.assertEqual(request.kind, "ENTITY")
        self.assertGreaterEqual(len(request.options), 2)
        self.assertIsNotNone(request.recommended_option_id)

    def test_unknown_mention_is_flagged(self):
        resolution = resolver().resolve("Totally Unknown")
        self.assertTrue(resolution.needs_clarification)
        self.assertEqual(resolution.candidates, ())
        self.assertIsNone(resolver().propose_clarification(resolution).recommended_option_id)

    def test_partial_name_matches_by_containment(self):
        resolution = resolver().resolve("Judge")
        self.assertEqual(resolution.canonical.entity_key, "MLBAM:592450")
        self.assertEqual(resolution.candidates[0].match_kind, "CONTAINS")

    def test_duplicate_entity_keys_are_rejected(self):
        with self.assertRaises(ValueError):
            EntityDictionary((CanonicalEntity(entity_key="X", entity_type="PLAYER", display_name="A"),
                              CanonicalEntity(entity_key="X", entity_type="PLAYER", display_name="B")))


if __name__ == "__main__":
    unittest.main()
