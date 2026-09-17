"""Open-world entity recovery.

Local dictionary literal scanning is insufficient for nicknames such as ``太鼓达人``.
Recovery is a routing problem, not a failure:

    LLM/heuristic entity mention
        -> local EntityResolver
        -> (this module) Shared Knowledge / Web search
        -> confidence assessment

A strongly supported single candidate continues. Several plausible candidates still
clarify. A mention that cannot be resolved at all is retained as an unresolved concept so
the Planner can route it, rather than being silently discarded.
"""

from collections.abc import Callable
from dataclasses import dataclass

from app.models.entities import CanonicalEntity, EntityCandidate
from app.models.evidence import RawWebResult
from app.semantic.entity_resolver import EntityDictionary, EntityResolver


@dataclass(frozen=True)
class EntityRecoveryResult:
    mention: str
    canonical: CanonicalEntity | None = None
    candidates: tuple[EntityCandidate, ...] = ()
    evidence_text: str = ""
    source_url: str = ""
    recovered: bool = False
    reason: str = ""

    @property
    def ambiguous(self) -> bool:
        return len({item.entity.entity_key for item in self.candidates}) > 1


class EntityRecovery:
    """Resolve a mention locally, then through web evidence if configured."""

    def __init__(self, dictionary: EntityDictionary, resolver: EntityResolver,
                 web_search: Callable[[str], tuple[RawWebResult, ...]] | None = None) -> None:
        self._dictionary = dictionary
        self._resolver = resolver
        self._web_search = web_search

    @property
    def web_available(self) -> bool:
        return self._web_search is not None

    def recover(self, mention: str) -> EntityRecoveryResult:
        local = self._resolver.resolve(mention)
        if local.canonical is not None and not local.needs_clarification:
            return EntityRecoveryResult(mention=mention, canonical=local.canonical,
                                        candidates=local.candidates, recovered=True,
                                        reason="LOCAL")
        if not self.web_available:
            return EntityRecoveryResult(
                mention=mention, candidates=local.candidates,
                reason="NO_LOCAL_MATCH" if not local.candidates else "LOCAL_AMBIGUOUS")
        try:
            documents = self._web_search(mention)  # type: ignore[misc]
        except Exception as error:  # noqa: BLE001 - a web failure must never crash a request
            return EntityRecoveryResult(
                mention=mention, candidates=local.candidates, reason=f"WEB_ERROR:{type(error).__name__}")
        matches = self._scan(documents)
        if len(matches) == 1:
            evidence = documents[0] if documents else None
            return EntityRecoveryResult(
                mention=mention, canonical=matches[0].entity, candidates=matches, recovered=True,
                evidence_text=(evidence.text[:2000] if evidence else ""),
                source_url=(evidence.url if evidence else ""), reason="WEB")
        return EntityRecoveryResult(
            mention=mention, candidates=matches or local.candidates,
            reason="WEB_AMBIGUOUS" if matches else "WEB_NO_MATCH")

    def _scan(self, documents: tuple[RawWebResult, ...]) -> tuple[EntityCandidate, ...]:
        """Find known canonical entities named in the returned web text."""
        found: dict[str, EntityCandidate] = {}
        for document in documents:
            haystack = f"{document.title}\n{document.text}".casefold()
            for entity in self._dictionary.entities():
                surfaces = (entity.display_name, *entity.aliases)
                for surface in surfaces:
                    if len(surface) >= 2 and surface.casefold() in haystack:
                        hit = EntityCandidate(entity=entity, match_kind="ALIAS",
                                              confidence=0.8)
                        found.setdefault(entity.entity_key, hit)
                        break
        return tuple(sorted(found.values(), key=lambda item: (-item.confidence, item.entity.entity_key)))


def recovery_search_hints(mention: str, *, team: str = "") -> tuple[str, ...]:
    """Stable, provider-agnostic search suggestions for an unknown mention."""
    hints = [f"{mention} MLB", f"{mention} baseball"]
    if team:
        hints.append(f"{mention} {team}")
    return tuple(dict.fromkeys(hints))
