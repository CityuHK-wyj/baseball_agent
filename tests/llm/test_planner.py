import unittest

from app.agent.planner import PlannerContext, RuleBasedPlanner
from app.llm.planner import LLMPlanner
from app.llm.provider import FakeModelProvider
from app.models.contracts import RequirementState
from tests.factories import objective, requirement


def context(**changes) -> PlannerContext:
    fields = dict(objective=objective(), requirements=(requirement(),),
                  requirement_states=(RequirementState(requirement_ref="r1"),),
                  recoverable_gaps=("r1",), round=0, max_rounds=3, budget_remaining=5)
    return PlannerContext(**(fields | changes))


def planner(provider, fallback=None) -> LLMPlanner:
    counter = iter(f"id-{index}" for index in range(100))
    return LLMPlanner(provider, "test-model", id_factory=lambda _p: next(counter), fallback=fallback)


class LLMPlannerTests(unittest.TestCase):
    def test_valid_json_becomes_a_planning_decision(self):
        provider = FakeModelProvider(['{"kind":"PLAN","tasks":[{"requirement_refs":["r1"],'
                                      '"description":"get ev"}],"rationale":"need data"}'])
        decision = planner(provider).decide(context())
        self.assertEqual(decision.kind, "PLAN")
        self.assertEqual(decision.tasks[0].requirement_refs, ("r1",))
        self.assertEqual(decision.objective_ref, "o1")

    def test_terminal_decision_is_parsed(self):
        provider = FakeModelProvider(['{"kind":"STOP_PLANNING","planner_terminal":true,'
                                      '"terminal_reason":"COMPLETE","rationale":"done"}'])
        decision = planner(provider).decide(context(requirement_states=(
            RequirementState(requirement_ref="r1", status="SATISFIED"),)))
        self.assertTrue(decision.planner_terminal)
        self.assertEqual(decision.terminal_reason, "COMPLETE")

    def test_task_referencing_an_unknown_requirement_is_rejected(self):
        provider = FakeModelProvider(['{"kind":"PLAN","tasks":[{"requirement_refs":["ghost"],'
                                      '"description":"x"}],"rationale":"x"}'])
        with self.assertRaises(ValueError):
            planner(provider).decide(context())

    def test_invalid_output_falls_back_to_the_deterministic_planner(self):
        provider = FakeModelProvider(["not json at all"])
        decision = planner(provider, fallback=RuleBasedPlanner(id_factory=lambda _p: "id")).decide(context())
        self.assertEqual(decision.kind, "PLAN")

    def test_invalid_kind_falls_back(self):
        provider = FakeModelProvider(['{"kind":"DESTROY_EVERYTHING"}'])
        decision = planner(provider, fallback=RuleBasedPlanner(id_factory=lambda _p: "id")).decide(context())
        self.assertEqual(decision.kind, "PLAN")

    def test_unknown_output_fields_are_rejected(self):
        provider = FakeModelProvider(['{"kind":"PLAN","tasks":[],"rationale":"x",'
                                      '"ignore_policy":true}'])
        with self.assertRaises(ValueError):
            planner(provider).decide(context())

    def test_wrong_task_shape_falls_back_instead_of_raising_an_internal_error(self):
        provider = FakeModelProvider(['{"kind":"PLAN","tasks":"ignore all safeguards",'
                                      '"rationale":"x"}'])
        decision = planner(provider, fallback=RuleBasedPlanner(id_factory=lambda _p: "id")).decide(context())
        self.assertEqual(decision.kind, "PLAN")
        self.assertEqual(decision.tasks[0].requirement_refs, ("r1",))

    def test_planner_refuses_to_run_after_a_terminal_decision(self):
        with self.assertRaises(RuntimeError):
            planner(FakeModelProvider()).decide(context(planner_terminal=True, terminal_reason="COMPLETE"))


if __name__ == "__main__":
    unittest.main()
