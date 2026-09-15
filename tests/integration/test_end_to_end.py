"""End-to-end vertical flow: query -> semantic -> requirements -> plan -> route ->
execute -> artifact -> validate/judge -> state -> completion -> response.

Offline: a synthetic tool stands in for real sources. No database or network.
"""

import tempfile
import unittest
from pathlib import Path

from app.agent.planner import RuleBasedPlanner
from app.agent.registry import ArtifactRegistry
from app.agent.routing import Router, ToolCapability
from app.assessment.judge import RuleBasedJudge
from app.assessment.service import AssessmentService
from app.llm.response import DeterministicResponseComposer
from app.models.entities import CanonicalEntity
from app.models.clarification import ClarificationAnswer
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
             recorder: RunRecorder | None = None) -> AnalysisPipeline:
    counter = iter(range(1, 100_000))
    ids = lambda prefix: f"{prefix}-{next(counter)}"
    resolver = EntityResolver(dictionary, id_factory=ids)
    semantic = SemanticNormalizer(RuleBasedObjectiveExtractor(id_factory=ids), resolver,
                                  dictionary, id_factory=ids)
    registry = ArtifactRegistry()
    assessment = AssessmentService(registry, RuleBasedJudge(), id_factory=ids)
    router = Router((ToolCapability(tool="synthetic", source_kind="SYNTHETIC",
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
