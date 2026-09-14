"""ContextService wiring: bounded context for the Planner, knowledge-only for Response.

No attempt history, rejected evidence, plans or routing experiments may cross these
boundaries.
"""

import tempfile
import unittest
from pathlib import Path

from app.agent.executor import Executor, ToolResult
from app.agent.orchestrator import Orchestrator
from app.agent.planner import RuleBasedPlanner
from app.agent.registry import ArtifactRegistry
from app.agent.routing import Router, ToolCapability
from app.assessment.judge import RuleBasedJudge
from app.assessment.service import AssessmentService
from app.context.service import ContextItem, ContextRequest, ContextService, StaticContextSource
from app.persistence.artifacts import LocalFilesystemArtifactStorage
from app.persistence.recorder import RunRecorder
from app.persistence.store import SqliteOperationalStore
from tests.factories import artifact, objective, requirement


def item(item_id: str, kind: str, **changes) -> ContextItem:
    fields = dict(item_id=item_id, kind=kind, title=f"title-{item_id}", source="source", content="body")
    return ContextItem(**(fields | changes))


class CapturingPlanner(RuleBasedPlanner):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.contexts = []

    def decide(self, context):
        self.contexts.append(context)
        return super().decide(context)


class OneShotTool:
    name = "tool"

    def __init__(self, item):
        self.item = item
        self.calls = 0

    def execute(self, task):
        self.calls += 1
        return ToolResult(status="OK", artifact=self.item, payload=b"{}")


def build(tool, planner, context_service=None, recorder=None, run_id="run-1"):
    registry = ArtifactRegistry()
    counter = iter(f"id-{index}" for index in range(1000))
    ids = lambda prefix: f"{prefix}-{next(counter)}"
    assessment = AssessmentService(registry, RuleBasedJudge(), id_factory=ids)
    router = Router((ToolCapability(tool="tool", source_kind="SYNTHETIC",
                                    supported_artifact_types=("TABLE",)),), id_factory=ids)
    executor = Executor({"tool": tool}, max_retries=0, id_factory=ids)
    orchestrator = Orchestrator(planner, router, executor, assessment, registry,
                                max_rounds=3, budget=10, id_factory=ids,
                                context_service=context_service, recorder=recorder, run_id=run_id)
    return orchestrator, registry


def knowledge_context() -> ContextService:
    return ContextService((
        StaticContextSource("METRIC", (item("m1", "METRIC"),)),
        StaticContextSource("REFERENCE", (item("ref1", "REFERENCE"),)),
        StaticContextSource("ATTEMPT", (item("attempt-history", "ATTEMPT"),)),
        StaticContextSource("PLAN", (item("old-plan", "PLAN"),)),
        StaticContextSource("REJECTED_EVIDENCE", (item("rejected-1", "REJECTED_EVIDENCE"),)),
    ))


class PlannerContextWiringTests(unittest.TestCase):
    def test_planner_receives_bounded_context_and_summaries_not_history(self):
        planner = CapturingPlanner(id_factory=lambda _p: "decision")
        orchestrator, _ = build(OneShotTool(artifact("a1")), planner, knowledge_context())
        orchestrator.run(objective(), (requirement(),))

        all_items = [entry.item_id for context in planner.contexts for entry in context.context_items]
        self.assertIn("m1", all_items)
        self.assertIn("ref1", all_items)
        self.assertNotIn("attempt-history", all_items)
        self.assertNotIn("rejected-1", all_items)
        self.assertTrue(any(context.assessment_summaries for context in planner.contexts),
                        "the Planner must see assessment summaries")
        self.assertTrue(any(context.requirement_states for context in planner.contexts))
        self.assertTrue(all(len(context.context_items) <= 8 for context in planner.contexts))
        self.assertTrue(any(context.execution_summary.rounds >= 1 for context in planner.contexts))

    def test_planner_context_is_scoped_to_the_run(self):
        scoped = ContextService((StaticContextSource("REFERENCE", (
            ContextItem(item_id="run2-only", kind="REFERENCE", title="private",
                        source="reports", scope_run="run-2"),)),))

        own_planner = CapturingPlanner(id_factory=lambda _p: "decision")
        own, _ = build(OneShotTool(artifact("a1")), own_planner, scoped, run_id="run-2")
        own.run(objective(), (requirement(),))
        self.assertIn("run2-only", [e.item_id for c in own_planner.contexts for e in c.context_items])

        other_planner = CapturingPlanner(id_factory=lambda _p: "decision")
        other, _ = build(OneShotTool(artifact("a2")), other_planner, scoped, run_id="run-1")
        other.run(objective(), (requirement(),))
        self.assertNotIn("run2-only", [e.item_id for c in other_planner.contexts for e in c.context_items])


class ResponseContextWiringTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        root = Path(self._directory.name)
        self.store = SqliteOperationalStore(root / "operational.db")
        counter = iter(f"id-{index}" for index in range(100))
        self.recorder = RunRecorder(self.store, LocalFilesystemArtifactStorage(root / "payloads"),
                                    id_factory=lambda _p: next(counter))

    def tearDown(self):
        self.store.close()
        self._directory.cleanup()

    def test_response_receives_accepted_evidence_and_critical_knowledge_only(self):
        planner = RuleBasedPlanner(id_factory=lambda _p: "decision")
        orchestrator, _ = build(OneShotTool(artifact("a1")), planner, knowledge_context(),
                                recorder=self.recorder)
        result = orchestrator.run(objective(), (requirement(),))
        package = result.response_package

        self.assertEqual([entry.artifact_ref for entry in package.accepted_evidence], ["a1"])
        self.assertIn("METRIC:m1", package.critical_shared_knowledge)
        self.assertIn("REFERENCE:ref1", package.critical_shared_knowledge)
        self.assertNotIn("PLAN:old-plan", package.critical_shared_knowledge)
        self.assertNotIn("ATTEMPT:attempt-history", package.critical_shared_knowledge)
        self.assertNotIn("REJECTED_EVIDENCE:rejected-1", package.critical_shared_knowledge)

    def test_rejected_artifact_never_reaches_the_response(self):
        planner = RuleBasedPlanner(id_factory=lambda _p: "decision")
        orchestrator, _ = build(OneShotTool(artifact("a1", row_count=0)), planner, knowledge_context())
        result = orchestrator.run(objective(), (requirement(),))
        package = result.response_package
        self.assertEqual(package.accepted_evidence, ())
        self.assertNotIn("a1", [entry.artifact_ref for entry in package.accepted_evidence])
        # Knowledge can still be listed; rejected evidence cannot.
        self.assertTrue(all(not knowledge.startswith("REJECTED_EVIDENCE")
                            for knowledge in package.critical_shared_knowledge))


if __name__ == "__main__":
    unittest.main()
