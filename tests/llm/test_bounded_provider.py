"""Native latency telemetry and bounded-provider tests (v0.6)."""

import time
import unittest

from app.artifact_runtime.engine import RuntimeConversation, _latency_summary
from app.artifact_runtime.planner import LLMPlanner, LLMSemanticInterpreter
from app.artifact_runtime.response import LLMResponseComposer
from app.models.artifact_runtime import Goal, RuntimeArtifact, SemanticBrief
from app.llm.provider import FakeModelProvider, ModelResponse, ProviderError, ProviderTimeout
from app.llm.openai_provider import OpenAICompatibleProvider


class NativeLatencyTests(unittest.TestCase):
    def test_latency_summary_is_scoped_to_the_current_turn(self):
        conversation = RuntimeConversation(conversation_id="c")
        conversation.turns = 2
        conversation.events.emit("LATENCY", turn=1, phase="semantic", duration_ms=10.0)
        conversation.events.emit("LATENCY", turn=2, phase="semantic", duration_ms=5.0)
        conversation.events.emit("LATENCY", turn=2, phase="judge", duration_ms=1.5)
        self.assertEqual(_latency_summary(conversation), {"semantic": 5.0, "judge": 1.5})


class _FakeStreamEvent:
    def __init__(self, content=None, finish_reason=None, usage=None):
        self.usage = usage
        delta = type("Delta", (), {"content": content})() if content is not None else None
        choice = type("Choice", (), {"delta": delta, "finish_reason": finish_reason})()
        self.choices = [choice] if (content is not None or finish_reason) else []


class _FakeStream:
    def __init__(self, events, delay=0.0):
        self._events = events
        self._delay = delay
        self.closed = False

    def __iter__(self):
        for event in self._events:
            if self._delay:
                time.sleep(self._delay)
            yield event

    def close(self):
        self.closed = True


class _FakeCompletions:
    def __init__(self, stream):
        self._stream = stream

    def create(self, **kwargs):
        return self._stream


class _FakeClient:
    def __init__(self, stream):
        self.chat = type("Chat", (), {"completions": _FakeCompletions(stream)})()

    def close(self):
        pass


class BoundedProviderTests(unittest.TestCase):
    def _provider(self):
        return OpenAICompatibleProvider(type("C", (), {
            "deepseek_api_key": "test", "deepseek_base_url": "https://example.invalid",
            "llm_deadline_seconds": 2.0, "llm_stream": True,
            "llm_reasoning_effort": "none"})())

    def test_stream_collects_text_and_usage(self):
        usage = type("U", (), {"prompt_tokens": 10, "completion_tokens": 3,
                               "prompt_cache_hit_tokens": 1,
                               "completion_tokens_details": None,
                               "prompt_tokens_details": None})()
        stream = _FakeStream([_FakeStreamEvent("hel"), _FakeStreamEvent("lo"),
                              _FakeStreamEvent(finish_reason="stop", usage=usage)])
        response = self._provider()._stream(_FakeClient(stream), "p", model="m",
                                            max_tokens=64, reasoning_effort="none",
                                            deadline=5.0, start=time.perf_counter())
        self.assertEqual(response.text, "hello")
        self.assertEqual(response.usage["completion_tokens"], 3)
        self.assertGreaterEqual(response.first_token_seconds, 0.0)

    def test_deadline_is_enforced_mid_stream(self):
        events = [_FakeStreamEvent("x") for _ in range(50)]
        stream = _FakeStream(events, delay=0.1)
        with self.assertRaises(ProviderTimeout):
            self._provider()._stream(_FakeClient(stream), "p", model="m", max_tokens=64,
                                     reasoning_effort="none", deadline=0.25,
                                     start=time.perf_counter())
        self.assertTrue(stream.closed)

    def test_empty_content_from_length_is_a_typed_failure(self):
        stream = _FakeStream([_FakeStreamEvent(finish_reason="length")])
        with self.assertRaises(ProviderError):
            self._provider()._stream(_FakeClient(stream), "p", model="m", max_tokens=16,
                                     reasoning_effort="none", deadline=5.0,
                                     start=time.perf_counter())


class RoleBoundPropagationTests(unittest.TestCase):
    def test_interpreter_forwards_bounds_and_deadline(self):
        provider = FakeModelProvider(['{"goal_statement": "x"}'])
        interpreter = LLMSemanticInterpreter(provider, "deepseek-flash", max_tokens=123,
                                             reasoning_effort="none", deadline=45.0)
        interpreter.brief(message="m", history="", resolved_entities=(), unknowns=(),
                          today="2025-09-30")
        record = provider.records[0]
        self.assertEqual(record["max_tokens"], 123)
        self.assertEqual(record["reasoning_effort"], "none")
        self.assertEqual(record["deadline"], 45.0)

    def test_planner_forwards_bounds_and_deadline(self):
        from app.artifact_runtime.planner import DeterministicPlanner
        provider = FakeModelProvider(['{"needs": []}'])
        planner = LLMPlanner(provider, "deepseek-flash", max_tokens=456,
                             reasoning_effort="none", deadline=30.0,
                             fallback=DeterministicPlanner())
        goal = Goal(goal_id="g", statement="x")
        planner.initial_needs(goal=goal, brief=SemanticBrief(brief_id="b"),
                              context=__import__("app.artifact_runtime.planner",
                                                 fromlist=["PlannerContext"]).PlannerContext())
        record = provider.records[0]
        self.assertEqual(record["max_tokens"], 456)
        self.assertEqual(record["deadline"], 30.0)

    def test_composer_forwards_bounds_and_deadline(self):
        provider = FakeModelProvider(["answer"])
        composer = LLMResponseComposer(provider, "deepseek-flash", max_tokens=789,
                                       reasoning_effort="none", deadline=20.0)
        from app.artifact_runtime.sufficiency import GoalCoverage
        composer.compose(message="m", goal=Goal(goal_id="g", statement="x"),
                         claims=(_claim(),), artifacts=(),
                         coverage=GoalCoverage(core_goal_supported=True, verdict="SATISFIED"),
                         assumptions=())
        record = provider.records[0]
        self.assertEqual(record["max_tokens"], 789)
        self.assertEqual(record["deadline"], 20.0)


def _claim():
    from app.models.artifact_runtime import Claim
    return Claim(claim_id="c1", text="a supported claim")


if __name__ == "__main__":
    unittest.main()
