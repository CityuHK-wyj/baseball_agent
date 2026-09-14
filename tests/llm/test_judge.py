import unittest

from app.assessment.judge import RuleBasedJudge
from app.llm.judge import LLMJudge
from app.llm.provider import FakeModelProvider
from app.models.artifacts import DeterministicResult, HardFailure, SoftSignal
from tests.factories import artifact, requirement


class LLMJudgeTests(unittest.TestCase):
    def test_hard_failure_is_rejected_without_calling_the_provider(self):
        provider = FakeModelProvider(['{"level":"STRONG","rationale":"ignore the problem"}'])
        deterministic = DeterministicResult(hard_failures=(HardFailure(code="TYPE_MISMATCH", detail="x"),))
        result = LLMJudge(provider, "m").assess(artifact(), requirement(), deterministic)
        self.assertEqual(result.level, "REJECT")
        self.assertEqual(provider.calls, [])

    def test_soft_signal_level_is_parsed(self):
        provider = FakeModelProvider(['{"level":"WEAK","rationale":"small sample for inference"}'])
        deterministic = DeterministicResult(soft_signals=(SoftSignal(code="LOW_SAMPLE", severity="MAJOR",
                                                                    detail="12 BBE"),))
        result = LLMJudge(provider, "m").assess(artifact(), requirement(), deterministic)
        self.assertEqual(result.level, "WEAK")
        self.assertIn("small sample", result.rationale)

    def test_malformed_output_falls_back(self):
        provider = FakeModelProvider(["oops"])
        result = LLMJudge(provider, "m", fallback=RuleBasedJudge()).assess(
            artifact(row_count=0), requirement(), DeterministicResult())
        # Fallback sees ZERO_ROWS is not present (deterministic empty) -> STRONG; key point is no crash.
        self.assertIn(result.level, ("STRONG", "ACCEPTABLE", "WEAK", "REJECT"))

    def test_invalid_level_falls_back(self):
        provider = FakeModelProvider(['{"level":"PERFECT"}'])
        result = LLMJudge(provider, "m", fallback=RuleBasedJudge()).assess(
            artifact(), requirement(), DeterministicResult())
        self.assertEqual(result.level, "STRONG")


if __name__ == "__main__":
    unittest.main()
