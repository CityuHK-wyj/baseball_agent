import unittest

from app.agent.review import StateTransitionLog
from app.models.transition import StateTransition


def transition(**changes) -> StateTransition:
    fields = dict(domain="REQUIREMENT", subject_ref="r1", from_status="PENDING",
                  to_status="SATISFIED", reason="assessment accepted", trigger="assessment:1", version=0)
    return StateTransition(**(fields | changes))


class StateTransitionTests(unittest.TestCase):
    def test_a_transition_must_change_status(self):
        with self.assertRaises(Exception):
            transition(to_status="PENDING")

    def test_log_applies_ordered_transitions_and_keeps_history(self):
        log = StateTransitionLog()
        log.apply(transition())
        log.apply(transition(from_status="SATISFIED", to_status="PARTIAL", reason="evidence withdrawn",
                             version=1))
        self.assertEqual(log.current("REQUIREMENT", "r1").to_status, "PARTIAL")
        self.assertEqual(len(log.history("REQUIREMENT", "r1")), 2)

    def test_out_of_order_version_and_broken_continuation_are_rejected(self):
        log = StateTransitionLog()
        log.apply(transition())
        with self.assertRaises(ValueError):
            log.apply(transition(from_status="SATISFIED", to_status="PARTIAL", version=5))
        with self.assertRaises(ValueError):
            log.apply(transition(from_status="PENDING", to_status="UNSATISFIED", version=1))

    def test_first_transition_with_nonzero_version_is_rejected(self):
        with self.assertRaises(ValueError):
            StateTransitionLog().apply(transition(version=1))

    def test_current_is_none_before_any_transition(self):
        self.assertIsNone(StateTransitionLog().current("OBJECTIVE", "o1"))


if __name__ == "__main__":
    unittest.main()
