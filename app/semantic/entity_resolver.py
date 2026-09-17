"""Canonical entity resolution.

Names are for communication; stable ids are for joining. The resolver returns candidates
and only commits to a canonical entity when one is unambiguously strongest; otherwise it
asks for clarification.
"""

from collections.abc import Callable

from app.models.clarification import ClarificationOption, ClarificationRequest
from app.models.entities import CanonicalEntity, EntityCandidate, EntityResolution


class EntityDictionary:
    def __init__(self, entities: tuple[CanonicalEntity, ...] = ()) -> None:
        self._entities: tuple[CanonicalEntity, ...] = ()
        for entity in entities:
            self.add(entity)

    def add(self, entity: CanonicalEntity) -> None:
        if any(item.entity_key == entity.entity_key for item in self._entities):
            raise ValueError(f"Duplicate entity key {entity.entity_key!r}")
        if any(item.entity_key != entity.entity_key and item.display_name == entity.display_name
               for item in self._entities):
            # Not an error:同名 is exactly the ambiguity the resolver must surface.
            pass
        self._entities += (entity,)

    def entities(self) -> tuple[CanonicalEntity, ...]:
        return self._entities

    def get(self, entity_key: str) -> CanonicalEntity:
        for item in self._entities:
            if item.entity_key == entity_key:
                return item
        raise KeyError(f"Unknown entity {entity_key!r}")

    def candidates(self, mention: str, entity_type: str | None = None) -> tuple[EntityCandidate, ...]:
        needle = mention.casefold().strip()
        found: list[EntityCandidate] = []
        for entity in self._entities:
            if entity_type is not None and entity.entity_type != entity_type:
                continue
            kind, confidence = self._match(needle, entity)
            if kind is not None:
                found.append(EntityCandidate(entity=entity, match_kind=kind, confidence=confidence))
        return tuple(sorted(found, key=lambda item: (-item.confidence, item.entity.entity_key)))

    @staticmethod
    def _match(needle: str, entity: CanonicalEntity):
        if needle == entity.entity_key.casefold():
            return "EXACT_KEY", 1.0
        if needle == entity.display_name.casefold():
            return "DISPLAY_NAME", 0.9
        if any(needle == alias.casefold() for alias in entity.aliases):
            return "ALIAS", 0.85
        surfaces = (entity.display_name, *entity.aliases)
        if len(needle) >= 3 and any(needle in surface.casefold() or surface.casefold() in needle
                                    for surface in surfaces):
            return "CONTAINS", 0.6
        return None, 0.0


class EntityResolver:
    def __init__(self, dictionary: EntityDictionary,
                 id_factory: Callable[[str], str] | None = None,
                 max_options: int = 4) -> None:
        self._dictionary = dictionary
        self._id_factory = id_factory or (lambda prefix: f"{prefix}-{abs(hash(prefix))}")
        self._max_options = max_options

    def resolve(self, mention: str, entity_type: str | None = None) -> EntityResolution:
        candidates = self._dictionary.candidates(mention, entity_type)
        if not candidates:
            return EntityResolution(mention=mention, needs_clarification=True,
                                    reason="No known entity matches this mention")
        top_confidence = candidates[0].confidence
        top = tuple(item for item in candidates if item.confidence == top_confidence)
        if len(top) == 1:
            return EntityResolution(mention=mention, candidates=candidates, canonical=top[0].entity)
        return EntityResolution(mention=mention, candidates=candidates, needs_clarification=True,
                                reason="Multiple equally plausible entities match this mention")

    def propose_clarification(self, resolution: EntityResolution) -> ClarificationRequest | None:
        if not resolution.needs_clarification:
            return None
        options = tuple(
            ClarificationOption(option_id=f"opt-{index}", label=candidate.entity.display_name,
                                value=candidate.entity.entity_key, rationale=candidate.match_kind)
            for index, candidate in enumerate(resolution.candidates[:self._max_options]))
        entity_type = resolution.candidates[0].entity.entity_type.lower() if resolution.candidates else "entity"
        return ClarificationRequest(
            clarification_id=self._id_factory("clarification"), kind="ENTITY",
            question=f"Which {entity_type} did you mean by '{resolution.mention}'?",
            reason=resolution.reason, options=options,
            recommended_option_id=options[0].option_id if options else None,
            affected_ref=resolution.mention)
