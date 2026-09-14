import unittest

from app.models.report import AgentReport
from app.models.transition import StateTransition


def report(agent: str = "PLANNER", **changes) -> AgentReport:
    fields = dict(report_id="rep1", agent=agent, assignment_refs=("objective:o1",),
                  status="SUCCEEDED", summary="planned", result_refs=("plan:1",))
    return AgentReport(**(fields | changes))


class AgentReportTests(unittest.TestCase):
    def test_report_is_a_reference_envelope_not_a_payload(self):
        fields = set(report().model_dump())
        self.assertFalse({"payload", "raw", "artifact_body"} & fields)
        self.assertIn("result_refs", fields)

    def test_owned_domain_and_cross_domain_proposals_are_distinguished(self):
        owned = StateTransition(domain="PLANNING", subject_ref="plan:1", from_status="OPEN",
                                to_status="ACCEPTED", reason="plan accepted")
        cross = StateTransition(domain="OBJECTIVE", subject_ref="o1", from_status="IN_PROGRESS",
                                to_status="COMPLETE", reason="all core met")
        item = report(impact_proposals=(owned, cross))
        self.assertEqual(item.owned_domain, "PLANNING")
        self.assertEqual(item.cross_domain_proposals, (cross,))

    def test_status_and_requests_are_recorded(self):
        item = report(agent="JUDGE", status="PARTIAL", requests=("clarification:1",), handoff="to planner")
        self.assertEqual(item.status, "PARTIAL")
        self.assertEqual(item.requests, ("clarification:1",))
        self.assertEqual(item.owned_domain, "ASSESSMENT")


if __name__ == "__main__":
    unittest.main()
