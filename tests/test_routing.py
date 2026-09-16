import unittest
from datetime import date

from app.agent.routing import Router, ToolCapability
from app.models.contracts import TimeRange
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

    def test_user_cost_approval_cannot_enable_a_system_forbidden_tool(self):
        router = Router((ToolCapability(tool="forbidden", source_kind="WEB",
                                        supported_artifact_types=("TABLE",), cost="PAID",
                                        system_permitted=False),))
        self.assertEqual(router.permission_candidates("TABLE"), ())
        self.assertIsNone(router.authorized_for(("PAID",)).route(task(), "TABLE").selected_tool)

    def test_permission_scope_does_not_enable_another_paid_tool(self):
        router = Router((
            ToolCapability(tool="approved", source_kind="WEB", supported_artifact_types=("TABLE",), cost="PAID"),
            ToolCapability(tool="other", source_kind="POSTGRES", supported_artifact_types=("TABLE",), cost="PAID"),
        ))
        decision = router.authorized_for(("PAID",), ("approved",)).route(
            task(source_preference="other"), "TABLE")
        self.assertEqual(decision.selected_tool, "approved")

    def test_coverage_filters_sources_by_requested_window(self):
        router = Router((
            ToolCapability(tool="cold", source_kind="PARQUET", supported_artifact_types=("TABLE",),
                           coverage_start=date(2015, 1, 1), coverage_end=date(2023, 12, 31)),
            ToolCapability(tool="hot", source_kind="POSTGRES", supported_artifact_types=("TABLE",),
                           coverage_start=date(2024, 1, 1), coverage_end=date(2026, 12, 31)),
        ))
        self.assertEqual([c.tool for c in router.eligible_sources(
            "TABLE", time_range=TimeRange(start=date(2023, 1, 1), end=date(2023, 12, 31)))], ["cold"])
        self.assertEqual([c.tool for c in router.eligible_sources(
            "TABLE", time_range=TimeRange(start=date(2025, 1, 1), end=date(2025, 12, 31)))], ["hot"])


if __name__ == "__main__":
    unittest.main()
