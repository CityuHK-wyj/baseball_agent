"""Entity resolution across the whole system: local, MLB registry, then evidence text.

The runtime never silently invents an entity identity. A resolved result carries its
namespace and stable identifier; an unresolved mention is returned as such and may be
routed to web research by the Planner.
"""

from dataclasses import dataclass

from app.models.entities import CanonicalEntity, EntityCandidate
from app.semantic.entity_resolver import EntityDictionary, EntityResolver


@dataclass(frozen=True)
class EntityLookupResult:
    mention: str
    canonical: CanonicalEntity | None = None
    candidates: tuple[CanonicalEntity, ...] = ()
    source: str = ""
    reason: str = ""

    @property
    def resolved(self) -> bool:
        return self.canonical is not None

    @property
    def ambiguous(self) -> bool:
        return len({item.entity_key for item in self.candidates}) > 1


class EntityLookup:
    def __init__(self, dictionary: EntityDictionary, resolver: EntityResolver,
                 mlb_search=None) -> None:
        self._dictionary = dictionary
        self._resolver = resolver
        self._mlb_search = mlb_search

    @property
    def dictionary(self) -> EntityDictionary:
        return self._dictionary

    def resolve(self, mention: str, evidence_text: str = "") -> EntityLookupResult:
        local = self._resolver.resolve(mention)
        if local.canonical is not None and not local.needs_clarification:
            return EntityLookupResult(mention=mention, canonical=local.canonical,
                                      candidates=tuple(item.entity for item in local.candidates),
                                      source="LOCAL_DICTIONARY")
        local_candidates = tuple(item.entity for item in local.candidates)
        # Local-first: a genuine local ambiguity is preserved without a remote registry call.
        # The provider is consulted only when the local dictionary yielded no candidate at
        # all, so ambiguity does not incur network latency and cannot be silently resolved
        # by a remote first match.
        if (self._mlb_search is not None and mention.isascii() and not local_candidates):
            found = self._mlb_search(mention)
            if found:
                return EntityLookupResult(mention=mention, canonical=found, source="MLB_REGISTRY")
        scanned = self._scan(evidence_text)
        if len(scanned) == 1:
            return EntityLookupResult(mention=mention, canonical=scanned[0],
                                      candidates=scanned, source="EVIDENCE")
        if scanned:
            return EntityLookupResult(mention=mention, candidates=scanned, source="EVIDENCE",
                                      reason="ambiguous evidence match")
        if local_candidates:
            # Preserve a genuine dictionary ambiguity instead of silently dropping it.
            return EntityLookupResult(mention=mention, candidates=local_candidates,
                                      source="LOCAL_DICTIONARY",
                                      reason=local.reason or "ambiguous dictionary match")
        return EntityLookupResult(mention=mention, reason="not found locally or in evidence")

    def scan(self, text: str) -> tuple[CanonicalEntity, ...]:
        return self._scan(text)

    def _scan(self, text: str) -> tuple[CanonicalEntity, ...]:
        if not text:
            return ()
        haystack = text.casefold()
        found: list[CanonicalEntity] = []
        for entity in self._dictionary.entities():
            for surface in (entity.display_name, *entity.aliases):
                if len(surface) >= 2 and surface.casefold() in haystack:
                    found.append(entity)
                    break
        return tuple({item.entity_key: item for item in found}.values())


class MLBPeopleSearch:
    """Live MLB StatsAPI people search (English names). Returns one candidate or None."""

    def __init__(self, timeout: float = 12.0) -> None:
        self._timeout = timeout

    def __call__(self, name: str) -> CanonicalEntity | None:
        try:
            import requests
            response = requests.get(
                "https://statsapi.mlb.com/api/v1/people/search",
                params={"names": name},
                headers={"User-Agent": "baseball-agent/0.2",
                         "Accept": "application/json"}, timeout=self._timeout)
            if response.status_code != 200:
                return None
            people = response.json().get("people", [])
        except Exception:  # noqa: BLE001 - registry lookup is best-effort
            return None
        if not people:
            return None
        person = people[0]
        return CanonicalEntity(
            entity_key=f"MLBAM:{person['id']}", entity_type="PLAYER",
            display_name=person.get("fullName") or name)
