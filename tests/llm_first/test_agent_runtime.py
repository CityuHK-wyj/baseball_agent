"""Behavioral tests for the LLM-first runtime.

These test outcomes, not exact semantic JSON: unknown -> investigate, vague -> plan,
clarification -> WAITING_FOR_USER, follow-up -> conversation context, and strict action
boundaries. Network and LLM are replaced with fakes.
"""

import unittest

from app.agent.agent import BaseballAgent
from app.agent.cognition import DeterministicCognition, LLMCognition, _parse_plan
from app.agent.local_analytics import LocalAnalyticsRunner
from app.knowledge.candidates import CandidateKnowledge, CandidateKnowledgeStore
from app.knowledge.service import KnowledgeBase
from app.knowledge.store import SqliteKnowledgeStore
from app.llm.provider import FakeModelProvider, ProviderError
from app.models.agent_runtime import (ClarificationDecision, CognitionPlan, EvidenceItem,
                                      LocalMetricHint)
from app.models.knowledge import KnowledgeItem
from app.semantic.entity_resolver import EntityDictionary, EntityResolver
from app.tools.entity_lookup import EntityLookup


class ScriptedCognition:
    """Returns queued plans and records the context each call received."""

    def __init__(self, plans) -> None:
        self._plans = list(plans)
        self.plan_calls: list[dict] = []
        self.answers: list[str] = []

    def plan(self, **context):
        self.plan_calls.append(context)
        if self._plans:
            return self._plans.pop(0)
        return CognitionPlan(user_goal=context["message"], source="scripted")

    def compose(self, **context):
        self.answers.append(context)
        return "ANSWER: " + context["message"]


def _knowledge() -> KnowledgeBase:
    store = SqliteKnowledgeStore(":memory:")
    store.upsert_item(KnowledgeItem(
        knowledge_id="TERM:DFA", canonical_key="dfa", knowledge_type="TERM",
        title="Designated for Assignment", aliases=("DFA",),
        summary="Removes a player from the 40-man roster.", source_authority="OFFICIAL"))
    return KnowledgeBase(store)


def _agent(cognition) -> BaseballAgent:
    dictionary = EntityDictionary()
    return BaseballAgent(cognition, _knowledge(),
                         EntityLookup(dictionary, EntityResolver(dictionary)),
                         web=None, batting=None, local=None, candidates=None)


class ConversationStateTests(unittest.TestCase):
    def test_knowledge_plan_returns_complete_with_evidence(self):
        cognition = ScriptedCognition([CognitionPlan(
            user_goal="define DFA", needs_knowledge=True, knowledge_queries=("DFA",))])
        agent = _agent(cognition)
        conversation = agent.start_conversation()
        result = agent.send_message(conversation.conversation_id, "DFA是什么意思？")
        self.assertEqual(result.status, "COMPLETE")
        self.assertTrue(any(item.kind == "KNOWLEDGE" for item in result.evidence))
        self.assertIn("ANSWER", result.answer)

    def test_clarification_is_waiting_for_user_and_resumes_naturally(self):
        cognition = ScriptedCognition([
            CognitionPlan(user_goal="compare", unresolved=("高区",),
                          clarification=ClarificationDecision(
                              question="哪种高区定义？", options=("上1/3", "个人上缘"))),
            CognitionPlan(user_goal="compare", research_queries=("q",)),
        ])
        agent = _agent(cognition)
        conversation = agent.start_conversation()
        first = agent.send_message(conversation.conversation_id, "高区快速球?")
        self.assertEqual(first.status, "WAITING_FOR_USER")
        self.assertEqual(agent.get_status(conversation.conversation_id), "WAITING_FOR_USER")
        self.assertIsNotNone(first.pending_clarification)
        second = agent.respond_to_clarification(conversation.conversation_id, "个人上缘")
        self.assertIn(second.status, ("COMPLETE", "LIMITED", "FAILED"))
        # The clarification answer is retained in conversation context for the planner.
        self.assertTrue(any("个人上缘" in item
                            for item in agent.get_conversation(conversation.conversation_id).accepted_context))

    def test_follow_up_receives_conversation_history(self):
        cognition = ScriptedCognition([
            CognitionPlan(user_goal="first", needs_knowledge=True, knowledge_queries=("DFA",)),
            CognitionPlan(user_goal="follow", needs_knowledge=True, knowledge_queries=("DFA",)),
        ])
        agent = _agent(cognition)
        conversation = agent.start_conversation()
        agent.send_message(conversation.conversation_id, "DFA是什么意思？")
        agent.send_message(conversation.conversation_id, "那去年呢？")
        second_history = cognition.plan_calls[1]["history"]
        self.assertIn("DFA", second_history)
        self.assertIn("那去年呢", second_history)

    def test_unresolved_mention_files_a_candidate_not_active_knowledge(self):
        from app.knowledge.candidates import CandidateKnowledgeStore
        cognition = ScriptedCognition([CognitionPlan(
            user_goal="nickname", unresolved=("太鼓达人",), research_queries=("q",))])
        dictionary = EntityDictionary()
        candidates = CandidateKnowledgeStore(":memory:")
        agent = BaseballAgent(cognition, _knowledge(),
                              EntityLookup(dictionary, EntityResolver(dictionary)),
                              web=None, batting=None, local=None, candidates=candidates)
        conversation = agent.start_conversation()
        agent.send_message(conversation.conversation_id, "太鼓达人是什么？")
        stored = candidates.list()
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].status, "CANDIDATE")
        self.assertEqual(stored[0].surface, "太鼓达人")


class CognitionParsingTests(unittest.TestCase):
    def test_plan_parses_free_form_and_hints(self):
        text = ('{"user_goal":"g","understanding":"u","analysis_strategy":"s",'
                '"assumptions":["a"],"unresolved":["x"],"research_queries":["q"],'
                '"needs_batting_stats":true,"batting_year":2025,'
                '"batting_entity_names":["Ohtani"],'
                '"local_metrics":[{"metric":"exit_velocity","aggregation":"MAX",'
                '"entity_ids":["660271"],"start":"2025-01-01","end":"2025-12-31"}],'
                '"clarification":null,"direct_answer":null}')
        plan = _parse_plan(text)
        self.assertEqual(plan.user_goal, "g")
        self.assertTrue(plan.needs_batting_stats)
        self.assertEqual(plan.local_metrics[0].metric, "exit_velocity")
        self.assertEqual(plan.local_metrics[0].entity_ids, ("660271",))

    def test_llm_cognition_falls_back_on_provider_error(self):
        cognition = LLMCognition(FakeModelProvider(error=ProviderError("down")), "m")
        plan = cognition.plan(message="简单问题", history="", resolved_entities=(),
                              unknowns=(), today="2026-09-17")
        self.assertEqual(plan.source, "deterministic")

    def test_deterministic_planner_uses_resolved_entities_for_batting(self):
        plan = DeterministicCognition().plan(
            message="最近30天Ohtani和Judge谁打得更好？", history="",
            resolved_entities=("Shohei Ohtani", "Aaron Judge"), unknowns=(),
            today="2026-09-17")
        self.assertTrue(plan.needs_batting_stats)
        self.assertEqual(plan.batting_entity_names, ("Shohei Ohtani", "Aaron Judge"))


class LocalBoundaryTests(unittest.TestCase):
    def test_unknown_metric_returns_a_recovery_code(self):
        runner = LocalAnalyticsRunner()
        outcome = runner.execute(LocalMetricHint(metric="spin_rate"))
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.recovery_code, "UNKNOWN_LOCAL_METRIC")

    def test_source_selection_uses_coverage_overlap(self):
        runner = LocalAnalyticsRunner(postgres_enabled=True)
        caveats: list[str] = []
        from app.models.contracts import TimeRange
        from datetime import date
        self.assertEqual(runner._source_for(  # noqa: SLF001
            TimeRange(start=date(2023, 1, 1), end=date(2023, 12, 31)), caveats), "PARQUET")
        self.assertEqual(runner._source_for(  # noqa: SLF001
            TimeRange(start=date(2025, 1, 1), end=date(2025, 12, 31)), caveats), "POSTGRES")


if __name__ == "__main__":
    unittest.main()
