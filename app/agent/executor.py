"""Bounded technical execution. Retries live here; recovery lives in the Planner."""

from typing import Callable, Protocol

from app.models.artifacts import Artifact, ArtifactContract
from app.models.planning import AgentTask, RoutingDecision, TaskAttempt, TaskExecution
from app.tools.results import ToolResult

__all__ = ["Executor", "Tool", "ToolResult", "ExecutionOutcome"]


class ExecutionOutcome(ArtifactContract):
    execution: TaskExecution
    attempts: tuple[TaskAttempt, ...] = ()
    artifact: Artifact | None = None


class Tool(Protocol):
    name: str

    def execute(self, task: AgentTask) -> ToolResult: ...


_STATUS = {"OK": "SUCCEEDED", "EMPTY": "EMPTY", "ERROR": "FAILED"}


class Executor:
    def __init__(self, tools: dict[str, Tool] | None = None, max_retries: int = 1,
                 id_factory: Callable[[str], str] | None = None) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must not be negative")
        self._tools = dict(tools or {})
        self._max_retries = max_retries
        self._id_factory = id_factory or (lambda prefix: f"{prefix}-{id(object())}")

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def run(self, task: AgentTask, routing: RoutingDecision) -> ExecutionOutcome:
        execution_id = self._id_factory("execution")
        if routing.selected_tool is None or routing.selected_tool not in self._tools:
            return ExecutionOutcome(execution=TaskExecution(
                execution_id=execution_id, task_ref=task.task_id, status="BLOCKED"))
        tool = self._tools[routing.selected_tool]
        attempts: list[TaskAttempt] = []
        artifact: Artifact | None = None
        status = "FAILED"
        for _ in range(self._max_retries + 1):
            result = tool.execute(task)
            artifact = result.artifact
            attempts.append(TaskAttempt(
                attempt_id=self._id_factory("attempt"), execution_ref=execution_id, tool=tool.name,
                status=_STATUS[result.status], retryable=result.retryable,
                error_code=result.error_code, error_type=result.error_type,
                safe_error_summary=result.safe_error_summary,
                artifact_ref=result.artifact.artifact_id if result.artifact else None))
            status = _STATUS[result.status]
            if result.status != "ERROR" or not result.retryable:
                break
        return ExecutionOutcome(
            execution=TaskExecution(
                execution_id=execution_id, task_ref=task.task_id, status=status,
                attempt_refs=tuple(item.attempt_id for item in attempts),
                artifact_refs=(artifact.artifact_id,) if artifact else ()),
            attempts=tuple(attempts), artifact=artifact)
