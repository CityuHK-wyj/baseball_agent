"""Planner authority, free-form task fields, and open-world dual semantic review."""

import json
import unittest

from app.agent.planner import PlannerContext, RuleBasedPlanner, task_type_for
from app.llm.planner import LLMPlanner
from app.llm.provider import FakeModelProvider
from app.models.contracts import (ArtifactDescriptor, ArtifactRequirement, RankingConstraint)
from app.models.semantic_candidate import CandidateConstraint, EvidenceSpan, SemanticCandidate
from app.models.understanding import SemanticUnderstanding
from app.semantic.lexical_anchors import extract_lexical_anchors
from app.semantic.semantic_reconciler import SemanticReconciler
from tests.factories import objective


def _requirement(**changes) -> ArtifactRequirement:
    fields = dict(
        requirement_id="r1", objective_ref="o1", description="rank exit velocity",
        descriptor=ArtifactDescriptor(artifact_type="TABLE", data_keys=("exit_velocity", "batter"),
                                      granularity="player_rank", population_scope="league",
                                      constraints=(RankingConstraint(metric_key="exit_velocity",
                                                                     aggregation="MAX",
                                                                     direction="DESC", limit=5),)),
    )
    return ArtifactRequirement(**(fields | changes))


def _requirement_state(requirement: ArtifactRequirement):
    from app.models.contracts import RequirementState
    return RequirementState(requirement_ref=requirement.requirement_id)


class PlannerTaskTypeTests(unittest.TestCase):
    def test_evidence_requirement_is_a_web_research_task(self):
        requirement = _requirement(descriptor=ArtifactDescriptor(
            artifact_type="EVIDENCE", data_keys=("injury_status",), granularity="event",
            population_scope="player"))
        self.assertEqual(task_type_for(requirement), "KNOWLEDGE")

    def test_table_requirement_is_local_analytics(self):
        self.assertEqual(task_type_for(_requirement()), "LOCAL_ANALYTICS")

    def test_rule_planner_tasks_carry_free_form_objective_and_strategy(self):
        requirement = _requirement()
        understanding = SemanticUnderstanding(
            raw_query="谁打得更好", analysis_strategy="Use a balanced batting profile.",
            planner_notes="Prefer multiple indicators.")
        context = PlannerContext(
            objective=objective(), requirements=(requirement,),
            requirement_states=(_requirement_state(requirement),),
            recoverable_gaps=(requirement.requirement_id,), raw_query="谁打得更好",
            understanding=understanding)
        decision = RuleBasedPlanner(id_factory=lambda p: f"{p}-1").decide(context)
        task = decision.tasks[0]
        self.assertEqual(task.task_type, "LOCAL_ANALYTICS")
        self.assertIn("balanced batting profile", task.objective)
        self.assertEqual(task.instructions, "Prefer multiple indicators.")


class LLMPlannerFreeFormTests(unittest.TestCase):
    def _context(self) -> PlannerContext:
        requirement = _requirement()
        return PlannerContext(objective=objective(), requirements=(requirement,),
                              requirement_states=(_requirement_state(requirement),),
                              recoverable_gaps=(requirement.requirement_id,),
                              raw_query="rank hitters by maximum exit velocity",
                              understanding=SemanticUnderstanding(raw_query="rank hitters"))

    def test_llm_planner_parses_task_type_and_free_form_fields(self):
        provider = FakeModelProvider([json.dumps({
            "kind": "PLAN",
            "tasks": [{
                "requirement_refs": ["r1"], "description": "rank it",
                "task_type": "LOCAL_ANALYTICS",
                "objective": "Rank hitters by maximum exit velocity.",
                "instructions": "Use the local archive first.",
                "expected_evidence": "A ranked table.",
                "search_hints": ["exit velocity leaders"],
            }],
            "planner_terminal": False, "terminal_reason": "", "rationale": "one task"})])
        decision = LLMPlanner(provider, "test-model",
                              id_factory=lambda p: f"{p}-1").decide(self._context())
        task = decision.tasks[0]
        self.assertEqual(task.task_type, "LOCAL_ANALYTICS")
        self.assertIn("maximum exit velocity", task.objective)
        self.assertEqual(task.search_hints, ("exit velocity leaders",))

    def test_llm_planner_rejects_executable_smuggling(self):
        provider = FakeModelProvider([json.dumps({
            "kind": "PLAN",
            "tasks": [{"requirement_refs": ["r1"], "description": "x", "sql": "DROP TABLE t"}],
            "planner_terminal": False, "terminal_reason": "", "rationale": "bad"})])
        with self.assertRaises(ValueError):
            LLMPlanner(provider, "test-model").decide(self._context())


class DualSemanticOpennessTests(unittest.TestCase):
    """Non-material free-form differences must not block execution."""

    def test_different_briefs_with_identical_typed_meaning_agree(self):
        extractor = SemanticCandidate(
            user_goal="Compare Ohtani and Judge's recent offensive performance.",
            semantic_brief="Recent offensive comparison.")
        reviewer = SemanticCandidate(
            user_goal="Evaluate which hitter has been more productive over the last month.",
            semantic_brief="Productivity over the last month.")
        review = SemanticReconciler().compare(extractor, reviewer, extract_lexical_anchors("q"))
        self.assertEqual(review.agreement_status, "AGREE")

    def test_material_typed_conflict_still_clarifies(self):
        extractor = SemanticCandidate(constraints=(
            CandidateConstraint(kind="RANKING", metric_key="exit_velocity", aggregation="MAX",
                                direction="DESC", limit=5,
                                evidence=EvidenceSpan(text="maximum exit velocity")),))
        reviewer = SemanticCandidate(constraints=(
            CandidateConstraint(kind="RANKING", metric_key="exit_velocity", aggregation="AVG",
                                direction="DESC", limit=5,
                                evidence=EvidenceSpan(text="average exit velocity")),))
        review = SemanticReconciler().compare(
            extractor, reviewer, extract_lexical_anchors("maximum exit velocity"))
        self.assertEqual(review.agreement_status, "MATERIAL_DISAGREEMENT")


if __name__ == "__main__":
    unittest.main()
