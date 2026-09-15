"""End-to-end vertical flow: query -> semantic -> requirements -> plan -> route ->
execute -> artifact -> validate/judge -> state -> completion -> response.

Offline: a synthetic tool stands in for real sources. No database or network.
"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.agent.planner import RuleBasedPlanner
from app.agent.registry import ArtifactRegistry
from app.agent.routing import Router, ToolCapability
from app.assessment.judge import RuleBasedJudge
from app.assessment.service import AssessmentService
from app.llm.response import DeterministicResponseComposer
from app.models.entities import CanonicalEntity
from app.models.clarification import ClarificationAnswer
from app.models.interaction import PermissionAnswer
from app.models.contracts import CategoryConstraint
from app.persistence.artifacts import LocalFilesystemArtifactStorage
from app.persistence.recorder import RunRecorder
from app.persistence.resume import ResumeService
from app.persistence.store import SqliteOperationalStore
from app.pipeline import AnalysisPipeline, default_tool_factory
from app.semantic.entity_resolver import EntityDictionary, EntityResolver
from app.semantic.normalizer import SemanticNormalizer
from app.semantic.objective_extractor import RuleBasedObjectiveExtractor
from app.semantic.requirement_decomposer import RuleBasedRequirementDecomposer


def pipeline(dictionary: EntityDictionary, *, row_count: int = 1200,
             recorder: RunRecorder | None = None, router: Router | None = None) -> AnalysisPipeline:
    counter = iter(range(1, 100_000))
    ids = lambda prefix: f"{prefix}-{next(counter)}"
    resolver = EntityResolver(dictionary, id_factory=ids)
    semantic = SemanticNormalizer(RuleBasedObjectiveExtractor(id_factory=ids), resolver,
                                  dictionary, id_factory=ids)
    registry = ArtifactRegistry()
    assessment = AssessmentService(registry, RuleBasedJudge(), id_factory=ids)
    router = router or Router((ToolCapability(tool="synthetic", source_kind="SYNTHETIC",
                                               supported_artifact_types=("TABLE", "EVIDENCE", "FEATURE")),),
                              id_factory=ids)
    return AnalysisPipeline(
        semantic, RuleBasedRequirementDecomposer(id_factory=ids),
        RuleBasedPlanner(id_factory=ids, max_rounds=3), router, assessment, registry,
        tool_factory=default_tool_factory(row_count), recorder=recorder,
        response_composer=DeterministicResponseComposer(), max_rounds=3, budget=10, id_factory=ids)


def judge_dictionary() -> EntityDictionary:
    return EntityDictionary((
        CanonicalEntity(entity_key="MLBAM:592450", entity_type="PLAYER",
                        display_name="Aaron Judge", aliases=("Judge", "交通指挥员")),
    ))


def ambiguous_dictionary() -> EntityDictionary:
    return EntityDictionary((
        CanonicalEntity(entity_key="MLBAM:1", entity_type="PLAYER", display_name="Hernandez"),
        CanonicalEntity(entity_key="MLBAM:2", entity_type="PLAYER", display_name="Hernandez"),
    ))


class EndToEndTests(unittest.TestCase):
    def test_complete_flow_returns_a_sourced_response(self):
        result = pipeline(judge_dictionary()).analyze("How did Aaron Judge perform at the plate?")
        self.assertFalse(result.needs_clarification)
        self.assertEqual(result.objective_statuses, ("COMPLETE",))
        self.assertIn("COMPLETE", result.responses[0])
        self.assertIn("synthetic-", result.responses[0])
        package = result.response_packages[0]
        self.assertTrue(package.accepted_evidence)
        self.assertEqual(package.unresolved_items, ())

    def test_ambiguous_entity_asks_for_clarification_instead_of_guessing(self):
        result = pipeline(ambiguous_dictionary()).analyze("How did Hernandez perform?")
        self.assertTrue(result.needs_clarification)
        self.assertTrue(result.clarifications[0].options)
        self.assertEqual(result.objective_statuses, ())

    def test_clarification_is_checkpointed_and_resumes_the_same_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SqliteOperationalStore(root / "operational.db")
            recorder = RunRecorder(store, LocalFilesystemArtifactStorage(root / "payloads"))
            try:
                subject = pipeline(ambiguous_dictionary(), recorder=recorder)
                waiting = subject.analyze("How did Hernandez perform?", run_id="clarify-run")

                self.assertTrue(waiting.needs_clarification)
                self.assertEqual(waiting.run_ids, ("clarify-run",))
                self.assertEqual(store.latest_checkpoint("clarify-run").recovery_position,
                                 "WAITING_FOR_USER")
                self.assertEqual(store.list_objects("execution", "clarify-run"), ())

                option = waiting.clarifications[0].options[1]
                resumed = subject.resume_clarification(
                    "clarify-run", ClarificationAnswer(
                        clarification_ref=waiting.clarifications[0].clarification_id,
                        chosen_option_id=option.option_id))

                self.assertEqual(resumed.run_ids, ("clarify-run",))
                self.assertEqual(resumed.objective_statuses, ("COMPLETE",))
                self.assertEqual(len(store.list_objects("execution", "clarify-run")), 1)
                interaction = store.get_object("interaction", "clarify-run").payload
                self.assertEqual(interaction["status"], "CONFIRMED")
                confirmed = interaction["confirmed_constraints"][0]
                self.assertEqual(confirmed["origin"], "USER_CONFIRMED")
                self.assertEqual(confirmed["authority"], "USER_CONSTRAINT")
                with self.assertRaises(ValueError):
                    subject.resume_clarification(
                        "clarify-run", ClarificationAnswer(
                            clarification_ref=waiting.clarifications[0].clarification_id,
                            chosen_option_id=option.option_id))
                self.assertEqual(len(store.list_objects("execution", "clarify-run")), 1)
            finally:
                store.close()

    def test_paid_source_permission_is_checkpointed_scoped_and_single_use(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SqliteOperationalStore(root / "operational.db")
            recorder = RunRecorder(store, LocalFilesystemArtifactStorage(root / "payloads"))
            paid_router = Router((ToolCapability(tool="synthetic", source_kind="SYNTHETIC",
                                                  supported_artifact_types=("TABLE",), cost="PAID"),))
            try:
                subject = pipeline(judge_dictionary(), recorder=recorder, router=paid_router)
                waiting = subject.analyze("How did Judge perform?", run_id="paid-run")
                self.assertEqual(len(waiting.permissions), 1)
                self.assertEqual(store.latest_checkpoint("paid-run").recovery_position, "WAITING_FOR_USER")
                self.assertEqual(store.list_objects("execution", "paid-run"), ())
                request = waiting.permissions[0]
                done = subject.resume_permission("paid-run", PermissionAnswer(
                    permission_ref=request.permission_id, approved=True))
                self.assertEqual(done.objective_statuses, ("COMPLETE",))
                self.assertEqual(len(store.list_objects("execution", "paid-run")), 1)
                self.assertEqual(store.get_object("interaction", "paid-run").payload["status"], "APPROVED")
                with self.assertRaises(ValueError):
                    subject.resume_permission("paid-run", PermissionAnswer(
                        permission_ref=request.permission_id, approved=True))
            finally:
                store.close()

    def test_expired_permission_never_executes_and_is_audited(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SqliteOperationalStore()
            self.addCleanup(store.close)
            recorder = RunRecorder(store, LocalFilesystemArtifactStorage(Path(directory)))
            subject = pipeline(judge_dictionary(), recorder=recorder, router=Router((
                ToolCapability(tool="synthetic", source_kind="SYNTHETIC",
                               supported_artifact_types=("TABLE",), cost="PAID"),)))
            waiting = subject.analyze("Judge performance", run_id="expired")
            record = recorder.load_interaction("expired")
            expired = record.permission.model_copy(update={
                "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)})
            recorder.record_interaction(record.model_copy(update={"permission": expired}))
            with self.assertRaisesRegex(ValueError, "expired"):
                subject.resume_permission("expired", PermissionAnswer(
                    permission_ref=waiting.permissions[0].permission_id, approved=True))
            self.assertEqual(store.list_objects("execution", "expired"), ())
            self.assertEqual(recorder.load_interaction("expired").status, "EXPIRED")
            self.assertTrue(store.list_objects("interaction_audit", "expired"))

    def test_permission_is_scoped_to_one_objective_and_current_capability(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SqliteOperationalStore()
            self.addCleanup(store.close)
            recorder = RunRecorder(store, LocalFilesystemArtifactStorage(Path(directory)))
            paid = Router((ToolCapability(tool="synthetic", source_kind="SYNTHETIC",
                supported_artifact_types=("TABLE", "EVIDENCE"), cost="PAID"),))
            subject = pipeline(judge_dictionary(), recorder=recorder, router=paid)
            waiting = subject.analyze("Judge injury and salary value", run_id="scoped")
            result = subject.resume_permission("scoped", PermissionAnswer(
                permission_ref=waiting.permissions[0].permission_id, approved=True))
            self.assertEqual(result.objective_statuses.count("COMPLETE"), 1)
            self.assertEqual(len(store.list_objects("execution", "scoped")), 1)

    def test_blocked_source_constraint_can_be_revised_once_in_same_run(self):
        from app.models.interaction import ConstraintRevisionAnswer
        with tempfile.TemporaryDirectory() as directory:
            store = SqliteOperationalStore()
            self.addCleanup(store.close)
            recorder = RunRecorder(store, LocalFilesystemArtifactStorage(Path(directory)))
            subject = pipeline(judge_dictionary(), recorder=recorder)
            original = CategoryConstraint(key="source", values=("POSTGRES",), origin="USER_EXPLICIT")
            waiting = subject.analyze("Judge performance", constraints=(original,), run_id="revision")
            request = waiting.constraint_revisions[0]
            self.assertEqual(store.latest_checkpoint("revision").recovery_position, "WAITING_FOR_USER")
            self.assertEqual(store.list_objects("execution", "revision"), ())
            answer = ConstraintRevisionAnswer(revision_ref=request.revision_id, accepted=True)
            done = subject.resume_constraint_revision("revision", answer)
            self.assertEqual(done.run_ids, ("revision",))
            self.assertEqual(done.objective_statuses, ("COMPLETE",))
            self.assertEqual(done.objectives[0].constraints[0].origin, "USER_CONFIRMED")
            self.assertEqual(original.values, ("POSTGRES",))
            with self.assertRaises(ValueError):
                subject.resume_constraint_revision("revision", answer)

    def test_persisted_interaction_can_only_be_consumed_once(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SqliteOperationalStore()
            self.addCleanup(store.close)
            recorder = RunRecorder(store, LocalFilesystemArtifactStorage(Path(directory)))
            subject = pipeline(ambiguous_dictionary(), recorder=recorder)
            subject.analyze("Hernandez performance", run_id="claim")
            pending = recorder.load_interaction("claim")
            confirmed = pending.model_copy(update={"status": "CONFIRMED"})
            recorder.consume_interaction(pending, confirmed)
            with self.assertRaises(ValueError):
                recorder.consume_interaction(pending, confirmed)

    def test_revision_rejection_wrong_run_and_system_policy_preserve_constraints(self):
        from app.models.interaction import ConstraintRevisionAnswer
        with tempfile.TemporaryDirectory() as directory:
            store = SqliteOperationalStore()
            self.addCleanup(store.close)
            recorder = RunRecorder(store, LocalFilesystemArtifactStorage(Path(directory)))
            subject = pipeline(judge_dictionary(), recorder=recorder)
            original = CategoryConstraint(key="source", values=("POSTGRES",), origin="USER_EXPLICIT")
            waiting = subject.analyze("Judge performance", constraints=(original,), run_id="reject")
            answer = ConstraintRevisionAnswer(revision_ref=waiting.constraint_revisions[0].revision_id,
                                              accepted=False)
            with self.assertRaises(ValueError):
                subject.resume_constraint_revision("wrong-run", answer)
            done = subject.resume_constraint_revision("reject", answer)
            self.assertEqual(done.objectives[0].constraints, (original,))
            self.assertEqual(store.list_objects("execution", "reject"), ())
            self.assertEqual(recorder.load_interaction("reject").status, "REJECTED")
            policy = original.model_copy(update={"authority": "SYSTEM_POLICY"})
            blocked = subject.analyze("Judge performance", constraints=(policy,), run_id="policy")
            self.assertEqual(blocked.constraint_revisions, ())
            self.assertEqual(blocked.permissions, ())
            self.assertEqual(store.list_objects("execution", "policy"), ())

    def test_multi_objective_run_scopes_response_per_objective(self):
        result = pipeline(judge_dictionary()).analyze("Analyse Judge injury and salary value")
        self.assertEqual(len(result.objective_statuses), 2)
        for package in result.response_packages:
            self.assertEqual(len(package.accepted_evidence), 1, "no cross-objective leakage")

    def test_empty_source_yields_failed_objective_with_no_accepted_evidence(self):
        result = pipeline(judge_dictionary(), row_count=0).analyze("How did Judge perform?")
        self.assertEqual(result.objective_statuses, ("FAILED",))
        self.assertEqual(result.response_packages[0].accepted_evidence, ())
        self.assertNotIn("synthetic-", result.responses[0])

    def test_persisted_run_checkpoints_and_resumes_without_reexecution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SqliteOperationalStore(root / "operational.db")
            counter = iter(range(1, 100_000))
            recorder = RunRecorder(store, LocalFilesystemArtifactStorage(root / "payloads"),
                                   id_factory=lambda prefix: f"{prefix}-{next(counter)}")
            try:
                result = pipeline(judge_dictionary(), recorder=recorder).analyze(
                    "How did Judge perform?", run_id="run-e2e")
                self.assertEqual(result.objective_statuses, ("COMPLETE",))
                positions = [item.recovery_position
                             for item in store.list_checkpoints("run-e2e")]
                self.assertIn("FINALIZATION", positions)
                plan = ResumeService(store).build_plan("run-e2e")
                self.assertTrue(plan.planner_terminal)
                self.assertTrue(plan.reusable_artifact_refs)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
