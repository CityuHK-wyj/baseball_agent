import unittest

from app.agent.review import ReportReviewer, StateTransitionLog
from app.models.report import AgentReport
from app.models.transition import StateTransition


def reviewer() -> ReportReviewer:
    return ReportReviewer()


def report(report_id: str, agent: str = "PLANNER", **changes) -> AgentReport:
    fields = dict(report_id=report_id, agent=agent, assignment_refs=(), status="SUCCEEDED",
                  result_refs=())
    return AgentReport(**(fields | changes))


class ReportReviewTests(unittest.TestCase):
    def test_report_with_unresolved_prerequisite_is_deferred(self):
        item = report("rep1", assignment_refs=("objective:o1",))
        decision = reviewer().review(item, resolved_refs=())
        self.assertEqual(decision.status, "DEFERRED")

    def test_failed_report_is_rejected(self):
        item = report("rep1", status="FAILED")
        self.assertEqual(reviewer().review(item, resolved_refs=()).status, "REJECTED")

    def test_cross_domain_proposal_requires_review(self):
        cross = StateTransition(domain="OBJECTIVE", subject_ref="o1", from_status="IN_PROGRESS",
                                to_status="COMPLETE", reason="done")
        item = report("rep1", agent="JUDGE", impact_proposals=(cross,))
        self.assertEqual(reviewer().review(item, resolved_refs=()).status, "READY_FOR_REVIEW")

    def test_reports_are_reviewed_in_dependency_order_not_arrival_order(self):
        producer = report("A", agent="PLANNER", assignment_refs=("objective:o1",),
                          result_refs=("plan:1",))
        consumer = report("B", agent="EXECUTOR", assignment_refs=("plan:1",))
        # Consumer arrives first; it must wait until the producer is accepted.
        outcome = reviewer().review_all((consumer, producer), resolved_refs=("objective:o1",))
        by_report = {item.report_ref: item.status for item in outcome.decisions}
        self.assertEqual(by_report["A"], "ACCEPTED")
        self.assertEqual(by_report["B"], "ACCEPTED")
        self.assertEqual(outcome.deferred, ())

    def test_cycle_of_unresolved_prerequisites_stays_deferred(self):
        first = report("A", assignment_refs=("from:B",), result_refs=("from:A",))
        second = report("B", assignment_refs=("from:A",), result_refs=("from:B",))
        outcome = reviewer().review_all((first, second))
        self.assertEqual(set(outcome.deferred), {"A", "B"})

    def test_reviewed_cross_domain_transitions_are_applied_by_the_orchestrator(self):
        cross = StateTransition(domain="OBJECTIVE", subject_ref="o1", from_status="IN_PROGRESS",
                                to_status="COMPLETE", reason="all core met")
        item = report("rep1", agent="JUDGE", impact_proposals=(cross,))
        decision = reviewer().review(item, resolved_refs=())
        self.assertEqual(decision.status, "READY_FOR_REVIEW")
        log = StateTransitionLog()
        log.apply_report_proposals(item)
        self.assertEqual(log.current("OBJECTIVE", "o1").to_status, "COMPLETE")


if __name__ == "__main__":
    unittest.main()
