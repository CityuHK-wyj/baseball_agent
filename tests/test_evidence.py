import unittest

from app.models.evidence import RawWebResult
from app.semantic.evidence import RuleBasedEvidenceExtractor, evidence_to_artifact


def extractor() -> RuleBasedEvidenceExtractor:
    counter = iter(f"evidence-{index}" for index in range(100))
    return RuleBasedEvidenceExtractor(id_factory=lambda _p: next(counter))


ARTICLE = ("Aaron Judge was placed on the injured list on 2025-06-01 with a wrist strain. "
           "He signed a nine-year contract worth 360 million dollars. "
           "The weather in New York was pleasant.")


class RuleBasedEvidenceExtractorTests(unittest.TestCase):
    def test_extracts_signal_claims_and_keeps_support_and_time(self):
        raw = RawWebResult(result_id="r1", url="https://example.com/a", title="Judge news",
                           text=ARTICLE, source="example")
        evidence = extractor().extract(raw)
        self.assertGreaterEqual(len(evidence.claims), 2)
        joined = " ".join(item.claim for item in evidence.claims)
        self.assertIn("injured list", joined)
        self.assertIn("contract", joined)
        self.assertNotIn("weather", joined)
        self.assertTrue(all(item.support for item in evidence.claims))
        self.assertEqual(evidence.claims[0].claim_time, "2025")
        self.assertEqual(evidence.raw_ref, "r1")
        self.assertEqual(evidence.provenance.source_kind, "WEB")
        self.assertEqual(evidence.provenance.reference, "https://example.com/a")

    def test_short_or_irrelevant_text_yields_no_claims(self):
        raw = RawWebResult(result_id="r1", url="https://example.com/a", text="Sunny. Nice.")
        self.assertTrue(extractor().extract(raw).is_empty)

    def test_evidence_becomes_an_artifact_with_lineage(self):
        raw = RawWebResult(result_id="r1", url="https://example.com/a", text=ARTICLE)
        evidence = extractor().extract(raw)
        artifact = evidence_to_artifact(evidence, raw_artifact_id="raw-a1")
        self.assertEqual(artifact.descriptor.artifact_type, "EVIDENCE")
        self.assertEqual(artifact.lineage, ("raw-a1",))
        self.assertEqual(artifact.provenance.source_kind, "WEB")
        self.assertEqual(artifact.row_count, len(evidence.claims))

    def test_raw_result_is_not_itself_evidence(self):
        raw = RawWebResult(result_id="r1", url="https://example.com/a", text=ARTICLE)
        self.assertFalse(hasattr(raw, "claims"))
        self.assertTrue(hasattr(extractor().extract(raw), "claims"))


if __name__ == "__main__":
    unittest.main()
