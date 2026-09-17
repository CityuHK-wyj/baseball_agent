import unittest

from app.context.service import ContextItem, ContextRequest, ContextService, StaticContextSource


def item(item_id: str, kind: str = "METRIC", **changes) -> ContextItem:
    fields = dict(item_id=item_id, kind=kind, title=f"title-{item_id}", source="test", content="body")
    return ContextItem(**(fields | changes))


class ContextServiceTests(unittest.TestCase):
    def setUp(self):
        self.metrics = StaticContextSource("METRIC", (item("m1"), item("m2")))
        self.reports = StaticContextSource("COMPLETION_REPORT", (item("r1", kind="COMPLETION_REPORT"),))
        self.service = ContextService((self.metrics, self.reports))

    def test_retrieves_by_kind_from_matching_sources(self):
        package = self.service.retrieve(ContextRequest(request_id="q1", kinds=("METRIC",)))
        self.assertEqual([entry.item_id for entry in package.items], ["m1", "m2"])
        self.assertEqual(package.total_available, 2)
        self.assertFalse(package.truncated)

    def test_empty_kinds_queries_all_sources(self):
        package = self.service.retrieve(ContextRequest(request_id="q1"))
        self.assertEqual({entry.kind for entry in package.items}, {"METRIC", "COMPLETION_REPORT"})

    def test_response_excludes_failed_attempts_rejected_evidence_and_plans(self):
        source = StaticContextSource("ATTEMPT", (item("attempt-1", kind="ATTEMPT"),))
        rejected = StaticContextSource("REJECTED_EVIDENCE", (item("rej-1", kind="REJECTED_EVIDENCE"),))
        plans = StaticContextSource("PLAN", (item("plan-1", kind="PLAN"),))
        service = ContextService((source, rejected, plans, self.metrics))
        response = service.retrieve(ContextRequest(request_id="q1", purpose="RESPONSE"))
        self.assertEqual([entry.item_id for entry in response.items], ["m1", "m2"])
        planner = service.retrieve(ContextRequest(request_id="q2", purpose="PLANNER"))
        kinds = {entry.kind for entry in planner.items}
        self.assertIn("METRIC", kinds)
        self.assertNotIn("ATTEMPT", kinds)
        self.assertNotIn("REJECTED_EVIDENCE", kinds)

    def test_non_accepted_items_are_never_projected(self):
        source = StaticContextSource("METRIC", (item("m1"), item("m2", accepted=False)))
        service = ContextService((source,))
        for purpose in ("PLANNER", "RESPONSE"):
            with self.subTest(purpose=purpose):
                package = service.retrieve(ContextRequest(request_id="q", purpose=purpose))
                self.assertEqual([entry.item_id for entry in package.items], ["m1"])

    def test_freshness_ranking_and_truncation(self):
        source = StaticContextSource("METRIC", (item("old", freshness_rank=5), item("new", freshness_rank=0)))
        service = ContextService((source,))
        package = service.retrieve(ContextRequest(request_id="q", max_items=1))
        self.assertEqual([entry.item_id for entry in package.items], ["new"])
        self.assertTrue(package.truncated)
        self.assertEqual(package.total_available, 2)

    def test_entity_filter_and_deduplication(self):
        first = StaticContextSource("METRIC", (item("m1", entity_ref="player:1"),))
        second = StaticContextSource("SCHEMA", (item("m1", kind="SCHEMA", entity_ref="player:1"),))
        service = ContextService((first, second))
        package = service.retrieve(ContextRequest(request_id="q", entity_refs=("player:1",)))
        self.assertEqual(len(package.items), 1, "duplicate item ids collapse")
        filtered = service.retrieve(ContextRequest(request_id="q", entity_refs=("player:2",)))
        self.assertEqual(filtered.items, ())

    def test_unknown_kind_returns_an_empty_package(self):
        package = self.service.retrieve(ContextRequest(request_id="q", kinds=("SOURCE_MAPPING",)))
        self.assertEqual(package.items, ())
        self.assertFalse(package.truncated)

    def test_source_rejects_mismatched_item_kind(self):
        with self.assertRaises(ValueError):
            StaticContextSource("METRIC", (item("x", kind="SCHEMA"),))


if __name__ == "__main__":
    unittest.main()
