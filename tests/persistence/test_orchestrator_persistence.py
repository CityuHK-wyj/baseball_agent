"""Orchestrator persistence: record, checkpoint, interrupt, resume, reuse.

No real database or network is used. The tools are deterministic fakes.
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
from app.models.artifacts import ArtifactAssessment, DeterministicResult, JudgeResult
from app.models.contracts import ObjectiveState, RequirementState
from app.models.planning import TaskExecution
from app.persistence.artifacts import LocalFilesystemArtifactStorage
from app.persistence.recorder import RunRecorder
from app.persistence.resume import ResumeService
from app.persistence.store import SqliteOperationalStore
from tests.factories import artifact, objective, requirement

RUN_ID = "run-1"


class RecordingTool:
    name = "tool"

    def __init__(self, item, payload: bytes | None = b'{"rows": 1200}'):
        self.item = item
        self.payload = payload
        self.calls = 0

    def execute(self, task):
        self.calls += 1
        return ToolResult(status="OK", artifact=self.item, payload=self.payload)


class ExplodingPlanner:
    def decide(self, context):
        raise AssertionError("the Planner must not be invoked for a terminal resumed run")


class FailingStorage(LocalFilesystemArtifactStorage):
    def put(self, *args, **kwargs):
        raise OSError("simulated storage failure")


def build_orchestrator(tool, recorder=None, planner=None, *, source_kind="SYNTHETIC",
                       permitted_sources=()):
    registry = ArtifactRegistry()
    counter = iter(f"id-{index}" for index in range(1000))
    ids = lambda prefix: f"{prefix}-{next(counter)}"
    assessment = AssessmentService(registry, RuleBasedJudge(), id_factory=ids)
    router = Router((ToolCapability(tool="tool", source_kind=source_kind,
                                    supported_artifact_types=("TABLE",)),), id_factory=ids)
    executor = Executor({"tool": tool}, max_retries=0, id_factory=ids)
    orchestrator = Orchestrator(planner or RuleBasedPlanner(id_factory=ids), router, executor,
                                assessment, registry, max_rounds=3, budget=10,
                                id_factory=ids, recorder=recorder, run_id=RUN_ID,
                                permitted_sources=permitted_sources)
    return orchestrator, registry, assessment


class OrchestratorPersistenceTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        root = Path(self._directory.name)
        self.store = SqliteOperationalStore(root / "operational.db")
        self.storage = LocalFilesystemArtifactStorage(root / "payloads")
        counter = iter(f"ckpt-{index}" for index in range(1000))
        self.recorder = RunRecorder(self.store, self.storage, id_factory=lambda _p: next(counter))
        self.resume = ResumeService(self.store)

    def tearDown(self):
        self.store.close()
        self._directory.cleanup()

    def test_full_run_persists_in_safe_order_and_takes_all_checkpoints(self):
        tool = RecordingTool(artifact("a1"))
        orchestrator, _, _ = build_orchestrator(tool, recorder=self.recorder)
        result = orchestrator.run(objective(), (requirement(),))

        self.assertTrue(self.storage.exists("a1"))
        metadata = self.store.get_object("artifact", "a1")
        self.assertEqual(metadata.payload["payload_ref"], str(self.storage.root / "a1"))
        self.assertEqual(len(self.store.list_objects("assessment", RUN_ID)), 1)
        self.assertEqual(self.store.get_object("requirement_state", "r1").payload["status"], "SATISFIED")
        self.assertEqual(self.store.get_object("objective_state", "o1").payload["status"], "COMPLETE")
        self.assertEqual(len(self.store.list_objects("execution", RUN_ID)), 1)
        self.assertEqual(len(self.store.list_objects("completion_report", RUN_ID)), 1)
        self.assertEqual(len(self.store.list_objects("response_package", RUN_ID)), 1)

        positions = [item.recovery_position for item in self.store.list_checkpoints(RUN_ID)]
        for expected in ("PLAN_ACCEPTED", "ARTIFACT_ASSESSED", "PLANNER_TERMINAL", "FINALIZATION"):
            self.assertIn(expected, positions)
        self.assertEqual(result.completion_report.stop_reason, "COMPLETE")

    def test_state_never_references_an_artifact_that_failed_to_persist(self):
        recorder = RunRecorder(self.store, FailingStorage(Path(self._directory.name) / "bad"))
        orchestrator, _, _ = build_orchestrator(RecordingTool(artifact("a1")), recorder=recorder)
        with self.assertRaises(OSError):
            orchestrator.run(objective(), (requirement(),))
        self.assertIsNone(self.store.get_object("artifact", "a1"))
        self.assertIsNone(self.store.get_object("requirement_state", "r1"))
        self.assertEqual(self.store.list_objects("assessment", RUN_ID), ())

    def _seed_interrupted_run(self, position: str = "PLAN_ACCEPTED", terminal: bool = False):
        self.recorder.record_artifact(RUN_ID, artifact("a1"), b'{"rows": 1200}')
        self.recorder.record_assessment(RUN_ID, ArtifactAssessment(
            assessment_id="s1", artifact_ref="a1", requirement_ref="r1",
            deterministic_result=DeterministicResult(),
            judge_result=JudgeResult(level="STRONG", rationale="aligned"),
            final_level="STRONG", assessment_summary="STRONG: aligned"))
        self.recorder.record_requirement_states(RUN_ID, {"r1": RequirementState(
            requirement_ref="r1", status="SATISFIED", assessment_refs=("s1",))})
        self.recorder.record_objective_state(RUN_ID, ObjectiveState(
            objective_ref="o1", status="COMPLETE", requirement_refs=("r1",), version=1))
        self.recorder.record_execution(RUN_ID, TaskExecution(
            execution_id="e1", task_ref="t1", status="RUNNING"), (), None)
        self.recorder.checkpoint(RUN_ID, position, active_work_refs=("e1",))

    def test_resume_reclassifies_interruption_and_reuses_artifacts_without_reexecution(self):
        self._seed_interrupted_run()
        plan = self.resume.build_plan(RUN_ID)
        self.assertEqual(plan.interrupted_execution_refs, ("e1",))
        self.assertEqual(plan.reusable_artifact_refs, ("a1",))

        restored = self.resume.rehydrate(RUN_ID)
        tool = RecordingTool(artifact("a2"))
        orchestrator, registry, _ = build_orchestrator(tool, recorder=self.recorder)
        result = orchestrator.run(objective(), (requirement(),), restored=restored)

        self.assertEqual(tool.calls, 0, "an already-satisfied requirement must not re-execute")
        self.assertIn("a1", result.completion_report.final_artifact_refs)
        self.assertNotIn("a2", [item.artifact_id for item in registry.artifacts()])
        self.assertEqual(result.completion_report.stop_reason, "COMPLETE")

    def test_planner_terminal_survives_persistence_and_resume(self):
        self._seed_interrupted_run(position="PLANNER_TERMINAL", terminal=True)
        self.assertTrue(self.resume.build_plan(RUN_ID).planner_terminal)

        restored = self.resume.rehydrate(RUN_ID)
        tool = RecordingTool(artifact("a2"))
        orchestrator, _, _ = build_orchestrator(tool, recorder=self.recorder, planner=ExplodingPlanner())
        result = orchestrator.run(objective(), (requirement(),), restored=restored)

        self.assertEqual(tool.calls, 0)
        self.assertEqual(result.completion_report.stop_reason, "COMPLETE")
        self.assertEqual(result.objective_state.status, "COMPLETE")

    def test_new_permitted_source_reopens_a_persisted_noncomplete_terminal_plan(self):
        blocked_tool = RecordingTool(artifact("a-blocked"))
        blocked, _, _ = build_orchestrator(
            blocked_tool, recorder=self.recorder, source_kind="POSTGRES",
            permitted_sources=("PARQUET",))
        first = blocked.run(objective(), (requirement(),))
        self.assertEqual(first.completion_report.stop_reason, "POLICY_BLOCKED")
        self.assertEqual(blocked_tool.calls, 0)

        restored = self.resume.rehydrate(RUN_ID)
        permitted_tool = RecordingTool(artifact("a-permitted"))
        resumed, _, _ = build_orchestrator(
            permitted_tool, recorder=self.recorder, source_kind="POSTGRES",
            permitted_sources=("POSTGRES",))
        result = resumed.run(objective(), (requirement(),), restored=restored)

        self.assertEqual(permitted_tool.calls, 1)
        self.assertEqual(result.objective_state.status, "COMPLETE")

    def test_no_checkpoint_means_no_rehydration(self):
        self.assertIsNone(self.resume.rehydrate(RUN_ID))


if __name__ == "__main__":
    unittest.main()
