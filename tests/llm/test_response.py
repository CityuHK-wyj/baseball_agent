import unittest

from app.llm.provider import FakeModelProvider, ProviderError
from app.llm.response import DeterministicResponseComposer, LLMResponseComposer
from app.models.reports import AcceptedEvidence, ResponsePackage


def package() -> ResponsePackage:
    return ResponsePackage(
        run_id="run-1", objective_ref="o1", objective_status="COMPLETE",
        accepted_evidence=(AcceptedEvidence(artifact_ref="a1", requirement_ref="r1", level="STRONG",
                                            summary="EV 100 mph over 40 BBE", payload_ref="store://a1",
                                            source_kind="POSTGRES", source="hot"),),
        critical_shared_knowledge=("METRIC:exit_velocity",),
        limitations=("partial temporal coverage",), unresolved_items=())


class ResponseComposerTests(unittest.TestCase):
    def test_llm_composer_uses_only_accepted_evidence(self):
        provider = FakeModelProvider(["A sourced answer."])
        text = LLMResponseComposer(provider, "m").compose(package())
        self.assertEqual(text, "A sourced answer.")
        _, prompt = provider.calls[0]
        self.assertIn("a1", prompt)
        self.assertNotIn("attempt", prompt.lower())
        self.assertNotIn("rejected", prompt.lower())

    def test_provider_failure_falls_back_to_deterministic_text(self):
        provider = FakeModelProvider(error=ProviderError("boom"))
        text = LLMResponseComposer(provider, "m").compose(package())
        self.assertIn("Objective o1: COMPLETE", text)
        self.assertIn("a1", text)

    def test_deterministic_composer_lists_evidence_and_limitations(self):
        text = DeterministicResponseComposer().compose(package())
        self.assertIn("[STRONG] a1", text)
        self.assertIn("partial temporal coverage", text)


if __name__ == "__main__":
    unittest.main()
