"""Retrieval ranking, authority, temporal validity, alias and relation traversal."""

import unittest
from datetime import date

from app.knowledge.retrieval import KnowledgeRetriever
from app.knowledge.store import SqliteKnowledgeStore
from app.models.knowledge import KnowledgeItem, KnowledgeQuery, KnowledgeRelation


def item(knowledge_id: str, canonical_key: str, **changes) -> KnowledgeItem:
    fields = dict(
        knowledge_id=knowledge_id, knowledge_type="TERM", canonical_key=canonical_key,
        title=canonical_key.replace("_", " ").title(), language="en", summary="",
        source_authority="OFFICIAL", source_refs=("mlb",), verification_status="VERIFIED",
        status="ACTIVE", freshness_policy="LOW",
    )
    return KnowledgeItem(**(fields | changes))


class RetrieverTests(unittest.TestCase):
    def setUp(self):
        self.store = SqliteKnowledgeStore(":memory:")
        self.store.upsert_item(item("RULE:BALK", "balk", knowledge_type="RULE",
                                    title="Balk", aliases=("投手犯规",),
                                    summary="An illegal pitching action with runners on base.",
                                    structured_payload={"rule_reference": "6.02(a)"}))
        self.store.upsert_item(item("TERM:DFA", "designated_for_assignment",
                                    title="Designated for Assignment", aliases=("DFA", "指定让渡"),
                                    summary="A roster move removing a player from the 40-man roster."))
        self.store.upsert_item(item("TEAM:LAD", "LAD", knowledge_type="TEAM",
                                    title="Los Angeles Dodgers", aliases=("Dodgers", "道奇"),
                                    summary="National League West club.",
                                    entity_refs=("TEAM:LAD",)))
        self.store.upsert_item(item("METRIC:WRC_PLUS", "wrc_plus", knowledge_type="METRIC",
                                    title="wRC+", aliases=("Weighted Runs Created Plus",),
                                    summary="Park- and league-adjusted offensive value, 100 = average.",
                                    structured_payload={"definition": "adjusted runs created",
                                                        "provider": "FanGraphs"}))
        self.store.upsert_item(item("COMM:REDDIT", "community_sentiment", knowledge_type="TERM",
                                    title="Community sentiment", aliases=(),
                                    source_authority="COMMUNITY", source_refs=("reddit",)))
        self.retriever = KnowledgeRetriever(self.store, today=lambda: date(2026, 9, 15))

    def tearDown(self):
        self.store.close()

    def test_canonical_and_alias_lookup(self):
        self.assertEqual(self.retriever.lookup("designated_for_assignment").knowledge_id, "TERM:DFA")
        self.assertEqual(self.retriever.lookup("DFA").knowledge_id, "TERM:DFA")
        self.assertEqual(self.retriever.lookup("道奇").knowledge_id, "TEAM:LAD")
        self.assertEqual([entry.knowledge_id for entry in self.retriever.match_alias("指定让渡")], ["TERM:DFA"])
        self.assertIsNone(self.retriever.lookup("no-such-thing"))

    def test_natural_bilingual_question_finds_embedded_alias(self):
        matches = self.retriever.retrieve(KnowledgeQuery(query="道奇属于哪个分区？"))
        self.assertEqual(matches[0].item.knowledge_id, "TEAM:LAD")
        matches = self.retriever.retrieve(KnowledgeQuery(query="DFA是什么意思？"))
        self.assertEqual(matches[0].item.knowledge_id, "TERM:DFA")

    def test_runtime_context_respects_requested_historical_date(self):
        from app.context.knowledge_source import KnowledgeContextSource
        from app.context.service import ContextRequest, ContextService
        from app.knowledge.service import KnowledgeBase
        self.store.upsert_item(item("RULE:OLD", "old_rule", knowledge_type="RULE",
            aliases=("special rule",), effective_to=date(2021, 12, 31)))
        self.store.upsert_item(item("RULE:NEW", "new_rule", knowledge_type="RULE",
            aliases=("special rule",), effective_from=date(2022, 1, 1)))
        service = ContextService((KnowledgeContextSource(KnowledgeBase(self.store)),))
        package = service.retrieve(ContextRequest(request_id="past", query="special rule",
                                                  as_of=date(2021, 6, 1)))
        self.assertEqual([entry.item_id for entry in package.items], ["RULE:OLD"])

    def test_current_snapshot_without_effective_dates_is_not_a_historical_fact(self):
        self.store.upsert_item(item("TEAM:NEW", "new_club", knowledge_type="TEAM",
            aliases=("Club",), summary="Current sponsored stadium name.", as_of=date(2026, 9, 15)))
        matches = self.retriever.retrieve(KnowledgeQuery(query="Club", as_of=date(2021, 7, 1)))
        self.assertNotIn("TEAM:NEW", [match.item.knowledge_id for match in matches])

    def test_free_text_prefers_official_over_community(self):
        matches = self.retriever.retrieve(KnowledgeQuery(query="sentiment", max_items=5))
        official_rank = {match.item.knowledge_id: match.score for match in matches}
        self.assertIn("COMM:REDDIT", official_rank)
        self.assertIn("COMMUNITY_SOURCE", next(m.reasons for m in matches if m.item.knowledge_id == "COMM:REDDIT"))

    def test_authority_floor_filters_community(self):
        matches = self.retriever.retrieve(KnowledgeQuery(
            query="community_sentiment", authority_floor="AUTHORITATIVE_REFERENCE"))
        self.assertEqual(matches, ())

    def test_type_and_language_filters(self):
        matches = self.retriever.retrieve(KnowledgeQuery(query="", knowledge_types=("METRIC",)))
        self.assertEqual([match.item.knowledge_id for match in matches], ["METRIC:WRC_PLUS"])
        english = self.retriever.retrieve(KnowledgeQuery(query="", language="en"))
        self.assertTrue(all(match.item.language == "en" for match in english))

    def test_temporal_validity_excludes_expired_items(self):
        self.store.upsert_item(item("RULE:OLD_DH", "old_dh", knowledge_type="RULE",
                                    title="Pre-2022 NL DH absence",
                                    effective_from=date(2000, 1, 1), effective_to=date(2021, 12, 31),
                                    structured_payload={"rule_reference": "5.11"}))
        as_of_2021 = self.retriever.retrieve(KnowledgeQuery(query="old_dh", as_of=date(2021, 6, 1)))
        self.assertEqual([m.item.knowledge_id for m in as_of_2021], ["RULE:OLD_DH"])
        as_of_2026 = self.retriever.retrieve(KnowledgeQuery(query="old_dh", as_of=date(2026, 6, 1)))
        self.assertEqual(as_of_2026, ())

    def test_relation_traversal(self):
        self.store.upsert_item(item("DIV:NLW", "NLW", knowledge_type="DIVISION",
                                    title="National League West"))
        self.store.upsert_relation(KnowledgeRelation(relation_id="r1", from_key="TEAM:LAD",
                                                     relation_type="MEMBER_OF", to_key="DIV:NLW"))
        related = self.retriever.related("TEAM:LAD", "MEMBER_OF")
        self.assertEqual([entry.knowledge_id for entry in related], ["DIV:NLW"])


if __name__ == "__main__":
    unittest.main()
