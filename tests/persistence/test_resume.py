import tempfile
import unittest
from pathlib import Path

from app.models.contracts import RequirementState
from app.models.planning import TaskExecution
from app.persistence.artifacts import LocalFilesystemArtifactStorage
from app.persistence.recorder import RunRecorder
from app.persistence.resume import ResumeService
from app.persistence.store import SqliteOperationalStore
from tests.factories import artifact, requirement


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.store = SqliteOperationalStore(":memory:")
        self.storage = LocalFilesystemArtifactStorage(Path(self._directory.name))
        self.recorder = RunRecorder(self.store, self.storage)

    def tearDown(self):
        self.store.close()
        self._directory.cleanup()

    def test_artifact_payload_and_metadata_are_persisted_together(self):
        item = artifact("a1")
        stored = self.recorder.record_artifact("run-1", item, b'{"rows": 3}')
        self.assertEqual(self.storage.get(stored), b'{"rows": 3}')
        record = self.store.get_object("artifact", "a1")
        self.assertEqual(record.payload["artifact_id"], "a1")
        self.assertEqual(record.payload["payload_ref"], stored.location)
        self.assertEqual(record.run_id, "run-1")

    def test_state_is_referenced_after_the_artifact_exists(self):
        self.recorder.record_artifact("run-1", artifact("a1"), b"{}")
        self.recorder.record_requirement_states("run-1", {"r1": RequirementState(
            requirement_ref="r1", status="SATISFIED", assessment_refs=("s1",))})
        checkpoint = self.recorder.checkpoint("run-1", "ARTIFACT_ASSESSED")
        self.assertIn("requirement_state:r1:v0", checkpoint.state_version_refs)
        self.assertTrue(all(":" in ref for ref in checkpoint.state_version_refs))

    def test_execution_history_is_persisted(self):
        execution = TaskExecution(execution_id="e1", task_ref="t1", status="RUNNING")
        self.recorder.record_execution("run-1", execution, (), None)
        record = self.store.get_object("execution", "e1")
        self.assertEqual(record.payload["execution"]["status"], "RUNNING")


class ResumeServiceTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.store = SqliteOperationalStore(":memory:")
        self.storage = LocalFilesystemArtifactStorage(Path(self._directory.name))
        self.recorder = RunRecorder(self.store, self.storage)
        self.resume = ResumeService(self.store)

    def tearDown(self):
        self.store.close()
        self._directory.cleanup()

    def test_without_a_checkpoint_the_run_is_not_resumable(self):
        plan = self.resume.build_plan("run-unknown")
        self.assertEqual(plan.status, "NO_CHECKPOINT")
        self.assertEqual(plan.recovery_position, "")

    def test_running_executions_are_reclassified_as_interrupted_and_artifacts_reused(self):
        self.recorder.record_artifact("run-1", artifact("a1"), b"{}")
        self.recorder.record_execution(
            "run-1", TaskExecution(execution_id="e1", task_ref="t1", status="RUNNING"), (), None)
        self.recorder.record_execution(
            "run-1", TaskExecution(execution_id="e2", task_ref="t2", status="SUCCEEDED",
                                   artifact_refs=("a1",)), (), None)
        self.recorder.checkpoint("run-1", "PLAN_ACCEPTED", active_work_refs=("e1",))

        plan = self.resume.build_plan("run-1")
        self.assertEqual(plan.status, "RESUMABLE")
        self.assertEqual(plan.recovery_position, "PLAN_ACCEPTED")
        self.assertEqual(plan.interrupted_execution_refs, ("e1",))
        self.assertEqual(plan.reusable_artifact_refs, ("a1",))
        self.assertEqual(plan.pending_request_refs, ())
        # The reclassification is persisted so a second resume is stable.
        persisted = self.store.get_object("execution", "e1").payload["execution"]["status"]
        self.assertEqual(persisted, "INTERRUPTED")
        self.assertNotIn("e2", self.resume.build_plan("run-1").interrupted_execution_refs)

    def test_planner_terminal_survives_resume(self):
        self.recorder.checkpoint("run-1", "PLANNER_TERMINAL")
        plan = self.resume.build_plan("run-1")
        self.assertTrue(plan.planner_terminal)
        self.assertEqual(plan.recovery_position, "PLANNER_TERMINAL")

    def test_pending_user_request_is_reported(self):
        self.recorder.checkpoint("run-1", "WAITING_FOR_USER", pending_request_refs=("q1",))
        plan = self.resume.build_plan("run-1")
        self.assertEqual(plan.pending_request_refs, ("q1",))


if __name__ == "__main__":
    unittest.main()
