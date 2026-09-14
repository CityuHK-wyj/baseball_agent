import unittest

from app.agent.executor import Executor, ToolResult
from app.agent.orchestrator import Orchestrator
from app.agent.planner import RuleBasedPlanner
from app.agent.registry import ArtifactRegistry
from app.agent.routing import Router, ToolCapability
from app.assessment.judge import RuleBasedJudge
from app.assessment.service import AssessmentService
from app.models.artifacts import Artifact
from app.models.contracts import ArtifactDescriptor
from tests.factories import artifact, objective, requirement


class RequirementTool:
    """Deterministic test tool: returns a scripted Artifact per requirement."""

    def __init__(self, name: str, artifacts_by_requirement: dict[str, Artifact]):
        self.name = name
        self._artifacts = artifacts_by_requirement

    def execute(self, task):
        result = self._artifacts.get(task.requirement_refs[0])
        if result is None:
            return ToolResult(status="EMPTY")
        return ToolResult(status="OK", artifact=result)


def build(artifacts_by_requirement, *, capabilities=None, max_rounds=3, budget=10,
          permitted_sources=(), permitted_costs=("FREE",)):
    registry = ArtifactRegistry()
    counter = iter(f"id-{index}" for index in range(1000))
    ids = lambda prefix: f"{prefix}-{next(counter)}"
    assessment = AssessmentService(registry, RuleBasedJudge(), id_factory=ids)
    router = Router(capabilities or (
        ToolCapability(tool="tool", source_kind="SYNTHETIC", supported_artifact_types=("TABLE",)),
    ), id_factory=ids, permitted_costs=permitted_costs)
    executor = Executor({"tool": RequirementTool("tool", artifacts_by_requirement)},
                        max_retries=0, id_factory=ids)
    orchestrator = Orchestrator(RuleBasedPlanner(id_factory=ids, max_rounds=max_rounds), router,
                                executor, assessment, registry, max_rounds=max_rounds,
                                budget=budget, id_factory=ids, permitted_sources=permitted_sources)
    return orchestrator, registry, assessment


class OrchestratorEndToEndTests(unittest.TestCase):
    def test_successful_run_reaches_complete_with_accepted_evidence(self):
        orchestrator, _, _ = build({"r1": artifact(row_count=1200)})
        result = orchestrator.run(objective(), (requirement(),))
        self.assertEqual(result.completion_report.stop_reason, "COMPLETE")
        self.assertEqual(result.objective_state.status, "COMPLETE")
        self.assertEqual(result.completion_report.plan_revisions, 1)
        self.assertEqual(len(result.completion_report.final_artifact_refs), 1)
        package = result.response_package
        self.assertEqual(len(package.accepted_evidence), 1)
        self.assertEqual(package.accepted_evidence[0].artifact_ref, "a1")
        self.assertEqual(package.unresolved_items, ())

    def test_multiple_core_requirements_all_satisfied(self):
        first = requirement("r1")
        second = requirement("r2", descriptor=ArtifactDescriptor(
            artifact_type="TABLE", data_keys=("launch_angle",), granularity="batted_ball",
            population_scope="player:1"))
        orchestrator, _, _ = build({
            "r1": artifact("a1"),
            "r2": artifact("a2", descriptor=second.descriptor),
        })
        result = orchestrator.run(objective(), (first, second))
        self.assertEqual(result.completion_report.stop_reason, "COMPLETE")
        self.assertEqual(len(result.response_package.accepted_evidence), 2)
        self.assertEqual(len(result.executions), 2)

    def test_zero_row_artifact_is_rejected_and_never_reaches_response(self):
        orchestrator, _, _ = build({"r1": artifact(row_count=0)})
        result = orchestrator.run(objective(), (requirement(),))
        self.assertEqual(result.objective_state.status, "FAILED")
        self.assertEqual(result.completion_report.stop_reason, "NO_PROGRESS")
        self.assertEqual(result.response_package.accepted_evidence, ())
        self.assertEqual(result.response_package.unresolved_items, ("r1",))
        # Internal history retains the rejected evidence for debugging and handoff.
        self.assertTrue(any(item.final_level == "REJECT" for item in result.assessments))
        self.assertEqual(len(result.executions), 2)  # second round attempted the same artifact, then stopped

    def test_no_eligible_source_stops_immediately_without_execution(self):
        orchestrator, _, _ = build({"r1": artifact()},
                                   capabilities=(ToolCapability(tool="web", source_kind="WEB",
                                                                supported_artifact_types=("TABLE",),
                                                                cost="PAID"),))
        result = orchestrator.run(objective(), (requirement(),))
        self.assertEqual(result.completion_report.stop_reason, "POLICY_BLOCKED")
        self.assertEqual(result.executions, ())
        self.assertEqual(result.response_package.accepted_evidence, ())

    def test_budget_exhaustion_stops_planning_without_hanging(self):
        second = requirement("r2")
        orchestrator, _, _ = build({"r1": artifact("a1"), "r2": artifact("a2")}, budget=1)
        result = orchestrator.run(objective(), (requirement(), second))
        self.assertEqual(result.completion_report.stop_reason, "BUDGET_EXHAUSTED")
        self.assertLessEqual(len(result.executions), 1)

    def test_max_rounds_is_a_hard_backstop(self):
        unsatisfiable = artifact("a1", row_count=0)
        orchestrator, _, _ = build({"r1": unsatisfiable}, max_rounds=2)
        result = orchestrator.run(objective(), (requirement(),))
        self.assertIn(result.completion_report.stop_reason, ("NO_PROGRESS", "MAX_ROUNDS"))
        self.assertLessEqual(result.completion_report.execution_summary.rounds, 2)

    def test_response_package_excludes_history_and_rejected_products(self):
        orchestrator, registry, _ = build({"r1": artifact(row_count=0)})
        result = orchestrator.run(objective(), (requirement(),))
        fields = set(result.response_package.model_dump())
        self.assertNotIn("attempts", fields)
        self.assertNotIn("assessments", fields)
        self.assertNotIn("planning_decisions", fields)
        # The rejected artifact is stored internally but never exposed as accepted evidence.
        self.assertIn("a1", [item.artifact_id for item in registry.artifacts()])
        self.assertEqual(result.response_package.accepted_evidence, ())

    def test_replanning_recovers_when_the_first_artifact_is_weak(self):
        class ImprovingTool:
            name = "tool"
            def __init__(self):
                self.calls = 0
            def execute(self, task):
                self.calls += 1
                if self.calls == 1:
                    return ToolResult(status="OK", artifact=artifact("a1", row_count=1))
                return ToolResult(status="OK", artifact=artifact("a2", row_count=1000))

        registry, counter = ArtifactRegistry(), iter(f"id-{i}" for i in range(1000))
        ids = lambda prefix: f"{prefix}-{next(counter)}"
        assessment = AssessmentService(registry, RuleBasedJudge(), id_factory=ids)
        router = Router((ToolCapability(tool="tool", source_kind="SYNTHETIC",
                                        supported_artifact_types=("TABLE",)),), id_factory=ids)
        executor = Executor({"tool": ImprovingTool()}, max_retries=0, id_factory=ids)
        orchestrator = Orchestrator(RuleBasedPlanner(id_factory=ids), router, executor, assessment,
                                    registry, max_rounds=3, budget=10, id_factory=ids)
        need = requirement("r1", evidence_purpose="DESCRIPTIVE", min_row_count=100)
        result = orchestrator.run(objective(), (need,))
        self.assertEqual(result.completion_report.stop_reason, "COMPLETE")
        self.assertEqual(result.response_package.accepted_evidence[0].artifact_ref, "a2")
        self.assertEqual(len(result.completion_report.final_artifact_refs), 1)


if __name__ == "__main__":
    unittest.main()
