"""Adversarial and boundary tests across the seams a reviewer will probe.

Complements the per-module tests; each case targets a specific invariant from the brief.
"""

import tempfile
import unittest
from pathlib import Path

from app.agent.executor import Executor, ToolResult
from app.agent.orchestrator import Orchestrator
from app.agent.planner import PlannerContext, RuleBasedPlanner
from app.agent.registry import ArtifactRegistry
from app.agent.routing import Router, ToolCapability
from app.assessment.judge import RuleBasedJudge
from app.assessment.service import AssessmentService
from app.context.service import ContextItem, ContextRequest, ContextService, StaticContextSource
from app.llm.planner import LLMPlanner
from app.llm.provider import FakeModelProvider
from app.models.contracts import AnalysisObjective, ArtifactRequirement, RequirementState
from app.models.planning import PlanningDecision
from app.persistence.artifacts import LocalFilesystemArtifactStorage
from app.persistence.recorder import RunRecorder
from app.persistence.resume import ResumeService
from app.persistence.store import SqliteOperationalStore
from app.validation.sql_guard import guard_read_only_sql
from tests.factories import artifact, objective, requirement


class PlannerInvariantTests(unittest.TestCase):
    def test_planning_decision_cannot_edit_a_requirement_definition(self):
        # No field on the decision can change a requirement's identity, criticality or
        # descriptor: the Planner can only reference requirement ids.
        fields = set(PlanningDecision.model_fields)
        self.assertFalse({"requirements", "initial_requirements", "base_criticality",
                          "descriptor", "objective"} & fields)
        baseline = requirement()
        with self.assertRaises(Exception):
            baseline.base_criticality = "OPTIONAL"
        with self.assertRaises(Exception):
            baseline.descriptor.data_keys = ()

    def test_llm_planner_cannot_reference_or_invent_a_requirement(self):
        context = PlannerContext(
            objective=objective(), requirements=(requirement(),),
            requirement_states=(RequirementState(requirement_ref="r1"),),
            recoverable_gaps=("r1",))
        provider = FakeModelProvider(['{"kind":"PLAN","tasks":[{"requirement_refs":["invented"],'
                                      '"description":"x"}],"rationale":"x"}'])
        with self.assertRaises(ValueError):
            LLMPlanner(provider, "m", id_factory=lambda _p: "id").decide(context)


class SqlAdversarialTests(unittest.TestCase):
    def test_write_hidden_in_a_cte_is_rejected(self):
        statements = [
            "WITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x",
            "WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x",
            "WITH x AS (SELECT 1) SELECT * FROM x; DROP TABLE t",
            "SELECT * FROM (SELECT 1) x; UPDATE t SET a = 1",
        ]
        for sql in statements:
            with self.subTest(sql=sql):
                self.assertFalse(guard_read_only_sql(sql, dialect="duckdb").allowed)

    def test_allowed_cte_is_still_allowed(self):
        self.assertTrue(guard_read_only_sql(
            "WITH x AS (SELECT 1 AS a) SELECT a FROM x", dialect="duckdb").allowed)


class RejectedEvidenceExclusionTests(unittest.TestCase):
    def _run(self, artifacts_by_requirement, needs):
        registry = ArtifactRegistry()
        counter = iter(range(1, 1000))
        ids = lambda prefix: f"{prefix}-{next(counter)}"
        assessment = AssessmentService(registry, RuleBasedJudge(), id_factory=ids)
        router = Router((ToolCapability(tool="tool", source_kind="SYNTHETIC",
                                        supported_artifact_types=("TABLE",)),), id_factory=ids)

        class Tool:
            name = "tool"

            def execute(self, task):
                item = artifacts_by_requirement.get(task.requirement_refs[0])
                return ToolResult(status="OK", artifact=item, payload=b"{}") if item else ToolResult(status="EMPTY")

        executor = Executor({"tool": Tool()}, max_retries=0, id_factory=ids)
        orchestrator = Orchestrator(RuleBasedPlanner(id_factory=ids), router, executor, assessment,
                                    registry, max_rounds=3, budget=10, id_factory=ids)
        return orchestrator.run(objective(), needs)

    def test_only_accepted_artifacts_reach_the_response(self):
        good = requirement("r1")
        bad = requirement("r2")
        result = self._run({"r1": artifact("a1"), "r2": artifact("a2", row_count=0)}, (good, bad))
        accepted = [item.artifact_ref for item in result.response_package.accepted_evidence]
        self.assertIn("a1", accepted)
        self.assertNotIn("a2", accepted)
        # The rejected artifact is still tracked internally, not silently dropped.
        self.assertTrue(any(item.final_level == "REJECT" for item in result.assessments))


class ResumeIntegrityTests(unittest.TestCase):
    def test_checkpoint_with_a_missing_artifact_does_not_invent_one(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SqliteOperationalStore(root / "operational.db")
            try:
                recorder = RunRecorder(store, LocalFilesystemArtifactStorage(root / "payloads"),
                                       id_factory=lambda _p: "id")
                # A state references artifact a1, but no artifact object was ever stored.
                recorder.record_requirement_states("run-1", {"r1": RequirementState(
                    requirement_ref="r1", status="SATISFIED", artifact_refs=("a1",))})
                recorder.checkpoint("run-1", "ARTIFACT_ASSESSED")
                service = ResumeService(store)
                plan = service.build_plan("run-1")
                self.assertEqual(plan.reusable_artifact_refs, ())
                restored = service.rehydrate("run-1")
                self.assertEqual(restored.artifacts, ())
            finally:
                store.close()


class ContextIsolationAdversarialTests(unittest.TestCase):
    def test_history_never_reaches_either_boundary(self):
        service = ContextService((
            StaticContextSource("ATTEMPT", (ContextItem(item_id="attempt-1", kind="ATTEMPT",
                                                         title="t", source="s"),)),
            StaticContextSource("REJECTED_EVIDENCE", (ContextItem(item_id="rej-1",
                                                                  kind="REJECTED_EVIDENCE",
                                                                  title="t", source="s"),)),
            StaticContextSource("PLAN", (ContextItem(item_id="plan-1", kind="PLAN",
                                                     title="t", source="s"),)),
            StaticContextSource("METRIC", (ContextItem(item_id="m1", kind="METRIC",
                                                       title="t", source="s"),)),
        ))
        for purpose in ("PLANNER", "RESPONSE"):
            with self.subTest(purpose=purpose):
                ids = {item.item_id for item in
                       service.retrieve(ContextRequest(request_id="q", purpose=purpose)).items}
                self.assertNotIn("attempt-1", ids)
                self.assertNotIn("rej-1", ids)
                self.assertIn("m1", ids)
                if purpose == "RESPONSE":
                    # Superseded plans must not reach the Response; the Planner owns plans.
                    self.assertNotIn("plan-1", ids)
                else:
                    self.assertIn("plan-1", ids)


class InfiniteLoopTests(unittest.TestCase):
    def test_planner_is_not_recalled_forever_when_a_tool_returns_nothing(self):
        registry = ArtifactRegistry()
        counter = iter(range(1, 1000))
        ids = lambda prefix: f"{prefix}-{next(counter)}"
        assessment = AssessmentService(registry, RuleBasedJudge(), id_factory=ids)
        router = Router((ToolCapability(tool="tool", source_kind="SYNTHETIC",
                                        supported_artifact_types=("TABLE",)),), id_factory=ids)

        class CountingPlanner(RuleBasedPlanner):
            def __init__(self):
                super().__init__(id_factory=ids)
                self.calls = 0

            def decide(self, context):
                self.calls += 1
                return super().decide(context)

        class EmptyTool:
            name = "tool"

            def execute(self, task):
                return ToolResult(status="EMPTY")

        planner = CountingPlanner()
        executor = Executor({"tool": EmptyTool()}, max_retries=0, id_factory=ids)
        orchestrator = Orchestrator(planner, router, executor, assessment, registry,
                                    max_rounds=5, budget=10, id_factory=ids)
        result = orchestrator.run(objective(), (requirement(),))
        self.assertEqual(result.completion_report.stop_reason, "NO_PROGRESS")
        self.assertLessEqual(planner.calls, 2, "planning must not loop without progress")


if __name__ == "__main__":
    unittest.main()
