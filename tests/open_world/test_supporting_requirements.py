"""The Planner can create supporting requirements mid-run (dynamic web research)."""

import json
import unittest

from app.agent.executor import Executor
from app.agent.orchestrator import Orchestrator
from app.agent.planner import PlannerContext
from app.agent.registry import ArtifactRegistry
from app.agent.routing import Router, ToolCapability
from app.assessment.judge import RuleBasedJudge
from app.assessment.service import AssessmentService
from app.llm.planner import LLMPlanner
from app.llm.provider import FakeModelProvider
from app.models.artifacts import Artifact, Provenance
from app.models.contracts import ArtifactDescriptor
from app.models.planning import AgentTask, PlanningDecision, SupportingNeed
from app.tools.results import ToolResult
from tests.factories import objective, requirement


class _EvidenceTool:
    name = "web-tool"

    def execute(self, task):
        artifact = Artifact(
            artifact_id="web-e1",
            descriptor=ArtifactDescriptor(artifact_type="EVIDENCE", data_keys=("entity_alias",),
                                          granularity="event", population_scope="web"),
            payload_ref="web://e1",
            provenance=Provenance(source="web", source_kind="WEB", reference="https://x.test"),
            row_count=1, text_content="太鼓达人 refers to a known MLB player.")
        return ToolResult.ok(1, artifact=artifact)


class _NeedPlanner:
    def __init__(self) -> None:
        self.calls = 0

    def decide(self, context: PlannerContext) -> PlanningDecision:
        self.calls += 1
        if self.calls == 1:
            need = SupportingNeed(
                need_id="need-1", objective_ref=context.objective.objective_id,
                description="Resolve the nickname via web research",
                artifact_type="EVIDENCE", data_keys=("entity_alias",),
                task_type="ENTITY_RESOLUTION", objective="Identify the nickname",
                search_hints=("太鼓达人 MLB",))
            task = AgentTask(task_id="t1", objective_ref=context.objective.objective_id,
                             requirement_refs=("supporting-need-1",),
                             description="Web entity research", task_type="ENTITY_RESOLUTION",
                             search_hints=("太鼓达人 MLB",))
            return PlanningDecision(decision_id="d1", objective_ref=context.objective.objective_id,
                                    kind="PLAN", tasks=(task,), supporting_needs=(need,),
                                    rationale="Create a web research supporting requirement.")
        return PlanningDecision(decision_id=f"d{self.calls}",
                                objective_ref=context.objective.objective_id,
                                kind="STOP_PLANNING", planner_terminal=True,
                                terminal_reason="NO_RECOVERABLE_PATH",
                                rationale="No further local path.")


class SupportingRequirementTests(unittest.TestCase):
    def test_planner_created_supporting_requirement_is_executed(self):
        ids = iter(f"id-{index}" for index in range(1000))
        id_factory = lambda prefix: f"{prefix}-{next(ids)}"  # noqa: E731
        capabilities = (
            ToolCapability(tool="synthetic", source_kind="SYNTHETIC",
                           supported_artifact_types=("TABLE",)),
            ToolCapability(tool="web-tool", source_kind="WEB",
                           supported_artifact_types=("EVIDENCE",),
                           supported_data_keys=("entity_alias",)),
        )
        router = Router(capabilities, id_factory=id_factory)
        executor = Executor({"web-tool": _EvidenceTool()}, max_retries=0, id_factory=id_factory)
        registry = ArtifactRegistry()
        assessment = AssessmentService(registry, RuleBasedJudge(), id_factory=id_factory)
        orchestrator = Orchestrator(_NeedPlanner(), router, executor, assessment, registry,
                                    max_rounds=3, budget=5, id_factory=id_factory)
        result = orchestrator.run(objective(), (requirement(),))
        added = [item for item in result.completion_report.requirement_completion
                 if item.origin == "PLANNER_ADDED"]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0].status, "SATISFIED")
        self.assertEqual(len(result.executions), 1)
        self.assertEqual(result.executions[0].execution.status, "SUCCEEDED")


class LLMSupportingNeedParsingTests(unittest.TestCase):
    def test_llm_planner_parses_supporting_needs_and_referencing_task(self):
        context = PlannerContext(objective=objective(), requirements=(requirement(),),
                                 web_recovery_available=True)
        provider = FakeModelProvider([json.dumps({
            "kind": "PLAN",
            "tasks": [{"requirement_refs": ["supporting-need-1"], "description": "research",
                       "task_type": "ENTITY_RESOLUTION"}],
            "supporting_needs": [{"need_id": "need-1", "description": "resolve nickname",
                                  "artifact_type": "EVIDENCE", "data_keys": ["entity_alias"],
                                  "task_type": "ENTITY_RESOLUTION",
                                  "search_hints": ["太鼓达人 MLB"]}],
            "planner_terminal": False, "terminal_reason": "", "rationale": "web recovery"})])
        decision = LLMPlanner(provider, "test-model",
                              id_factory=lambda p: f"{p}-1").decide(context)
        self.assertEqual(len(decision.supporting_needs), 1)
        self.assertEqual(decision.tasks[0].requirement_refs, ("supporting-need-1",))


if __name__ == "__main__":
    unittest.main()
