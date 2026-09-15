"""Knowledge store persistence, versioning, provenance and snapshot tests."""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from app.knowledge.store import PostgresKnowledgeStore, SqliteKnowledgeStore
from app.models.knowledge import (
    KnowledgeItem,
    KnowledgeRelation,
    KnowledgeSnapshot,
    KnowledgeSource,
)


def item(knowledge_id: str = "TERM:DFA", **changes) -> KnowledgeItem:
    fields = dict(
        knowledge_id=knowledge_id, knowledge_type="TRANSACTION_RULE",
        canonical_key="designated_for_assignment", title="Designated for Assignment",
        aliases=("DFA", "指定让渡"), language="en", summary="A roster move removing a player.",
        structured_payload={"abbreviation": "DFA"}, source_refs=("mlb_glossary",),
        source_authority="OFFICIAL", tags=("roster",), freshness_policy="CBA",
        verification_status="VERIFIED", status="ACTIVE", effective_from=date(2020, 1, 1),
    )
    return KnowledgeItem(**(fields | changes))


def source(source_id: str = "mlb_glossary", **changes) -> KnowledgeSource:
    fields = dict(source_id=source_id, name="MLB Glossary", root_url="https://www.mlb.com/glossary",
                  publisher="MLB", source_type="GLOSSARY", authority_level="OFFICIAL",
                  refresh_policy="SEASONAL", last_checked=date(2026, 9, 15))
    return KnowledgeSource(**(fields | changes))


class KnowledgeStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = SqliteKnowledgeStore(":memory:")

    def tearDown(self):
        self.store.close()

    def test_upsert_get_and_canonical_lookup(self):
        stored = self.store.upsert_item(item())
        self.assertEqual(stored.version, 1)
        self.assertEqual(self.store.get_item("TERM:DFA").title, "Designated for Assignment")
        self.assertEqual(self.store.get_by_canonical_key("designated_for_assignment").knowledge_id, "TERM:DFA")
        self.assertIsNone(self.store.get_item("missing"))

    def test_identical_upsert_does_not_bump_version(self):
        self.store.upsert_item(item())
        second = self.store.upsert_item(item())
        self.assertEqual(second.version, 1)
        self.assertEqual(len(self.store.history("TERM:DFA")), 1)

    def test_content_change_bumps_version_and_preserves_history(self):
        self.store.upsert_item(item())
        updated = self.store.upsert_item(item(summary="Updated definition."))
        self.assertEqual(updated.version, 2)
        history = self.store.history("TERM:DFA")
        self.assertEqual([entry.version for entry in history], [1, 2])
        self.assertEqual(history[0].summary, "A roster move removing a player.")

    def test_status_change_is_versioned(self):
        self.store.upsert_item(item(status="COLLECTED", verification_status="COLLECTED"))
        promoted = self.store.upsert_item(item(status="ACTIVE", verification_status="VERIFIED"))
        self.assertEqual(promoted.version, 2)
        self.assertEqual(promoted.status, "ACTIVE")

    def test_list_search_counts(self):
        self.store.upsert_item(item())
        self.store.upsert_item(item(knowledge_id="TEAM:LAD", knowledge_type="TEAM",
                                    canonical_key="LAD", title="Los Angeles Dodgers",
                                    aliases=("Dodgers", "道奇"), summary="NL West club.",
                                    structured_payload={}))
        self.assertEqual(len(self.store.list_items()), 2)
        self.assertEqual(self.store.list_items(knowledge_type="TEAM")[0].canonical_key, "LAD")
        self.assertEqual(self.store.counts_by_type(), {"TEAM": 1, "TRANSACTION_RULE": 1})
        self.assertEqual(self.store.counts_by_status(), {"ACTIVE": 2})
        self.assertEqual([entry.knowledge_id for entry in self.store.search_items("道奇")], ["TEAM:LAD"])
        self.assertEqual([entry.knowledge_id for entry in self.store.search_items("DFA")], ["TERM:DFA"])
        self.assertEqual(self.store.search_items("no-such-term"), ())
        self.assertEqual(self.store.item_count(), 2)

    def test_sources_are_persisted_and_listed(self):
        self.store.upsert_source(source())
        self.store.upsert_source(source("reddit_baseball", authority_level="COMMUNITY",
                                        source_type="FORUM", active=False))
        self.assertEqual(self.store.get_source("mlb_glossary").authority_level, "OFFICIAL")
        self.assertEqual(len(self.store.list_sources()), 2)
        self.assertEqual([entry.source_id for entry in self.store.list_sources(active_only=True)],
                         ["mlb_glossary"])

    def test_relations_traverse_both_directions(self):
        self.store.upsert_relation(KnowledgeRelation(relation_id="rel-1", from_key="TEAM:LAD",
                                                     relation_type="MEMBER_OF", to_key="DIV:NLW"))
        self.store.upsert_relation(KnowledgeRelation(relation_id="rel-2", from_key="TEAM:LAD",
                                                     relation_type="HOME_BALLPARK", to_key="PARK:LAD"))
        self.assertEqual([r.to_key for r in self.store.relations(from_key="TEAM:LAD")],
                         ["DIV:NLW", "PARK:LAD"])
        self.assertEqual([r.to_key for r in self.store.relations(from_key="TEAM:LAD",
                                                                 relation_type="MEMBER_OF")],
                         ["DIV:NLW"])
        self.assertEqual([r.from_key for r in self.store.relations(to_key="DIV:NLW")], ["TEAM:LAD"])

    def test_snapshot_lifecycle(self):
        snapshot = self.store.save_snapshot(KnowledgeSnapshot(snapshot_id="snap-1", label="seed",
                                                              counts={"TEAM": 30}))
        self.assertFalse(snapshot.activated)
        activated = self.store.activate_snapshot("snap-1")
        self.assertTrue(activated.activated)
        self.assertEqual([s.snapshot_id for s in self.store.list_snapshots()], ["snap-1"])
        self.assertIsNone(self.store.activate_snapshot("missing"))

    def test_temporal_validity(self):
        historical = item(effective_from=date(2020, 1, 1), effective_to=date(2022, 12, 31))
        self.assertTrue(historical.is_current_on(date(2021, 6, 1)))
        self.assertFalse(historical.is_current_on(date(2024, 6, 1)))
        self.assertFalse(historical.is_current_on(date(2019, 1, 1)))

    def test_file_backed_store_survives_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "knowledge.db"
            store = SqliteKnowledgeStore(path)
            store.upsert_item(item())
            store.close()
            reopened = SqliteKnowledgeStore(path)
            try:
                self.assertEqual(reopened.get_item("TERM:DFA").version, 1)
            finally:
                reopened.close()


class FakeCursor:
    def __init__(self, log: list[str]) -> None:
        self._log = log

    def execute(self, sql, params=()):
        self._log.append(sql)
        return self

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def close(self):
        return None


class FakeConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def cursor(self):
        return FakeCursor(self.statements)

    def commit(self):
        return None

    def close(self):
        return None


class PostgresKnowledgeStoreTests(unittest.TestCase):
    def test_uses_a_dedicated_knowledge_schema(self):
        connection = FakeConnection()
        PostgresKnowledgeStore(connection)
        joined = " ".join(connection.statements)
        self.assertIn("CREATE SCHEMA IF NOT EXISTS knowledge", joined)
        self.assertIn("knowledge.sources", joined)
        self.assertIn("knowledge.items", joined)
        self.assertIn("knowledge.versions", joined)
        self.assertNotIn("baseball_analytics", joined)


if __name__ == "__main__":
    unittest.main()
