"""Terminal states, coverage-driven status, and conversation persistence."""

import unittest
from datetime import date

from app.models.artifact_runtime import Need, Scope
from app.models.contracts import TimeRange
from app.persistence.store import SqliteOperationalStore

from tests.artifact_runtime.fakes import RecordingExecutor
from tests.artifact_runtime.harness import build_test_runtime
from tests.artifact_runtime.test_composition import _local_ir


class TerminalStateTests(unittest.TestCase):
    def test_wrong_scope_evidence_is_not_complete(self):
        need = Need(need_id="sql", objective="2025 window",
                    proposed_capability="local_analytics",
                    preferred_capabilities=("STATISTICAL_RESULT",),
                    parameters={"analytical_query": _local_ir(entity_set=False)},
                    required_scope=Scope(time_range=TimeRange(start=date(2024, 4, 1),
                                                              end=date(2024, 9, 30))))
        runtime, _ = build_test_runtime(
            needs=(need,), executor=RecordingExecutor(rows=((660271, 0.62, 40, 65),)))
        result = runtime.send_message(runtime.start_conversation(), "2025 hard hit")
        self.assertNotEqual(result.status, "COMPLETE")
        self.assertFalse(result.coverage.core_goal_supported)
        self.assertFalse(result.claims)
        self.assertTrue(result.coverage.gaps)

    def test_invalid_ir_with_no_recovery_is_failed(self):
        bad = _local_ir(entity_set=False)
        bad["selections"] = [{"alias": "x", "kind": "GROUP_KEY", "field": "no_such_field"}]
        need = Need(need_id="sql", objective="bad", proposed_capability="local_analytics",
                    preferred_capabilities=("STATISTICAL_RESULT",),
                    parameters={"analytical_query": bad})
        runtime, executor = build_test_runtime(needs=(need,))
        result = runtime.send_message(runtime.start_conversation(), "bad")
        self.assertEqual(executor.statements, [])
        self.assertEqual(result.status, "FAILED")
        self.assertFalse(result.claims)

    def test_empty_analytics_is_not_complete(self):
        need = Need(need_id="sql", objective="hard hit",
                    proposed_capability="local_analytics",
                    preferred_capabilities=("STATISTICAL_RESULT",),
                    parameters={"analytical_query": _local_ir(entity_set=False)})
        runtime, _ = build_test_runtime(needs=(need,), executor=RecordingExecutor(rows=()))
        result = runtime.send_message(runtime.start_conversation(), "hard hit")
        self.assertNotEqual(result.status, "COMPLETE")
        self.assertFalse(result.claims)

    def test_clarification_is_waiting_for_user(self):
        runtime, _ = build_test_runtime(
            pending_clarification_question="Which high-zone definition?")
        conversation_id = runtime.start_conversation()
        result = runtime.send_message(conversation_id, "high fastballs")
        self.assertEqual(result.status, "WAITING_FOR_USER")
        self.assertIsNotNone(result.pending_clarification)
        self.assertEqual(runtime.get_conversation(conversation_id).status, "WAITING_FOR_USER")

    def test_grounded_knowledge_completes_an_unscoped_definition(self):
        runtime, _ = build_test_runtime(needs=(
            Need(need_id="k", objective="define DFA", proposed_capability="shared_knowledge",
                 preferred_capabilities=("KNOWLEDGE_CANDIDATE",)),))
        result = runtime.send_message(runtime.start_conversation(), "DFA?")
        self.assertEqual(result.status, "COMPLETE")
        self.assertTrue(result.claims)


class ConversationPersistenceTests(unittest.TestCase):
    def _runtime(self, store):
        need = Need(need_id="k", objective="define DFA",
                    proposed_capability="shared_knowledge",
                    preferred_capabilities=("KNOWLEDGE_CANDIDATE",))
        runtime, _ = build_test_runtime(needs=(need,), store=store)
        return runtime

    def test_conversation_state_survives_a_restart(self):
        store = SqliteOperationalStore(":memory:")
        runtime = self._runtime(store)
        conversation_id = runtime.start_conversation()
        runtime.send_message(conversation_id, "DFA?")
        conversation = runtime.get_conversation(conversation_id)
        self.assertTrue(conversation.artifacts.all())

        revived = self._runtime(store)
        restored = revived.resume_conversation(conversation_id)
        self.assertEqual(len(restored.artifacts.all()), len(conversation.artifacts.all()))
        self.assertEqual(restored.goal.statement, conversation.goal.statement)
        self.assertTrue(restored.refs.all())
        self.assertTrue(restored.export_refs)

    def test_follow_up_starts_a_new_goal_and_keeps_history(self):
        runtime, _ = build_test_runtime(needs=(
            Need(need_id="k", objective="define DFA", proposed_capability="shared_knowledge",
                 preferred_capabilities=("KNOWLEDGE_CANDIDATE",)),))
        conversation_id = runtime.start_conversation()
        runtime.send_message(conversation_id, "DFA?")
        interpreter = runtime._interpreter  # noqa: SLF001
        runtime.send_message(conversation_id, "and what about waivers?")
        conversation = runtime.get_conversation(conversation_id)
        self.assertEqual(len(conversation.previous_goals), 1)
        self.assertIn("DFA?", interpreter.calls[1]["history"])

    def test_current_goal_needs_do_not_reuse_prior_artifacts(self):
        store = SqliteOperationalStore(":memory:")
        runtime, _ = build_test_runtime(
            needs=(Need(need_id="k", objective="define DFA", proposed_capability="shared_knowledge",
                        preferred_capabilities=("KNOWLEDGE_CANDIDATE",)),), store=store)
        conversation_id = runtime.start_conversation()
        runtime.send_message(conversation_id, "DFA?")
        # A follow-up with an unsupported need must not inherit the prior artifact.
        runtime._planner = _EmptyAfterFirst(runtime._planner)  # noqa: SLF001
        result = runtime.send_message(conversation_id, "totally new unknown thing")
        self.assertNotEqual(result.status, "COMPLETE")


class _EmptyAfterFirst:
    """Planner that produces no needs, so a new goal cannot be silently satisfied."""

    def __init__(self, wrapped=None):
        self._wrapped = wrapped

    def initial_needs(self, **kwargs):
        return ()

    def add_needs(self, **kwargs):
        return ()

    def next_action(self, **kwargs):
        if self._wrapped is None:
            return None
        return self._wrapped.next_action(**kwargs)


if __name__ == "__main__":
    unittest.main()
