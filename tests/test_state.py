import unittest

from app.agent.registry import ArtifactRegistry
from app.assessment.judge import RuleBasedJudge
from app.assessment.service import AssessmentService
from app.models.contracts import ObjectiveState, RequirementState
from app.state.services import (derive_objective_state, derive_requirement_state, optional_gaps,
                                unmet_core_requirements)
from tests.factories import artifact, objective, requirement


def make_service(*artifacts):
    registry = ArtifactRegistry()
    for item in artifacts:
        registry.register(item)
    counter = iter(f"assessment-{index}" for index in range(100))
    return AssessmentService(registry, RuleBasedJudge(), id_factory=lambda _p: next(counter))


class RequirementStateTests(unittest.TestCase):
    def test_pending_without_assessments(self):
        state = derive_requirement_state("r1", ())
        self.assertEqual(state.status, "PENDING")
        self.assertEqual(state.version, 0)
        self.assertEqual(state.assessment_refs, ())

    def test_accepted_is_satisfied_weak_is_partial_reject_is_unsatisfied(self):
        service = make_service(artifact(row_count=1000))
        satisfied = derive_requirement_state("r1", [service.assess("a1", requirement())])
        self.assertEqual(satisfied.status, "SATISFIED")

        weak_service = make_service(artifact(row_count=1))
        weak = derive_requirement_state(
            "r1", [weak_service.assess("a1", requirement(evidence_purpose="INFERENTIAL", min_row_count=100))])
        self.assertEqual(weak.status, "PARTIAL")

        rejected_service = make_service(artifact(row_count=0))
        rejected = derive_requirement_state("r1", [rejected_service.assess("a1", requirement())])
        self.assertEqual(rejected.status, "UNSATISFIED")

    def test_state_is_idempotent_when_evidence_is_unchanged(self):
        service = make_service(artifact(row_count=1000))
        assessment = service.assess("a1", requirement())
        first = derive_requirement_state("r1", [assessment])
        second = derive_requirement_state("r1", [assessment], previous=first)
        self.assertIs(second, first)
        third = derive_requirement_state("r1", [assessment], previous=first.model_copy(update={"status": "PENDING"}))
        self.assertEqual(third.version, first.version + 1)


class ObjectiveStateTests(unittest.TestCase):
    def test_complete_requires_every_core_requirement_and_keeps_optional_gaps(self):
        core = requirement("r-core")
        optional = requirement("r-opt", base_criticality="OPTIONAL")
        states = {"r-core": RequirementState(requirement_ref="r-core", status="SATISFIED"),
                  "r-opt": RequirementState(requirement_ref="r-opt", status="UNSATISFIED")}
        state = derive_objective_state("o1", (core, optional), states, planner_terminal=True)
        self.assertEqual(state.status, "COMPLETE")
        self.assertEqual(optional_gaps((core, optional), states), ("r-opt",))
        self.assertEqual(unmet_core_requirements((core, optional), states), ())

    def test_in_progress_then_limited_at_terminal(self):
        core = requirement("r-core")
        pending = {"r-core": RequirementState(requirement_ref="r-core", status="PENDING")}
        self.assertEqual(derive_objective_state("o1", (core,), pending, planner_terminal=False).status, "PENDING")
        partial = {"r-core": RequirementState(requirement_ref="r-core", status="PARTIAL")}
        self.assertEqual(derive_objective_state("o1", (core,), partial, planner_terminal=False).status, "IN_PROGRESS")
        self.assertEqual(derive_objective_state("o1", (core,), partial, planner_terminal=True).status, "LIMITED")
        self.assertEqual(unmet_core_requirements((core,), partial), (core,))

    def test_terminal_with_nothing_useful_fails(self):
        core = requirement("r-core")
        rejected = {"r-core": RequirementState(requirement_ref="r-core", status="UNSATISFIED")}
        self.assertEqual(derive_objective_state("o1", (core,), rejected, planner_terminal=True).status, "FAILED")
        pending = {"r-core": RequirementState(requirement_ref="r-core", status="PENDING")}
        self.assertEqual(derive_objective_state("o1", (core,), pending, planner_terminal=True).status, "FAILED")

    def test_optional_requirements_cannot_complete_a_failing_objective(self):
        core = requirement("r-core")
        optional = requirement("r-opt", base_criticality="OPTIONAL")
        states = {"r-core": RequirementState(requirement_ref="r-core", status="UNSATISFIED"),
                  "r-opt": RequirementState(requirement_ref="r-opt", status="SATISFIED")}
        self.assertEqual(derive_objective_state("o1", (core, optional), states, planner_terminal=True).status, "FAILED")


if __name__ == "__main__":
    unittest.main()
