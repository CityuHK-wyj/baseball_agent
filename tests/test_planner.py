import unittest

from app.agent.planner import PlannerContext, PlannerTerminalLatch, RuleBasedPlanner
from app.models.contracts import RequirementState
from tests.factories import objective, requirement


def context(**changes) -> PlannerContext:
    fields = dict(objective=objective(), requirements=(requirement(),),
                  requirement_states=(RequirementState(requirement_ref="r1"),),
                  recoverable_gaps=("r1",), round=0, max_rounds=3, budget_remaining=5)
    return PlannerContext(**(fields | changes))


class RuleBasedPlannerTests(unittest.TestCase):
    def setUp(self):
        counter = iter(f"id-{index}" for index in range(100))
        self.planner = RuleBasedPlanner(id_factory=lambda _p: next(counter))

    def test_first_decision_is_plan_and_targets_unmet_core_requirements(self):
        decision = self.planner.decide(context())
        self.assertEqual(decision.kind, "PLAN")
        self.assertFalse(decision.planner_terminal)
        self.assertEqual([task.requirement_refs for task in decision.tasks], [("r1",)])

    def test_second_decision_is_replan(self):
        decision = self.planner.decide(context(prior_plan_count=1, round=1))
        self.assertEqual(decision.kind, "REPLAN")

    def test_satisfied_core_requirement_stops_planning_as_complete(self):
        satisfied = (RequirementState(requirement_ref="r1", status="SATISFIED"),)
        decision = self.planner.decide(context(requirement_states=satisfied))
        self.assertEqual(decision.kind, "STOP_PLANNING")
        self.assertTrue(decision.planner_terminal)
        self.assertEqual(decision.terminal_reason, "COMPLETE")
        self.assertEqual(decision.tasks, ())

    def test_terminal_reasons_for_budget_rounds_and_unrecoverable_gaps(self):
        cases = {
            "MAX_ROUNDS": context(round=3),
            "BUDGET_EXHAUSTED": context(budget_remaining=0),
            "NO_RECOVERABLE_PATH": context(recoverable_gaps=()),
        }
        for reason, ctx in cases.items():
            with self.subTest(reason=reason):
                decision = self.planner.decide(ctx)
                self.assertTrue(decision.planner_terminal)
                self.assertEqual(decision.terminal_reason, reason)

    def test_planner_refuses_to_run_after_a_terminal_decision(self):
        with self.assertRaises(RuntimeError):
            self.planner.decide(context(planner_terminal=True, terminal_reason="COMPLETE"))

    def test_planner_never_mutates_the_requirement_baseline(self):
        baseline = requirement()
        ctx = context(requirements=(baseline,))
        self.planner.decide(ctx)
        self.assertEqual(ctx.requirements, (baseline,))
        self.assertEqual(baseline.origin, "INITIAL")
        with self.assertRaises(Exception):
            baseline.base_criticality = "OPTIONAL"


class PlannerTerminalLatchTests(unittest.TestCase):
    def test_latch_blocks_same_condition_and_reopens_only_on_external_change(self):
        latch = PlannerTerminalLatch()
        self.assertTrue(latch.may_invoke(("a",)))
        latch.latch("NO_PROGRESS", ("a",))
        self.assertTrue(latch.latched)
        self.assertFalse(latch.may_invoke(("a",)))
        self.assertTrue(latch.may_invoke(("a", "b")))
        latch.observe(("a", "b"))
        self.assertFalse(latch.latched)
        self.assertIsNone(latch.reason)


if __name__ == "__main__":
    unittest.main()
