"""KnowledgeBase facade: the single entry point for shared knowledge.

Callers (CLI, ContextService, projections) use this instead of touching store tables.
"""

from datetime import date
from typing import Callable

from app.knowledge.freshness import age_days, is_stale, max_age_days
from app.knowledge.ingestion import KnowledgeIngester, KnowledgePack
from app.knowledge.registry import SourceRegistry
from app.knowledge.retrieval import KnowledgeRetriever
from app.models.knowledge import KnowledgeDiff, KnowledgeItem, KnowledgeQuery


class KnowledgeBase:
    def __init__(self, store, today: Callable[[], date] = date.today) -> None:
        self.store = store
        self._today = today
        self.retriever = KnowledgeRetriever(store, today=today)
        self.ingester = KnowledgeIngester(store, today=today)
        self.sources = SourceRegistry.from_store(store)

    # -- write ---------------------------------------------------------------
    def ingest(self, pack: KnowledgePack, *, activate: bool = True) -> KnowledgeDiff:
        return self.ingester.ingest(pack, activate=activate)

    def reload_sources(self) -> SourceRegistry:
        self.sources = SourceRegistry.from_store(self.store)
        return self.sources

    # -- read ----------------------------------------------------------------
    def get(self, identifier: str) -> KnowledgeItem | None:
        return self.retriever.lookup(identifier)

    def search(self, query: str, **changes) -> tuple:
        return self.retriever.retrieve(KnowledgeQuery(query=query, **changes))

    def items(self, **filters) -> tuple[KnowledgeItem, ...]:
        return self.store.list_items(**filters)

    # -- status --------------------------------------------------------------
    def status(self) -> dict:
        today = self._today()
        items = self.store.list_items()
        stale = [item for item in items if is_stale(item, today)]
        by_type = self.store.counts_by_type()
        by_status = self.store.counts_by_status()
        verified = [item for item in items if item.verification_status == "VERIFIED"]
        ages = [age_days(item, today) for item in items if age_days(item, today) is not None]
        return {
            "total_items": len(items),
            "by_type": by_type,
            "by_status": by_status,
            "sources": len(self.store.list_sources()),
            "verified_items": len(verified),
            "stale_items": len(stale),
            "oldest_verified_days": max(ages) if ages else None,
            "latest_snapshot": (self.store.list_snapshots() or (None,))[-1],
            "freshness_policies": {policy: max_age_days(policy)
                                   for policy in sorted({item.freshness_policy for item in items})},
        }
