"""Projection of Shared Knowledge into the Entity Dictionary and Metric Registry.

These are projections, not second copies: knowledge is canonical identity. Adding a team
or player to the knowledge store makes it resolvable; the resolver never owns aliases.
"""

from app.models.entities import CanonicalEntity
from app.models.metrics import MetricDefinition, SourceMapping
from app.semantic.entity_resolver import EntityDictionary
from app.semantic.metric_registry import MetricRegistry

_ENTITY_TYPE_BY_KNOWLEDGE = {"TEAM": "TEAM", "LEAGUE": "LEAGUE", "PLAYER_PROFILE": "PLAYER"}


def _entity_key(item) -> str:
    if item.knowledge_type == "TEAM":
        return f"TEAM:{item.canonical_key}"
    if item.knowledge_type == "LEAGUE":
        return f"LEAGUE:{item.canonical_key}"
    return item.canonical_key  # player items use a stable MLBAM:<id> canonical key


def entity_dictionary_from_knowledge(store, *, statuses: tuple[str, ...] = ("ACTIVE",)) -> EntityDictionary:
    entities = []
    for item in store.list_items():
        entity_type = _ENTITY_TYPE_BY_KNOWLEDGE.get(item.knowledge_type)
        if entity_type is None or item.status not in statuses:
            continue
        aliases = tuple(dict.fromkeys((*item.aliases, item.title)))
        entities.append(CanonicalEntity(
            entity_key=_entity_key(item), entity_type=entity_type, display_name=item.title,
            aliases=tuple(alias for alias in aliases if alias != item.title),
            source_ids=item.source_refs))
    return EntityDictionary(tuple(entities))


def metric_registry_from_knowledge(store, *, statuses: tuple[str, ...] = ("ACTIVE",)) -> MetricRegistry:
    """Build the canonical metric contract from knowledge METRIC items."""
    definitions, mappings = [], []
    for item in store.list_items(knowledge_type="METRIC"):
        if item.status not in statuses:
            continue
        payload = item.structured_payload
        definitions.append(MetricDefinition(
            metric_key=item.canonical_key, display_name=item.title, description=item.summary,
            required_data_keys=tuple(payload.get("required_data_keys", ())),
            unit=str(payload.get("unit", ""))))
        mapping = payload.get("source_mapping")
        if mapping:
            mappings.append(SourceMapping(
                metric_key=item.canonical_key, source_kind=mapping["source_kind"],
                location=mapping["location"], computation=mapping.get("computation", "DIRECT")))
    return MetricRegistry(tuple(definitions), tuple(mappings))
