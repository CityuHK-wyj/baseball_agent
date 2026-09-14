import unittest

from pydantic import ValidationError

from app.models.contracts import AnalysisObjective, ArtifactDescriptor, ArtifactRequirement
from app.models.requirements import RequirementCatalog


def objective():
    return AnalysisObjective(objective_id="o1", raw_query="Did player 1 ever hit 105 mph?",
                             description="Establish the existence of a 105 mph batted ball")


def requirement(**changes):
    fields = dict(requirement_id="r1", objective_ref="o1", description="Exit velocity evidence",
                  descriptor=ArtifactDescriptor(artifact_type="TABLE", data_keys=("exit_velocity",),
                                                granularity="batted_ball", population_scope="player:1"))
    return ArtifactRequirement(**(fields | changes))


class RequirementCatalogTests(unittest.TestCase):
    def test_definitions_immediately_have_separate_initial_states(self):
        target, need = objective(), requirement()
        catalog = RequirementCatalog((target,), (need,))
        self.assertEqual(catalog.objective_states[0].status, "PENDING")
        self.assertEqual(catalog.requirement_states[0].status, "PENDING")
        self.assertFalse(hasattr(target, "status"))
        self.assertFalse(hasattr(need, "status"))
        self.assertEqual(catalog.initial_requirements, (need,))

    def test_planner_can_add_support_but_cannot_replace_baseline(self):
        baseline = requirement()
        catalog = RequirementCatalog((objective(),), (baseline,))
        support = requirement(requirement_id="support", origin="PLANNER_ADDED", parent_ref="r1")
        catalog.add_supporting(support)
        catalog.add_supporting(support)
        self.assertEqual(catalog.supporting_requirements, (support,))
        self.assertEqual(catalog.initial_requirements, (baseline,))
        self.assertEqual(catalog.completion_requirements("o1"), (baseline,))
        self.assertEqual(len(catalog.requirement_states), 2)
        with self.assertRaises(ValueError):
            catalog.add_supporting(requirement(origin="PLANNER_ADDED", base_criticality="OPTIONAL"))
        with self.assertRaises(ValidationError):
            baseline.base_criticality = "OPTIONAL"
        with self.assertRaises(ValidationError):
            baseline.descriptor.granularity = "pitch"
        with self.assertRaises(AttributeError):
            catalog.initial_requirements = ()

    def test_bad_baselines_and_invalid_support_references_fail(self):
        for initial in [(), (requirement(), requirement()),
                        (requirement(objective_ref="unknown"),),
                        (requirement(origin="PLANNER_ADDED"),),
                        (requirement(parent_ref="missing"),)]:
            with self.subTest(initial=initial), self.assertRaises(ValueError):
                RequirementCatalog((objective(),), initial)
        catalog = RequirementCatalog((objective(),), (requirement(),))
        for bad in [requirement(requirement_id="s"),
                    requirement(requirement_id="s", origin="PLANNER_ADDED", objective_ref="missing"),
                    requirement(requirement_id="s", origin="PLANNER_ADDED", parent_ref="missing"),
                    requirement(requirement_id="s", origin="PLANNER_ADDED", parent_ref="s")]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                catalog.add_supporting(bad)

    def test_duplicate_objective_identity_and_cross_objective_parent_fail(self):
        with self.assertRaises(ValueError):
            RequirementCatalog((objective(), objective()), (requirement(),))
        other = AnalysisObjective(objective_id="o2", raw_query="Another question", description="Another objective")
        catalog = RequirementCatalog((objective(), other),
                                     (requirement(), requirement(requirement_id="r2", objective_ref="o2")))
        with self.assertRaises(ValueError):
            catalog.add_supporting(requirement(requirement_id="s", objective_ref="o2", origin="PLANNER_ADDED", parent_ref="r1"))

    def test_domain_json_validates_dates_constraints_and_extra_state_fields(self):
        from app.models.contracts import TimeRange
        with self.assertRaises(ValidationError):
            TimeRange(start="2025-02-01", end="2025-01-01")
        with self.assertRaises(ValidationError):
            requirement(status="SATISFIED")
        with self.assertRaises(ValidationError):
            ArtifactDescriptor(artifact_type="TABLE", data_keys=(), granularity="pitch", population_scope="all")
        descriptor = requirement().descriptor
        serialized = descriptor.model_dump(mode="json")
        serialized["constraints"] = [{"kind": "NUMERIC", "key": "exit_velocity", "operator": "GT", "value": 105, "unit": "mph"}]
        roundtrip = ArtifactDescriptor.model_validate(serialized)
        self.assertEqual(roundtrip.constraints[0].operator, "GT")
        self.assertEqual(roundtrip.constraints[0].origin, "USER_CONFIRMED")
