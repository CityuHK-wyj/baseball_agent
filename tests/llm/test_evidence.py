import unittest

from app.llm.evidence import LLMEvidenceExtractor
from app.llm.provider import FakeModelProvider, ProviderError
from app.models.evidence import RawWebResult
from app.semantic.evidence import RuleBasedEvidenceExtractor


def raw() -> RawWebResult:
    return RawWebResult(result_id="r1", url="https://example.com/a", title="T",
                        text="Judge was placed on the injured list in 2025 with a strain.")


class LLMEvidenceExtractorTests(unittest.TestCase):
    def test_valid_json_becomes_claims(self):
        provider = FakeModelProvider(['{"claims":[{"claim":"Judge was placed on the injured list in 2025 with a strain.",'
                                      '"support":"Judge was placed on the injured list in 2025 with a strain.",'
                                      '"claim_time":"2025"}]}'])
        evidence = LLMEvidenceExtractor(provider, "m").extract(raw())
        self.assertEqual(len(evidence.claims), 1)
        self.assertIn("injured", evidence.claims[0].claim)
        self.assertEqual(evidence.claims[0].claim_time, "2025")

    def test_hallucinated_claim_falls_back_to_grounded_extraction(self):
        provider = FakeModelProvider(['{"claims":[{"claim":"Judge signed a 999 million dollar deal.",'
                                      '"support":"Judge signed a 999 million dollar deal.",'
                                      '"claim_time":"2025"}]}'])
        evidence = LLMEvidenceExtractor(provider, "m",
                                        fallback=RuleBasedEvidenceExtractor()).extract(raw())

        self.assertTrue(evidence.claims)
        self.assertNotIn("999 million", " ".join(item.claim for item in evidence.claims))

    def test_extra_fields_are_rejected(self):
        provider = FakeModelProvider(['{"claims":[],"ignore_policy":true}'])

        with self.assertRaises(ValueError):
            LLMEvidenceExtractor(provider, "m").extract(raw())

    def test_malformed_output_falls_back(self):
        provider = FakeModelProvider(["not json"])
        evidence = LLMEvidenceExtractor(provider, "m",
                                        fallback=RuleBasedEvidenceExtractor()).extract(raw())
        self.assertTrue(evidence.claims)

    def test_provider_error_falls_back(self):
        provider = FakeModelProvider(error=ProviderError("boom"))
        evidence = LLMEvidenceExtractor(provider, "m",
                                        fallback=RuleBasedEvidenceExtractor()).extract(raw())
        self.assertTrue(evidence.claims)

    def test_empty_claim_is_rejected(self):
        provider = FakeModelProvider(['{"claims":[{"claim":"   "}]}'])
        with self.assertRaises(ValueError):
            LLMEvidenceExtractor(provider, "m").extract(raw())


if __name__ == "__main__":
    unittest.main()
