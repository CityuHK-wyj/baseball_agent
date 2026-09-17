"""System-level evaluation metrics.

Produces structured numbers (completion/replan/retry/reuse/failure rates and average
steps) from run summaries. Deliberately simple and explainable; no dashboard.
"""

from collections.abc import Iterable, Mapping
from typing import Literal

from app.models.artifacts import ArtifactContract
from app.models.contracts import Name

ObjectiveStatus = Literal["PENDING", "IN_PROGRESS", "COMPLETE", "LIMITED", "FAILED"]


class RunSummary(ArtifactContract):
    run_id: Name
    objective_status: ObjectiveStatus
    rounds: int = 0
    plan_revisions: int = 0
    tasks_succeeded: int = 0
    tasks_empty: int = 0
    tasks_failed: int = 0
    attempts: int = 0
    accepted_artifacts: int = 0

    @property
    def tasks_executed(self) -> int:
        return self.tasks_succeeded + self.tasks_empty + self.tasks_failed

    @classmethod
    def from_result(cls, result) -> "RunSummary":
        execution = result.completion_report.execution_summary
        return cls(
            run_id=result.run_id, objective_status=result.objective_state.status,
            rounds=execution.rounds, plan_revisions=result.completion_report.plan_revisions,
            tasks_succeeded=execution.tasks_succeeded, tasks_empty=execution.tasks_empty,
            tasks_failed=execution.tasks_failed, attempts=execution.attempts,
            accepted_artifacts=len(result.completion_report.final_artifact_refs))


class RunEvaluation(ArtifactContract):
    runs: int = 0
    complete: int = 0
    limited: int = 0
    failed: int = 0
    complete_rate: float = 0.0
    replan_rate: float = 0.0
    retry_rate: float = 0.0
    source_failure_rate: float = 0.0
    average_steps: float = 0.0
    average_accepted_artifacts: float = 0.0
    judge_disagreement_rate: float = 0.0


def _ratio(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def evaluate_runs(summaries: Iterable[RunSummary],
                  judge_levels: Mapping[str, str] | None = None,
                  deterministic_levels: Mapping[str, str] | None = None) -> RunEvaluation:
    items = tuple(summaries)
    if not items:
        return RunEvaluation()
    runs = len(items)
    rounds = sum(item.rounds for item in items)
    revisions = sum(item.plan_revisions for item in items)
    executed = sum(item.tasks_executed for item in items)
    attempts = sum(item.attempts for item in items)
    retries = max(attempts - executed, 0)
    failed = sum(item.tasks_failed for item in items)
    disagreement = 0.0
    if judge_levels and deterministic_levels:
        shared = set(judge_levels) & set(deterministic_levels)
        if shared:
            disagreement = _ratio(sum(1 for key in shared
                                      if judge_levels[key] != deterministic_levels[key]), len(shared))
    return RunEvaluation(
        runs=runs,
        complete=sum(1 for item in items if item.objective_status == "COMPLETE"),
        limited=sum(1 for item in items if item.objective_status == "LIMITED"),
        failed=sum(1 for item in items if item.objective_status == "FAILED"),
        complete_rate=_ratio(sum(1 for item in items if item.objective_status == "COMPLETE"), runs),
        replan_rate=_ratio(revisions, rounds),
        retry_rate=_ratio(retries, executed),
        source_failure_rate=_ratio(failed, executed),
        average_steps=_ratio(rounds, runs),
        average_accepted_artifacts=_ratio(sum(item.accepted_artifacts for item in items), runs),
        judge_disagreement_rate=disagreement)
