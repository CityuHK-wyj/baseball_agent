import unittest

from app.observability.metrics import RunMetrics

SYNTHETIC_CREDENTIAL = "synthetic-local-value"


class RunMetricsTests(unittest.TestCase):
    def test_free_text_is_redacted_before_storage(self):
        metrics = RunMetrics("run-1", secrets=(SYNTHETIC_CREDENTIAL,))
        event = metrics.record("ATTEMPT", message=f"connect failed password={SYNTHETIC_CREDENTIAL}",
                               tool="hot", retry_count=1)
        self.assertNotIn(SYNTHETIC_CREDENTIAL, event.message)
        self.assertIn("SECRET_REDACTED", event.message)

    def test_events_are_ordered_and_countable(self):
        metrics = RunMetrics("run-1")
        metrics.record("PLAN", status="PLAN")
        metrics.record("TASK", tool="hot", status="SUCCEEDED", duration_ms=12)
        self.assertEqual([event.event_type for event in metrics.events()], ["PLAN", "TASK"])
        self.assertEqual(metrics.count("TASK"), 1)

    def test_no_stored_field_contains_the_known_value(self):
        known_value = "synthetic-second-value"
        metrics = RunMetrics("run-1", secrets=(known_value,))
        metrics.record("LLM", message=f"provider error token={known_value}", tokens=10)
        for event in metrics.events():
            self.assertNotIn(known_value, str(event.model_dump()))


if __name__ == "__main__":
    unittest.main()
