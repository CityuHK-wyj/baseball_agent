import unittest
from types import SimpleNamespace

from app.models.reports import CompletionReport, ExecutionSummary
from app.observability.evaluation import RunEvaluation, RunSummary, evaluate_runs


def summary(run_id: str, status: str, **changes) -> RunSummary:
    fields = dict(run_id=run_id, objective_status=status)
    return RunSummary(**(fields | changes))


class EvaluationTests(unittest.TestCase):
    def test_rates_are_computed_from_run_summaries(self):
        items = (
            summary("r1", "COMPLETE", rounds=2, plan_revisions=1, tasks_succeeded=1, attempts=1,
                    accepted_artifacts=1),
            summary("r2", "COMPLETE", rounds=3, plan_revisions=2, tasks_succeeded=2, attempts=3,
                    accepted_artifacts=2),
            summary("r3", "FAILED", rounds=2, plan_revisions=1, tasks_failed=1, attempts=3),
        )
        evaluation = evaluate_runs(items)
        self.assertEqual((evaluation.runs, evaluation.complete, evaluation.failed), (3, 2, 1))
        self.assertEqual(evaluation.complete_rate, 0.6667)
        self.assertEqual(evaluation.replan_rate, 0.5714)
        self.assertEqual(evaluation.retry_rate, 0.75)
        self.assertEqual(evaluation.source_failure_rate, 0.25)
        self.assertEqual(evaluation.average_steps, 2.3333)
        self.assertEqual(evaluation.average_accepted_artifacts, 1.0)

    def test_empty_input_is_all_zero(self):
        self.assertEqual(evaluate_runs(()), RunEvaluation())

    def test_judge_disagreement_rate(self):
        deterministic = {"a1": "STRONG", "a2": "WEAK"}
        llm = {"a1": "STRONG", "a2": "ACCEPTABLE"}
        evaluation = evaluate_runs((summary("r1", "COMPLETE"),),
                                   judge_levels=llm, deterministic_levels=deterministic)
        self.assertEqual(evaluation.judge_disagreement_rate, 0.5)

    def test_summary_from_result_reads_the_execution_summary(self):
        result = SimpleNamespace(
            run_id="run-9",
            objective_state=SimpleNamespace(status="LIMITED"),
            completion_report=CompletionReport(
                run_id="run-9", query="q", objective_ref="o1", objective_status="LIMITED",
                stop_reason="MAX_ROUNDS", plan_revisions=2,
                final_artifact_refs=("a1",),
                execution_summary=ExecutionSummary(rounds=3, tasks_succeeded=1, tasks_failed=1,
                                                   attempts=3)))
        item = RunSummary.from_result(result)
        self.assertEqual((item.objective_status, item.rounds, item.tasks_executed), ("LIMITED", 3, 2))
        self.assertEqual(item.accepted_artifacts, 1)


if __name__ == "__main__":
    unittest.main()
