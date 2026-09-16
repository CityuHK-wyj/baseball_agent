"""Crash recovery at the persistent pipeline boundary."""

import tempfile
import unittest
import subprocess
import sys
from pathlib import Path

from app.models.clarification import ClarificationAnswer
from app.persistence.artifacts import LocalFilesystemArtifactStorage
from app.persistence.recorder import RunRecorder
from app.persistence.store import SqliteOperationalStore
from tests.integration.test_end_to_end import pipeline, ambiguous_dictionary


class CrashResumeTests(unittest.TestCase):
    def test_abrupt_process_exit_after_execution_recovers_without_duplicate_fetch(self):
        worker = '''
import os, sys
from pathlib import Path
from app.pipeline import AnalysisPipeline
from app.persistence.store import SqliteOperationalStore
from app.persistence.recorder import RunRecorder
from app.persistence.artifacts import LocalFilesystemArtifactStorage
from app.models.evidence import RawWebResult
root = Path(sys.argv[1])
crash = sys.argv[2] == "crash"
class Store(SqliteOperationalStore):
    def save_object(self, kind, *args, **kwargs):
        if crash and kind == "assessment":
            os._exit(17)
        return super().save_object(kind, *args, **kwargs)
def fetch(task):
    with (root / "fetches").open("a") as log:
        log.write("fetch\\n")
    return RawWebResult(result_id=task.task_id, url="https://example.test/fixture",
        title="Fixture", source="fixture", text="Aaron Judge suffered an injury in 2025.")
store = Store(root / "operational.db")
pipeline = AnalysisPipeline.default(runtime_dir=root, web_fetcher=fetch,
    recorder=RunRecorder(store, LocalFilesystemArtifactStorage(root / "artifacts")))
result = pipeline.analyze("Judge injury", run_id="process") if crash else pipeline.resume_run("process")
assert result.objective_statuses == ("COMPLETE",)
pipeline.close()
store.close()
'''
        with tempfile.TemporaryDirectory() as directory:
            command = [sys.executable, "-c", worker, directory]
            first = subprocess.run([*command, "crash"], capture_output=True, text=True, timeout=30)
            self.assertEqual(first.returncode, 17, first.stderr)
            for _ in range(2):
                resumed = subprocess.run([*command, "resume"], capture_output=True, text=True, timeout=30)
                self.assertEqual(resumed.returncode, 0, resumed.stderr)
            self.assertEqual((Path(directory) / "fetches").read_text(), "fetch\n")

    def test_durable_execution_is_reused_if_outcome_index_write_crashes(self):
        from app.agent.executor import Executor
        from app.agent.routing import Router, ToolCapability
        from app.models.planning import AgentTask
        from app.tools.synthetic import SyntheticDataTool
        from tests.factories import requirement
        class Crash(BaseException):
            pass
        class CrashStore(SqliteOperationalStore):
            def save_object(self, kind, *args, **kwargs):
                if kind == "execution_outcome":
                    raise Crash()
                return super().save_object(kind, *args, **kwargs)
        task = AgentTask(task_id="task", objective_ref="o1", requirement_refs=("r1",), description="get EV")
        route = Router((ToolCapability(tool="synthetic", source_kind="SYNTHETIC",
                                       supported_artifact_types=("TABLE",)),)).route(task, "TABLE")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            storage = LocalFilesystemArtifactStorage(root / "artifacts")
            store = CrashStore(root / "operations.db")
            recorder = RunRecorder(store, storage)
            with self.assertRaises(Crash):
                recorder.execute_once("run", task, route, Executor({"synthetic": SyntheticDataTool((requirement(),))}))
            store.close()
            reopened = SqliteOperationalStore(root / "operations.db")
            self.addCleanup(reopened.close)
            # No registered tool: success must come from the durable execution.
            result = RunRecorder(reopened, storage).execute_once("run", task, route, Executor({}))
            self.assertEqual(result.execution.status, "SUCCEEDED")
            self.assertTrue(Path(result.artifact.payload_ref).is_file())

    def test_uncertain_external_execution_is_never_retried(self):
        from app.pipeline import AnalysisPipeline
        calls = []
        class Crash(BaseException):
            pass
        def fetch(task):
            calls.append(task.task_id)
            raise Crash()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subject = AnalysisPipeline.default(runtime_dir=root, web_fetcher=fetch)
            with self.assertRaises(Crash):
                subject.analyze("Judge injury", run_id="uncertain")
            subject.close()
            restarted = AnalysisPipeline.default(runtime_dir=root, web_fetcher=fetch)
            self.addCleanup(restarted.close)
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError, "EXECUTION_UNCERTAIN"):
                    restarted.resume_run("uncertain")
            self.assertEqual(len(calls), 1)

    def test_consumed_permission_and_revision_survive_process_restart(self):
        from app.agent.routing import Router, ToolCapability
        from app.models.contracts import CategoryConstraint
        from app.models.interaction import PermissionAnswer, ConstraintRevisionAnswer
        from tests.integration.test_end_to_end import judge_dictionary
        class Crash(BaseException):
            pass
        def crash_factory(requirements):
            raise Crash()
        for kind in ("permission", "revision"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                router = Router((ToolCapability(tool="synthetic", source_kind="SYNTHETIC",
                    supported_artifact_types=("TABLE",), cost="PAID" if kind == "permission" else "FREE"),))
                store = SqliteOperationalStore(root / "operations.db")
                subject = pipeline(judge_dictionary(), router=router, tool_factory=crash_factory,
                    recorder=RunRecorder(store, LocalFilesystemArtifactStorage(root / "artifacts")))
                constraints = (CategoryConstraint(key="source", values=("POSTGRES",)),) if kind == "revision" else ()
                waiting = subject.analyze("Judge performance", constraints=constraints, run_id=kind)
                with self.assertRaises(Crash):
                    if kind == "permission":
                        subject.resume_permission(kind, PermissionAnswer(
                            permission_ref=waiting.permissions[0].permission_id, approved=True))
                    else:
                        subject.resume_constraint_revision(kind, ConstraintRevisionAnswer(
                            revision_ref=waiting.constraint_revisions[0].revision_id, accepted=True))
                definitions = RunRecorder(store, LocalFilesystemArtifactStorage(root / "artifacts")).initial_definitions(kind)
                store.close()
                reopened = SqliteOperationalStore(root / "operations.db")
                try:
                    recorder = RunRecorder(reopened, LocalFilesystemArtifactStorage(root / "artifacts"))
                    restarted = pipeline(judge_dictionary(), router=router, recorder=recorder)
                    done = restarted.resume_run(kind)
                    self.assertEqual(done.objective_statuses, ("COMPLETE",))
                    self.assertEqual(recorder.initial_definitions(kind), definitions)
                finally:
                    reopened.close()

    def test_restart_after_artifact_creation_reuses_execution_without_refetch(self):
        from app.pipeline import AnalysisPipeline
        from app.models.evidence import RawWebResult
        class Crash(BaseException):
            pass
        class CrashStore(SqliteOperationalStore):
            def save_object(self, kind, *args, **kwargs):
                if kind == "assessment":
                    raise Crash()
                return super().save_object(kind, *args, **kwargs)
        calls = []
        def fetch(task):
            calls.append(task.task_id)
            return RawWebResult(result_id=task.task_id, url="https://example.test/injury",
                source="fixture", title="Injury fixture",
                text="Aaron Judge suffered an injury in 2025 and was placed on the injured list.")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = CrashStore(root / "operations.db")
            subject = AnalysisPipeline.default(runtime_dir=root, web_fetcher=fetch, recorder=RunRecorder(
                store, LocalFilesystemArtifactStorage(root / "artifacts")))
            try:
                with self.assertRaises(Crash):
                    subject.analyze("Judge injury", run_id="artifact-crash")
                self.assertEqual(len(calls), 1)
            finally:
                subject.close()
                store.close()
            reopened = SqliteOperationalStore(root / "operations.db")
            self.addCleanup(reopened.close)
            restarted = AnalysisPipeline.default(runtime_dir=root, web_fetcher=fetch, recorder=RunRecorder(
                reopened, LocalFilesystemArtifactStorage(root / "artifacts")))
            self.addCleanup(restarted.close)
            result = restarted.resume_run("artifact-crash")
            self.assertEqual(result.objective_statuses, ("COMPLETE",))
            self.assertEqual(len(calls), 1)
            repeated = restarted.resume_run("artifact-crash")
            self.assertEqual(repeated.objective_statuses, ("COMPLETE",))
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(repeated.response_packages[0].accepted_evidence), 1)
            self.assertEqual(repeated.runs[0].executions, ())

    def test_consumed_clarification_resumes_after_restart_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SqliteOperationalStore(root / "operations.db")
            recorder = RunRecorder(store, LocalFilesystemArtifactStorage(root / "artifacts"))
            subject = pipeline(ambiguous_dictionary(), recorder=recorder)
            waiting = subject.analyze("Hernandez performance", run_id="restart")
            request = waiting.clarifications[0]
            pending = recorder.load_interaction("restart")
            # Reproduce a consumed answer with its confirmed intent durably recorded.
            from app.models.contracts import Entity, CategoryConstraint
            confirmed = CategoryConstraint(key="entity_key", values=(request.options[0].value,),
                                           origin="USER_CONFIRMED")
            objective = pending.objectives[0].model_copy(update={
                "entities": (Entity(namespace="MLBAM", entity_type="PLAYER", identifier="1"),),
                "constraints": (confirmed,)})
            recorder.consume_interaction(pending, pending.model_copy(update={
                "status": "CONFIRMED", "objectives": (objective,), "confirmed_constraints": (confirmed,)}))
            store.close()
            reopened = SqliteOperationalStore(root / "operations.db")
            self.addCleanup(reopened.close)
            restarted = pipeline(ambiguous_dictionary(), recorder=RunRecorder(
                reopened, LocalFilesystemArtifactStorage(root / "artifacts")))
            result = restarted.resume_run("restart")
            self.assertEqual(result.run_ids, ("restart",))
            self.assertEqual(result.objective_statuses, ("COMPLETE",))
            self.assertEqual(result.objectives, (objective,))
