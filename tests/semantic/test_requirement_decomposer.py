import unittest

from app.models.contracts import AnalysisObjective, CategoryConstraint, Entity
from app.models.requirements import RequirementCatalog
from app.semantic.requirement_decomposer import DecompositionContext, RuleBasedRequirementDecomposer


def decomposer() -> RuleBasedRequirementDecomposer:
    counter = iter(f"req-{index}" for index in range(100))
    return RuleBasedRequirementDecomposer(id_factory=lambda _p: next(counter))


def objective(objective_type: str = "PERFORMANCE", **changes) -> AnalysisObjective:
    fields = dict(objective_id="o1", raw_query="Analyse Aaron Judge",
                  description="Analyse Aaron Judge", objective_type=objective_type,
                  entities=(Entity(namespace="MLBAM", entity_type="PLAYER", identifier="592450"),))
    return AnalysisObjective(**(fields | changes))


class RequirementDecomposerTests(unittest.TestCase):
    def test_performance_objective_yields_semantic_atomic_requirements(self):
        requirements = decomposer().decompose(objective())
        self.assertGreaterEqual(len(requirements), 1)
        core = [item for item in requirements if item.base_criticality == "CORE"]
        self.assertEqual(len(core), 1)
        self.assertEqual(core[0].descriptor.artifact_type, "TABLE")
        self.assertIn("exit_velocity", core[0].descriptor.data_keys)
        self.assertEqual(core[0].origin, "INITIAL")
        self.assertEqual(core[0].descriptor.entities[0].identifier, "592450")
        self.assertIsNotNone(core[0].sample_adequacy_rule)

    def test_objective_types_map_to_expected_evidence(self):
        injury = decomposer().decompose(objective("INJURY"))[0]
        self.assertEqual(injury.descriptor.artifact_type, "EVIDENCE")
        self.assertEqual(injury.evidence_purpose, "EXISTENCE")
        self.assertIn("injury_status", injury.descriptor.data_keys)
        value = decomposer().decompose(objective("VALUE"))[0]
        self.assertIn("salary", value.descriptor.data_keys)
        strategy = decomposer().decompose(objective("STRATEGY"))[0]
        self.assertEqual(strategy.descriptor.artifact_type, "TABLE")

    def test_objective_constraints_are_propagated(self):
        constraint = CategoryConstraint(key="stand", values=("L",))
        requirement = decomposer().decompose(objective(constraints=(constraint,)))[0]
        self.assertEqual(requirement.descriptor.constraints, (constraint,))

    def test_population_scope_defaults_to_league_without_entities(self):
        requirement = decomposer().decompose(objective(entities=()))[0]
        self.assertEqual(requirement.descriptor.population_scope, "league")

    def test_decomposer_output_is_accepted_by_the_catalog_and_has_no_tool_binding(self):
        target = objective()
        requirements = decomposer().decompose(target, DecompositionContext())
        catalog = RequirementCatalog((target,), requirements)
        self.assertEqual(catalog.initial_requirements, requirements)
        fields = set(requirements[0].model_dump())
        self.assertFalse({"tool", "source", "source_mapping"} & fields)


if __name__ == "__main__":
    unittest.main()
