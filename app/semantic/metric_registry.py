"""Metric Registry: deterministic execution correctness, not semantic discovery.

The registry answers "what is this metric and where does it come from" with exact
lookups. Semantic reasoning belongs to Shared Context retrieval, not here.
"""

import re

from app.models.metrics import MetricDefinition, SourceMapping

_TOKEN = re.compile(r"[a-z0-9_]+")


class MetricRegistry:
    def __init__(self, definitions: tuple[MetricDefinition, ...] = (),
                 mappings: tuple[SourceMapping, ...] = ()) -> None:
        self._definitions = tuple(definitions)
        keys = [item.metric_key for item in self._definitions]
        if len(set(keys)) != len(keys):
            raise ValueError("Metric definition keys must be unique")
        known = set(keys)
        for item in mappings:
            if item.metric_key not in known:
                raise ValueError(f"Source mapping references unknown metric {item.metric_key!r}")
        self._mappings = {item.metric_key: item for item in mappings}

    def get(self, metric_key: str) -> MetricDefinition:
        for item in self._definitions:
            if item.metric_key == metric_key:
                return item
        raise KeyError(f"Unknown metric {metric_key!r}")

    def mapping_for(self, metric_key: str) -> SourceMapping | None:
        return self._mappings.get(metric_key)

    def definitions(self) -> tuple[MetricDefinition, ...]:
        return self._definitions

    def search(self, query: str) -> tuple[MetricDefinition, ...]:
        """Deterministic token search over key, display name and description."""
        tokens = set(_TOKEN.findall(query.lower()))
        if not tokens:
            return self._definitions
        matches = []
        for item in self._definitions:
            haystack = f"{item.metric_key} {item.display_name} {item.description}".lower()
            if tokens & set(_TOKEN.findall(haystack)):
                matches.append(item)
        return tuple(matches)
