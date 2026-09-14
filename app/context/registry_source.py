"""ContextSource backed by the Metric Registry: deterministic lookup, no embeddings."""

from app.context.service import ContextItem, ContextRequest
from app.semantic.metric_registry import MetricRegistry
from app.semantic.schema_registry import SchemaRegistry


class MetricRegistrySource:
    kind = "METRIC"

    def __init__(self, registry: MetricRegistry, scope_run: str = "") -> None:
        self._registry = registry
        self._scope_run = scope_run

    def retrieve(self, request: ContextRequest) -> tuple[ContextItem, ...]:
        return tuple(
            ContextItem(item_id=item.metric_key, kind="METRIC", title=item.display_name,
                        content=item.description, source="metric_registry",
                        scope_run=self._scope_run, provenance_ref=f"metric:{item.metric_key}")
            for item in self._registry.search(request.query)
        )


class SchemaRegistrySource:
    kind = "SCHEMA"

    def __init__(self, registry: SchemaRegistry, scope_run: str = "") -> None:
        self._registry = registry
        self._scope_run = scope_run

    def retrieve(self, request: ContextRequest) -> tuple[ContextItem, ...]:
        return tuple(
            ContextItem(item_id=item.table_name, kind="SCHEMA", title=item.table_name,
                        content=" ".join(item.columns), source="schema_registry",
                        scope_run=self._scope_run, provenance_ref=f"schema:{item.table_name}")
            for item in self._registry.search(request.query)
        )
