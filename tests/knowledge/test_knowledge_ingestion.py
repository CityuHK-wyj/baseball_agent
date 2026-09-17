"""Ingestion, validation, staging/activation, supersession and snapshots."""

import unittest
from datetime import date

from app.knowledge.ingestion import (
    KnowledgeIngester,
    KnowledgePack,
    KnowledgePackError,
    KnowledgeValidator,
)
from app.knowledge.store import SqliteKnowledgeStore
from app.models.knowledge import KnowledgeItem, KnowledgeSource

SOURCE = KnowledgeSource(source_id="mlb_statsapi", name="MLB Stats API",
                         root_url="https://statsapi.mlb.com", source_type="STATS_API",
                         authority_level="OFFICIAL", refresh_policy="SEASONAL",
                         last_checked=date(2026, 9, 15))


def team(key: str, name: str, **changes) -> KnowledgeItem:
    fields = dict(
        knowledge_id=f"TEAM:{key}", knowledge_type="TEAM", canonical_key=key, title=name,
        source_refs=("mlb_statsapi",), source_authority="OFFICIAL", freshness_policy="ANNUAL",
        verification_status="VERIFIED", status="ACTIVE",
        structured_payload={"official_name": name, "league": "National League",
                            "division": "National League West", "abbreviation": key},
    )
    return KnowledgeItem(**(fields | changes))


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.store = SqliteKnowledgeStore(":memory:")
        self.ingester = KnowledgeIngester(self.store, today=lambda: date(2026, 9, 15),
                                          snapshot_id_factory=lambda domain: f"snap-{domain}")

    def tearDown(self):
        self.store.close()

    def test_stage_is_collected_and_activate_promotes(self):
        pack = KnowledgePack(domain="teams", items=(team("LAD", "Los Angeles Dodgers"),),
                             sources=(SOURCE,), expected_team_keys=("LAD",))
        diff = self.ingester.ingest(pack, activate=False)
        self.assertEqual(diff.added, ("TEAM:LAD",))
        self.assertEqual(self.store.get_item("TEAM:LAD").status, "COLLECTED")
        activated = self.ingester.ingest(pack, activate=True)
        self.assertEqual(activated.updated, ("TEAM:LAD",))
        stored = self.store.get_item("TEAM:LAD")
        self.assertEqual(stored.status, "ACTIVE")
        self.assertEqual(stored.verification_status, "VERIFIED")

    def test_team_set_mismatch_is_fatal(self):
        pack = KnowledgePack(domain="teams", items=(team("LAD", "Los Angeles Dodgers"),),
                             sources=(SOURCE,), expected_team_keys=("LAD", "SF"))
        with self.assertRaises(KnowledgePackError):
            self.ingester.ingest(pack)

    def test_duplicate_canonical_key_is_fatal(self):
        pack = KnowledgePack(domain="teams", sources=(SOURCE,),
                             items=(team("LAD", "Dodgers A"), team("LAD", "Dodgers B")))
        with self.assertRaises(KnowledgePackError):
            self.ingester.ingest(pack)

    def test_missing_source_reference_is_rejected(self):
        bad = team("LAD", "Dodgers", source_refs=("does_not_exist",))
        pack = KnowledgePack(domain="teams", items=(bad,), sources=(SOURCE,))
        diff = self.ingester.ingest(pack)
        self.assertIn("TEAM:LAD", diff.rejected)
        self.assertIsNone(self.store.get_item("TEAM:LAD"))

    def test_rule_without_reference_is_rejected(self):
        rule = KnowledgeItem(knowledge_id="RULE:BALK", knowledge_type="RULE", canonical_key="balk",
                             title="Balk", source_refs=("mlb_statsapi",),
                             source_authority="OFFICIAL", structured_payload={})
        pack = KnowledgePack(domain="rules", items=(rule,), sources=(SOURCE,))
        diff = self.ingester.ingest(pack)
        self.assertIn("RULE:BALK", diff.rejected)

    def test_refresh_supersedes_items_dropped_from_the_pack(self):
        first = KnowledgePack(domain="teams", sources=(SOURCE,),
                              items=(team("LAD", "Dodgers"), team("SF", "Giants")))
        self.ingester.ingest(first)
        second = KnowledgePack(domain="teams", sources=(SOURCE,),
                               items=(team("LAD", "Dodgers"),))
        diff = self.ingester.ingest(second)
        self.assertEqual(diff.unchanged, ("TEAM:LAD",))
        self.assertEqual(self.store.get_item("TEAM:SF").status, "SUPERSEDED")
        self.assertEqual(self.store.get_item("TEAM:LAD").status, "ACTIVE")

    def test_snapshot_records_counts_and_sources(self):
        pack = KnowledgePack(domain="teams", sources=(SOURCE,),
                             items=(team("LAD", "Dodgers"),))
        self.ingester.ingest(pack)
        snapshots = self.store.list_snapshots()
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].snapshot_id, "snap-teams")
        self.assertTrue(snapshots[0].activated)
        self.assertEqual(snapshots[0].counts.get("TEAM"), 1)

    def test_validator_reports_unknown_source_and_bad_dates(self):
        validator = KnowledgeValidator(known_source_ids={"mlb_statsapi"})
        bad = team("LAD", "Dodgers", effective_from=date(2024, 1, 1), effective_to=date(2020, 1, 1))
        report = validator.validate(KnowledgePack(domain="x", items=(bad,)))
        self.assertIn("TEAM:LAD", report.rejected)


if __name__ == "__main__":
    unittest.main()
