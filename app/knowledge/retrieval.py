"""Knowledge retrieval: structured lookup first, optional ranking second.

Query types are answered with the cheapest correct path:

- canonical id / alias -> exact lookup
- structured filters -> store filter
- free text -> candidate filter plus deterministic token ranking
- relation traversal -> ``relations``

Ranking considers authority, entity match, token relevance, temporal validity, freshness
and language. It is deterministic and inspectable; there is no embedding involved.
"""

import re
from datetime import date
from typing import Callable

from app.knowledge.freshness import freshness_rank, is_stale
from app.models.knowledge import (
    AUTHORITY_RANK,
    KnowledgeItem,
    KnowledgeMatch,
    KnowledgeQuery,
)

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.casefold()))


def _ordered_tokens(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_TOKEN.findall(text.casefold())))


def _mentions(surface: str, query: str) -> bool:
    surface = surface.casefold().strip()
    if len(surface) < 2:
        return False
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(surface) + r"(?![a-z0-9])", query.casefold()))


class KnowledgeRetriever:
    def __init__(self, store, today: Callable[[], date] = date.today) -> None:
        self._store = store
        self._today = today

    # -- exact lookup ----------------------------------------------------------
    def lookup(self, identifier: str) -> KnowledgeItem | None:
        """Resolve a knowledge id, canonical key, title or alias to one item."""
        exact = self._store.get_item(identifier) or self._store.get_by_canonical_key(identifier)
        if exact is not None:
            return exact
        matches = self.match_alias(identifier)
        return matches[0] if matches else None

    def match_alias(self, mention: str) -> tuple[KnowledgeItem, ...]:
        needle = mention.casefold().strip()
        if not needle:
            return ()
        found = [item for item in self._store.search_items(mention)
                 if any(surface.casefold() == needle for surface in item.names())]
        return tuple(sorted(found, key=lambda item: (-AUTHORITY_RANK[item.source_authority],
                                                      item.knowledge_id)))

    # -- ranked retrieval ------------------------------------------------------
    def retrieve(self, query: KnowledgeQuery) -> tuple[KnowledgeMatch, ...]:
        today = query.as_of or self._today()
        candidates = self._candidates(query)
        matches: list[KnowledgeMatch] = []
        for item in candidates:
            if not self._passes_filters(item, query, today):
                continue
            score, reasons = self._score(item, query, today)
            matches.append(KnowledgeMatch(item=item, score=score, reasons=reasons))
        matches.sort(key=lambda match: (-match.score, match.item.knowledge_id))
        return tuple(matches[:query.max_items])

    def _candidates(self, query: KnowledgeQuery) -> tuple[KnowledgeItem, ...]:
        if not query.query.strip():
            return self._store.list_items()
        needle = query.query.strip()
        candidates = list(self._store.search_items(needle))
        seen = {item.knowledge_id for item in candidates}
        for item in self._store.list_items():
            if item.knowledge_id not in seen and any(_mentions(name, needle) for name in item.names()):
                candidates.append(item)
        # An exact id/canonical key must be a candidate even if the LIKE filter missed it.
        exact = self._store.get_item(needle) or self._store.get_by_canonical_key(needle)
        if exact is not None and all(entry.knowledge_id != exact.knowledge_id for entry in candidates):
            candidates.append(exact)
        return tuple(candidates)

    def _passes_filters(self, item: KnowledgeItem, query: KnowledgeQuery, today: date) -> bool:
        if item.status not in query.statuses:
            return False
        if query.knowledge_types and item.knowledge_type not in query.knowledge_types:
            return False
        if query.language is not None and item.language != query.language:
            return False
        if query.community is not None and query.community not in item.tags:
            return False
        if query.as_of is not None and not item.is_current_on(today):
            return False
        if (query.as_of is not None and item.as_of is not None and query.as_of < item.as_of
                and item.effective_from is None):
            # A current snapshot with no historical validity cannot establish a past fact.
            return False
        if query.authority_floor is not None:
            if AUTHORITY_RANK[item.source_authority] < AUTHORITY_RANK[query.authority_floor]:
                return False
        if query.entity_refs and not set(query.entity_refs) & set(item.entity_refs):
            return False
        if query.tags and not set(query.tags) & set(item.tags):
            return False
        return True

    def _score(self, item: KnowledgeItem, query: KnowledgeQuery, today: date) -> tuple[float, tuple[str, ...]]:
        score = 0.0
        reasons: list[str] = []
        needle = query.query.casefold().strip()

        if needle:
            surfaces = {surface.casefold() for surface in item.names()}
            if item.canonical_key.casefold() == needle:
                score += 6.0
                reasons.append("CANONICAL_KEY_MATCH")
            elif item.title.casefold() == needle:
                score += 5.0
                reasons.append("TITLE_MATCH")
            elif needle in surfaces:
                score += 5.0
                reasons.append("ALIAS_MATCH")
            elif any(_mentions(surface, needle) for surface in surfaces):
                score += 4.0
                reasons.append("MENTION_MATCH")

            wanted = _tokens(query.query)
            if wanted:
                haystack = _tokens(" ".join(item.names()) + " " + item.summary + " "
                                   + " ".join(str(value) for value in item.structured_payload.values()))
                overlap = wanted & haystack
                if overlap:
                    score += 3.0 * (len(overlap) / len(wanted))
                    reasons.append("TOKEN_OVERLAP")

        authority_rank = AUTHORITY_RANK[item.source_authority]
        score += 0.4 * authority_rank
        if authority_rank >= AUTHORITY_RANK["OFFICIAL"]:
            reasons.append("OFFICIAL_SOURCE")
        elif authority_rank <= AUTHORITY_RANK["COMMUNITY"]:
            reasons.append("COMMUNITY_SOURCE")

        if query.entity_refs and set(query.entity_refs) & set(item.entity_refs):
            score += 1.0
            reasons.append("ENTITY_MATCH")

        rank = freshness_rank(item, today)
        score += (3 - rank) * 0.3
        reasons.append("STALE" if is_stale(item, today) else "FRESH")

        if query.language is not None and item.language == query.language:
            score += 0.2
            reasons.append("LANGUAGE_MATCH")

        return round(score, 6), tuple(reasons)

    # -- relation traversal ----------------------------------------------------
    def related(self, key: str, relation_type: str | None = None) -> tuple[KnowledgeItem, ...]:
        relations = self._store.relations(from_key=key, relation_type=relation_type)
        found = []
        for relation in relations:
            item = self.lookup(relation.to_key)
            if item is not None:
                found.append(item)
        return tuple(found)
