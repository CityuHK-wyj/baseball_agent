import unittest

from app.agent.routing import Router, ToolCapability
from app.models.planning import AgentTask


def task(**changes) -> AgentTask:
    fields = dict(task_id="t1", objective_ref="o1", requirement_refs=("r1",), description="get exit velocity")
    return AgentTask(**(fields | changes))


def capabilities():
    return (
        ToolCapability(tool="hot", source_kind="POSTGRES", supported_artifact_types=("TABLE",)),
        ToolCapability(tool="cold", source_kind="PARQUET", supported_artifact_types=("TABLE",)),
        ToolCapability(tool="web", source_kind="WEB", supported_artifact_types=("TABLE",), cost="PAID"),
        ToolCapability(tool="down", source_kind="POSTGRES", supported_artifact_types=("TABLE",), available=False),
        ToolCapability(tool="features", source_kind="FEATURE", supported_artifact_types=("FEATURE",)),
    )


class RouterTests(unittest.TestCase):
    def setUp(self):
        counter = iter(f"id-{index}" for index in range(100))
        self.router = Router(capabilities(), id_factory=lambda _p: next(counter))

    def test_policy_excludes_unavailable_and_unpermitted_paid_sources(self):
        decision = self.router.route(task(), "TABLE")
        self.assertEqual(decision.selected_tool, "hot")
        self.assertEqual(set(decision.fallbacks), {"cold"})
        self.assertIn("web: PAID source not permitted", decision.policy_notes)
        self.assertIn("down: unavailable", decision.policy_notes)

    def test_capability_excludes_sources_for_the_wrong_artifact_type(self):
        decision = self.router.route(task(), "FEATURE")
        self.assertEqual(decision.selected_tool, "features")

    def test_preference_is_soft_and_wins_only_among_eligible_sources(self):
        preferred = self.router.route(task(source_preference="cold"), "TABLE")
        self.assertEqual(preferred.selected_tool, "cold")
        # Preference for a source blocked by policy falls back to optimization.
        blocked = self.router.route(task(source_preference="web"), "TABLE")
        self.assertEqual(blocked.selected_tool, "hot")

    def test_user_hard_source_constraint_outranks_preference(self):
        decision = self.router.route(task(source_preference="hot"), "TABLE",
                                     user_hard_sources=("PARQUET",))
        self.assertEqual(decision.selected_tool, "cold")
        self.assertIn("hot: violates a user source constraint", decision.policy_notes)

    def test_no_eligible_source_yields_no_selection(self):
        decision = self.router.route(task(), "TABLE", user_hard_sources=("WEB",))
        self.assertIsNone(decision.selected_tool)
        self.assertEqual(self.router.eligible_sources("TABLE", ("WEB",)), ())

    def test_eligible_sources_respects_policy(self):
        self.assertEqual([item.tool for item in self.router.eligible_sources("TABLE")], ["hot", "cold"])


if __name__ == "__main__":
    unittest.main()
