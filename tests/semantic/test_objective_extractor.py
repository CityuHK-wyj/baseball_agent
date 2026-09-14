import unittest

from app.models.contracts import CategoryConstraint, Entity
from app.semantic.objective_extractor import RuleBasedObjectiveExtractor


def extractor() -> RuleBasedObjectiveExtractor:
    counter = iter(f"obj-{index}" for index in range(100))
    return RuleBasedObjectiveExtractor(id_factory=lambda _p: next(counter))


class ObjectiveExtractorTests(unittest.TestCase):
    def test_injury_value_and_performance_cues(self):
        cases = {
            "Was he injured last season?": ["INJURY"],
            "Analyse his salary and value for money": ["VALUE"],
            "How did he perform at the plate?": ["PERFORMANCE"],
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                types = [obj.objective_type for obj in extractor().extract(query)]
                self.assertEqual(types, expected)

    def test_multiple_cues_produce_multiple_objectives(self):
        types = [obj.objective_type for obj in
                 extractor().extract("傷病影響與薪資性價比如何？")]
        self.assertIn("INJURY", types)
        self.assertIn("VALUE", types)

    def test_entities_and_constraints_are_attached_and_priority_is_set(self):
        entity = Entity(namespace="MLBAM", entity_type="PLAYER", identifier="1")
        constraint = CategoryConstraint(key="stand", values=("L",))
        objective = extractor().extract("Was he injured?", entities=(entity,),
                                        constraints=(constraint,))[0]
        self.assertEqual(objective.entities, (entity,))
        self.assertEqual(objective.constraints, (constraint,))
        self.assertEqual(objective.base_priority, "HIGH")

    def test_objectives_are_unique_and_carry_raw_query(self):
        objectives = extractor().extract("salary and injuries")
        self.assertEqual(len({obj.objective_id for obj in objectives}), len(objectives))
        self.assertTrue(all(obj.raw_query == "salary and injuries" for obj in objectives))


if __name__ == "__main__":
    unittest.main()
