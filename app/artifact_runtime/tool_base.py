"""Tool capability contracts and the runtime tool registry.

Tools are *evidence-producing operators*. Each declares what export capabilities it
``accepts`` and ``produces``. The planner composes them by capability, never by
query-specific direction: any Artifact's export can become another tool's input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from app.models.artifact_runtime import (ArtifactExport, RuntimeArtifact, Scope,
                                         ToolCapabilityContract, ToolRequest)
from app.artifact_runtime.artifacts import ArtifactStore
from app.artifact_runtime.references import ReferenceStore
from app.artifact_runtime.schema_catalog import SchemaCatalog, catalog_from_registry


@dataclass
class ToolContext:
    refs: ReferenceStore
    artifacts: ArtifactStore
    today: date
    catalog: SchemaCatalog = field(default_factory=catalog_from_registry)
    knowledge: object = None
    entity_lookup: object = None
    web: object = None
    batting: object = None
    pitching: object = None
    local: object = None
    postgres_executor: object = None
    parquet_executor: object = None
    parquet_glob: str = "mlb_statcast_*.parquet"
    player_names: dict[str, str] = field(default_factory=dict)
    field_mapping: object = None
    roster_provider: Callable[[str], tuple[dict, ...]] | None = None
    candidate_sink: Callable[[dict], None] | None = None
    schema_relation_sql: dict[str, str] = field(default_factory=dict)

    def relation_sql(self, source_kind: str, table: str) -> str | None:
        override = self.schema_relation_sql.get(f"{source_kind}:{table}")
        if override:
            return override
        if source_kind == "PARQUET":
            escaped = self.parquet_glob.replace("'", "''")
            return f"read_parquet('{escaped}')"
        return None


@dataclass(frozen=True)
class ToolOutcome:
    artifacts: tuple[RuntimeArtifact, ...] = ()
    recovery_code: str = ""
    detail: str = ""
    applied_fields: tuple[str, ...] = ()
    referenced_exports: tuple[str, ...] = ()
    receipt: dict = field(default_factory=dict)
    binding_ids: tuple[str, ...] = ()
    external_effect_possible: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.artifacts) and not self.recovery_code


class RuntimeTool:
    """Base class. Subclasses set ``contract`` and implement ``run``."""

    contract: ToolCapabilityContract

    @property
    def name(self) -> str:
        return self.contract.name

    def run(self, request: ToolRequest, context: ToolContext) -> ToolOutcome:  # pragma: no cover
        raise NotImplementedError

    def output_scope(self, request: ToolRequest) -> Scope | None:
        return None


class ToolRegistry:
    def __init__(self, tools: tuple[RuntimeTool, ...] = ()) -> None:
        self._tools: dict[str, RuntimeTool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: RuntimeTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> RuntimeTool | None:
        return self._tools.get(name)

    def all(self) -> tuple[RuntimeTool, ...]:
        return tuple(self._tools.values())

    def find_for(self, *, produces: tuple[str, ...], accepts: tuple[str, ...],
                 ) -> tuple[RuntimeTool, ...]:
        """Tools that can produce one of ``produces`` and accept the available inputs.

        ``accepts`` here is the set of export types currently available from the run's
        artifacts; a tool is compatible when its accepted set is a subset.
        """
        wanted = set(produces)
        available = set(accepts)
        matches = []
        for tool in self._tools.values():
            if wanted and not (set(tool.contract.produces) & wanted):
                continue
            if tool.contract.accepts and not set(tool.contract.accepts) <= available:
                # A tool with input requirements is compatible only when satisfied.
                if not set(tool.contract.accepts) & available and tool.contract.accepts:
                    continue
            matches.append(tool)
        return tuple(matches)


def build_export(artifact: RuntimeArtifact, export_type: str, value,
                 *, text: str = "", references: tuple[str, ...] = (),
                 provenance: str = "", confidence: float = 0.5,
                 metadata: dict | None = None,
                 derived_from: tuple[str, ...] = ()) -> ArtifactExport:
    from app.artifact_runtime.contracts import contract_for
    scope = artifact.actual_scope
    return ArtifactExport(
        export_id=f"{artifact.artifact_id}:{export_type}", export_type=export_type,
        value=value, text=text, references=references, provenance=provenance,
        confidence=confidence, metadata=metadata or {},
        contract=contract_for(export_type, scope=scope), derived_from=derived_from)


def resolve_export_ref(context: ToolContext, ref: str) -> ArtifactExport | None:
    """Resolve a reference id or export id to a reusable ArtifactExport.

    Resolution is intentionally *not* contextual acceptance: the binding layer decides
    which upstream export a downstream action may consume. Here we only refuse exports
    that are explicitly unaccepted or whose owning artifact is rejected/invalid.
    """
    if not ref:
        return None
    reference = context.refs.maybe(ref)
    if reference is not None:
        if reference.ref_type == "ARTIFACT_EXPORT":
            export = context.artifacts.resolve_export(reference.selector)
            return export if _export_usable(context, export) else None
        if reference.ref_type == "ARTIFACT":
            artifact = context.artifacts.maybe(reference.target_id)
            if artifact is not None and artifact.exports:
                candidate = artifact.export("PLAYER_ID_SET") or artifact.exports[0]
                return candidate if _export_usable(context, candidate) else None
    export = context.artifacts.resolve_export(ref)
    return export if _export_usable(context, export) else None


def _export_usable(context: ToolContext, export: ArtifactExport | None) -> bool:
    if export is None or not export.accepted:
        return False
    for artifact in context.artifacts.all():
        if any(item.export_id == export.export_id for item in artifact.exports):
            return artifact.status in ("OK", "PARTIAL")
    return False


def ids_from_inputs(context: ToolContext, request: ToolRequest,
                    export_types: tuple[str, ...] = ("PLAYER_ID_SET",)) -> list[int]:
    """Collect canonical numeric ids from referenced exports, never from city names."""
    ids: list[int] = []
    for ref in request.input_refs:
        export = resolve_export_ref(context, ref)
        if export is None or (export_types and export.export_type not in export_types):
            continue
        value = export.value
        raw = value if isinstance(value, (list, tuple)) else (
            value.get("player_ids") if isinstance(value, dict) else [])
        for item in raw or ():
            candidate = item.get("player_id") if isinstance(item, dict) else item
            text = str(candidate)
            if text.isdigit():
                ids.append(int(text))
    return list(dict.fromkeys(ids))
