import unittest

from app.agent.executor import Executor, ToolResult
from app.models.planning import AgentTask, RoutingDecision
from tests.factories import artifact


def task() -> AgentTask:
    return AgentTask(task_id="t1", objective_ref="o1", requirement_refs=("r1",), description="get data")


def routing(tool: str | None) -> RoutingDecision:
    return RoutingDecision(decision_id="d1", task_ref="t1", selected_tool=tool, rationale="test")


class ScriptedTool:
    def __init__(self, name: str, results: list[ToolResult]):
        self.name = name
        self._results = list(results)
        self.calls = 0

    def execute(self, task: AgentTask) -> ToolResult:
        self.calls += 1
        return self._results.pop(0)


def executor(*tools, max_retries=1) -> Executor:
    counter = iter(f"id-{index}" for index in range(100))
    return Executor({tool.name: tool for tool in tools}, max_retries=max_retries,
                    id_factory=lambda _p: next(counter))


class ExecutorTests(unittest.TestCase):
    def test_retryable_error_is_retried_up_to_the_bound(self):
        tool = ScriptedTool("flaky", [ToolResult(status="ERROR", error_code="TIMEOUT", retryable=True)] * 2)
        outcome = executor(tool, max_retries=1).run(task(), routing("flaky"))
        self.assertEqual(tool.calls, 2)
        self.assertEqual(outcome.execution.status, "FAILED")
        self.assertEqual(len(outcome.attempts), 2)
        self.assertTrue(all(attempt.retryable for attempt in outcome.attempts))

    def test_retryable_then_success(self):
        tool = ScriptedTool("flaky", [ToolResult(status="ERROR", error_code="TIMEOUT", retryable=True),
                                     ToolResult(status="OK", artifact=artifact())])
        outcome = executor(tool, max_retries=1).run(task(), routing("flaky"))
        self.assertEqual(outcome.execution.status, "SUCCEEDED")
        self.assertEqual(outcome.artifact.artifact_id, "a1")
        self.assertEqual(outcome.execution.artifact_refs, ("a1",))

    def test_non_retryable_error_is_not_retried(self):
        tool = ScriptedTool("broken", [ToolResult(status="ERROR", error_code="BAD_SQL")])
        outcome = executor(tool, max_retries=3).run(task(), routing("broken"))
        self.assertEqual(tool.calls, 1)
        self.assertEqual(outcome.execution.status, "FAILED")

    def test_empty_is_a_business_outcome_not_a_technical_retry(self):
        tool = ScriptedTool("empty", [ToolResult(status="EMPTY")])
        outcome = executor(tool, max_retries=3).run(task(), routing("empty"))
        self.assertEqual(tool.calls, 1)
        self.assertEqual(outcome.execution.status, "EMPTY")
        self.assertEqual(outcome.attempts[0].status, "EMPTY")

    def test_missing_or_unknown_tool_is_blocked(self):
        for selected in (None, "absent"):
            with self.subTest(selected=selected):
                outcome = executor(max_retries=0).run(task(), routing(selected))
                self.assertEqual(outcome.execution.status, "BLOCKED")
                self.assertEqual(outcome.attempts, ())


if __name__ == "__main__":
    unittest.main()
