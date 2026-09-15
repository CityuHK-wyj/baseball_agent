"""Semantic normalization: raw query -> AnalysisObjective[] (+ clarifications).

The Planner never re-parses raw user intent (D023). This stage resolves entities,
normalizes constraints and extracts objectives. Meaning ambiguity is surfaced as a
ClarificationRequest instead of being silently decided (D045).
"""

from collections.abc import Callable, Iterable
import re

from app.models.clarification import ClarificationRequest
from app.models.contracts import Constraint, Entity
from app.models.entities import CanonicalEntity
from app.models.semantic import SemanticResult
from app.semantic.constraints import normalize_constraints
from app.semantic.entity_resolver import EntityDictionary, EntityResolver
from app.semantic.objective_extractor import ObjectiveExtractor

_NAMESPACE_FALLBACK = "LOCAL"


class SemanticNormalizer:
    def __init__(self, extractor: ObjectiveExtractor, entity_resolver: EntityResolver,
                 dictionary: EntityDictionary | None = None,
                 id_factory: Callable[[str], str] | None = None) -> None:
        self._extractor = extractor
        self._resolver = entity_resolver
        self._dictionary = dictionary or EntityDictionary()
        self._id_factory = id_factory or (lambda prefix: f"{prefix}-{abs(hash(prefix))}")

    def normalize(self, raw_query: str, constraints: Iterable[Constraint] = (),
                  mentions: Iterable[str] | None = None) -> SemanticResult:
        normalized = normalize_constraints(constraints)
        surface_mentions = tuple(mentions) if mentions is not None else self._scan_mentions(raw_query)

        entities: list[Entity] = []
        clarifications: list[ClarificationRequest] = []
        unresolved: list[str] = []
        notes: list[str] = []
        for mention in surface_mentions:
            resolution = self._resolver.resolve(mention)
            if resolution.canonical is not None:
                entities.append(_to_entity(resolution.canonical))
                continue
            unresolved.append(mention)
            request = self._resolver.propose_clarification(resolution)
            if request is not None:
                clarifications.append(request)

        objectives = self._extractor.extract(raw_query, entities=tuple(entities),
                                             constraints=normalized)
        if clarifications:
            notes.append("Objectives are provisional until clarifications are answered.")
        return SemanticResult(raw_query=raw_query, objectives=tuple(objectives),
                              clarifications=tuple(clarifications),
                              unresolved_mentions=tuple(unresolved), notes=tuple(notes))

    def _scan_mentions(self, raw_query: str) -> tuple[str, ...]:
        """Find known entity surfaces that literally occur in the query."""
        lowered = raw_query.casefold()
        found: list[str] = []
        for entity in self._dictionary.entities():
            surfaces = (entity.display_name, *entity.aliases)
            for surface in surfaces:
                min_length = 2 if re.search(r"[\u4e00-\u9fff]", surface) else 3
                if len(surface) >= min_length and surface.casefold() in lowered and surface not in found:
                    found.append(surface)
        return tuple(found)

    def entity_for_key(self, entity_key: str) -> CanonicalEntity:
        return self._dictionary.get(entity_key)


def _to_entity(canonical: CanonicalEntity) -> Entity:
    namespace, _, identifier = canonical.entity_key.partition(":")
    if not identifier:
        namespace, identifier = _NAMESPACE_FALLBACK, canonical.entity_key
    return Entity(namespace=namespace, entity_type=canonical.entity_type, identifier=identifier)
