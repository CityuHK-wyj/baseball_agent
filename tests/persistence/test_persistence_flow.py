"""End-to-end persistence vertical slice: record, checkpoint, resume."""

import tempfile
import unittest
from pathlib import Path

from app.models.artifacts import ArtifactAssessment, DeterministicResult, JudgeResult
from app.models.contracts import ObjectiveState, RequirementState
from app.models.reports import CompletionReport, ResponsePackage
from app.persistence.artifacts import LocalFilesystemArtifactStorage
from app.persistence.recorder import RunRecorder
from app.persistence.resume import ResumeService
from app.persistence.store import SqliteOperationalStore
from tests.factories import artifact


class PersistenceFlowTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.store = SqliteOperationalStore(Path(self._directory.name) / "operational.db")
        self.storage = LocalFilesystemArtifactStorage(Path(self._directory.name) / "payloads")
        counter = iter(f"id-{index}" for index in range(100))
        self.recorder = RunRecorder(self.store, self.storage, id_factory=lambda _p: next(counter))
        self.resume = ResumeService(self.store)

    def tearDown(self):
        self.store.close()
        self._directory.cleanup()

    def test_recorded_artifact_is_stored_before_state_references_it(self):
        self.recorder.record_artifact("run-1", artifact("a1"), b'{"rows": 3}')
        self.recorder.record_requirement_states("run-1", {"r1": RequirementState(
            requirement_ref="r1", status="SATISFIED", assessment_refs=("s1",))})
        self.recorder.checkpoint("run-1", "ARTIFACT_ASSESSED")

        self.assertTrue(self.storage.exists("a1"))
        checkpoint = self.store.latest_checkpoint("run-1")
        self.assertEqual(checkpoint.recovery_position, "ARTIFACT_ASSESSED")
        self.assertIn("requirement_state:r1:v0", checkpoint.state_version_refs)

    def test_finalization_products_survive_and_resume_is_terminal(self):
        self.recorder.record_artifact("run-1", artifact("a1"), b'{"rows": 3}')
        self.recorder.record_assessment("run-1", ArtifactAssessment(
            assessment_id="s1", artifact_ref="a1", requirement_ref="r1",
            deterministic_result=DeterministicResult(),
            judge_result=JudgeResult(level="STRONG", rationale="aligned"),
            final_level="STRONG", assessment_summary="STRONG: aligned"))
        self.recorder.record_requirement_states("run-1", {"r1": RequirementState(
            requirement_ref="r1", status="SATISFIED", assessment_refs=("s1",))})
        self.recorder.record_objective_state("run-1", ObjectiveState(
            objective_ref="o1", status="COMPLETE", requirement_refs=("r1",), version=1))
        report = CompletionReport(run_id="run-1", query="q", objective_ref="o1",
                                  objective_status="COMPLETE", stop_reason="COMPLETE")
        package = ResponsePackage(run_id="run-1", objective_ref="o1", objective_status="COMPLETE")
        self.recorder.record_completion_report("run-1", report)
        self.recorder.record_response_package("run-1", package)
        self.recorder.checkpoint("run-1", "FINALIZATION")

        plan = self.resume.build_plan("run-1")
        self.assertTrue(plan.planner_terminal)
        self.assertEqual(plan.reusable_artifact_refs, ("a1",))
        self.assertEqual(self.store.get_object("completion_report", "run-1").payload["stop_reason"], "COMPLETE")
        self.assertEqual(self.store.get_object("response_package", "run-1").payload["objective_status"], "COMPLETE")

    def test_resuming_twice_is_stable(self):
        from app.models.planning import TaskExecution
        self.recorder.record_execution("run-1", TaskExecution(
            execution_id="e1", task_ref="t1", status="RUNNING"), (), None)
        self.recorder.checkpoint("run-1", "PLAN_ACCEPTED")
        first = self.resume.build_plan("run-1")
        second = self.resume.build_plan("run-1")
        self.assertEqual(first.interrupted_execution_refs, ("e1",))
        self.assertEqual(second.interrupted_execution_refs, (), "already reclassified")
        self.assertEqual(second.recovery_position, "PLAN_ACCEPTED")


if __name__ == "__main__":
    unittest.main()
