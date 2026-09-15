"""ContextSource over the persistent Shared Knowledge store.

Wired into the existing ContextService seam: Shared Knowledge answers a ContextRequest
deterministically, with authority-aware ranking, temporal validity, freshness and
provenance. It is still a capability, not a retrieval agent.
"""

from datetime import date
from typing import Callable

from app.context.service import ContextItem, ContextRequest
from app.knowledge.freshness import freshness_rank
from app.knowledge.service import KnowledgeBase
from app.models.knowledge import KnowledgeQuery, KnowledgeType


class KnowledgeContextSource:
    kind = "KNOWLEDGE"

    def __init__(self, knowledge: KnowledgeBase, *, scope_run: str = "",
                 knowledge_types: tuple[KnowledgeType, ...] = (),
                 authority_floor: str | None = None, as_of: date | None = None,
                 today: Callable[[], date] = date.today) -> None:
        self._knowledge = knowledge
        self._scope_run = scope_run
        self._types = knowledge_types
        self._authority_floor = authority_floor
        self._as_of = as_of
        self._today = today

    def retrieve(self, request: ContextRequest) -> tuple[ContextItem, ...]:
        query = KnowledgeQuery(
            query=request.query, knowledge_types=self._types,
            entity_refs=request.entity_refs, as_of=self._as_of,
            authority_floor=self._authority_floor, max_items=request.max_items)
        matches = self._knowledge.retriever.retrieve(query)
        return tuple(
            ContextItem(
                item_id=match.item.knowledge_id,
                kind=match.item.knowledge_type,
                title=match.item.title,
                content=match.item.summary,
                source=f"knowledge:{match.item.source_authority.lower()}",
                entity_ref=match.item.entity_refs[0] if match.item.entity_refs else "",
                freshness_rank=freshness_rank(match.item, self._as_of or self._today()),
                provenance_ref=f"knowledge:{match.item.knowledge_id}",
                scope_run=self._scope_run)
            for match in matches)
