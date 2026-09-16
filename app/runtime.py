"""Default composition root. External data adapters stay explicitly injectable."""

from datetime import date
from pathlib import Path
from uuid import uuid4

from app.agent.planner import RuleBasedPlanner
from app.agent.registry import ArtifactRegistry
from app.agent.routing import Router, ToolCapability
from app.agent.source_mapping import SourceMappingResolver
from app.assessment.judge import RuleBasedJudge
from app.assessment.service import AssessmentService
from app.config import settings
from app.context.knowledge_source import KnowledgeContextSource
from app.context.registry_source import SchemaRegistrySource
from app.context.service import ContextService
from app.knowledge.entities import entity_dictionary_from_knowledge, metric_registry_from_knowledge
from app.knowledge.loader import seed_store
from app.knowledge.service import KnowledgeBase
from app.knowledge.store import SqliteKnowledgeStore
from app.persistence.artifacts import LocalFilesystemArtifactStorage
from app.persistence.recorder import RunRecorder
from app.persistence.store import SqliteOperationalStore
from app.pipeline import AnalysisPipeline
from app.semantic.entity_resolver import EntityResolver
from app.semantic.normalizer import SemanticNormalizer
from app.semantic.objective_extractor import RuleBasedObjectiveExtractor
from app.semantic.requirement_decomposer import RuleBasedRequirementDecomposer
from app.semantic.field_mapping import (BATTER_RELATIVE_UPPER_EDGE, ZONE_ABOVE_UPPER_EDGE,
                                        ZONE_UPPER_THIRD, FieldMappingRegistry)
from app.semantic.schema_registry import SchemaRegistry, statcast_schema_registry
from app.tools.execution import DuckDBReadOnlyExecutor, PostgresReadOnlyExecutor
from app.tools.knowledge import KnowledgeTool
from app.tools.statcast import ParquetStatcastTool, PostgresStatcastTool
from app.tools.synthetic import SyntheticDataTool
from app.tools.web_evidence import WebEvidenceTool
from app.semantic.evidence import RuleBasedEvidenceExtractor


def build_pipeline(*, runtime_dir: Path | None = None, knowledge=None, recorder=None,
                   persist: bool = True, demo: bool = False, empty: bool = False,
                   tool_factory=None, capabilities=(), metric_registry=None, schema_registry=None,
                   web_fetcher=None, evidence_extractor=None, web_cost="FREE", today=None):
    ids = lambda prefix: f"{prefix}-{uuid4().hex}"
    root = Path(runtime_dir) if runtime_dir is not None else settings.operational_store_path.parent
    owned = []
    if knowledge is None:
        path = root / "knowledge.db" if runtime_dir is not None else settings.knowledge_store_path
        path.parent.mkdir(parents=True, exist_ok=True)
        store = SqliteKnowledgeStore(path)
        owned.append(store)
        if store.item_count() == 0:
            seed_store(store, settings.knowledge_source_path, settings.knowledge_seed_path)
        knowledge = KnowledgeBase(store)
    dictionary = entity_dictionary_from_knowledge(knowledge.store)
    metric_registry = metric_registry or metric_registry_from_knowledge(knowledge.store)
    schema_registry = schema_registry or (SchemaRegistry() if demo else statcast_schema_registry())
    context = ContextService((KnowledgeContextSource(knowledge), SchemaRegistrySource(schema_registry)))
    if recorder is None and persist:
        path = root / "operational.db" if runtime_dir is not None else settings.operational_store_path
        path.parent.mkdir(parents=True, exist_ok=True)
        store = SqliteOperationalStore(path)
        owned.append(store)
        recorder = RunRecorder(store, LocalFilesystemArtifactStorage(
            root / "artifacts" if runtime_dir is not None else settings.artifact_storage_path), ids)
    knowledge_capability = ToolCapability(tool="shared-knowledge", source_kind="WEB",
                                          supported_artifact_types=("EVIDENCE",),
                                          supported_data_keys=("knowledge_statement",))
    analytics_capabilities: list[ToolCapability] = []
    field_mapping: FieldMappingRegistry | None = None
    if not demo:
        field_mapping = FieldMappingRegistry()
        analytics_capabilities = [
            ToolCapability(
                tool="statcast-parquet", source_kind="PARQUET",
                supported_artifact_types=("TABLE",),
                supported_data_keys=("exit_velocity", "batter", "pitch_velocity", "pitch_type",
                                     "count", "balls", "pitch_location", "game_date"),
                supported_constraint_keys=("count", "pitch_velocity", "pitch_type",
                                           "ranking",
                                           f"pitch_location:{ZONE_UPPER_THIRD}",
                                           f"pitch_location:{ZONE_ABOVE_UPPER_EDGE}",
                                           f"pitch_location:{BATTER_RELATIVE_UPPER_EDGE}"),
                coverage="2015-04-05..2023-11-01 Parquet archive",
                coverage_start=date(2015, 4, 5), coverage_end=date(2023, 11, 1)),
            ToolCapability(
                tool="statcast-postgres", source_kind="POSTGRES",
                supported_artifact_types=("TABLE",),
                supported_data_keys=("exit_velocity", "batter", "pitch_velocity", "pitch_type",
                                     "count", "balls", "pitch_location", "game_date"),
                supported_constraint_keys=("count", "pitch_velocity", "pitch_type",
                                           "ranking",
                                           f"pitch_location:{ZONE_UPPER_THIRD}",
                                           f"pitch_location:{ZONE_ABOVE_UPPER_EDGE}",
                                           f"pitch_location:{BATTER_RELATIVE_UPPER_EDGE}"),
                coverage="2024-03-15..2026-09-14 PostgreSQL",
                coverage_start=date(2024, 3, 15), coverage_end=date(2026, 9, 14),
                available=bool(settings.postgres_password)),
        ]
    all_capabilities = (knowledge_capability, *analytics_capabilities, *capabilities)
    if web_fetcher is not None:
        all_capabilities += (ToolCapability(tool="web-evidence", source_kind="WEB", cost=web_cost,
            supported_artifact_types=("EVIDENCE",),
            supported_data_keys=("injury_status", "salary", "news_claim")),)
    if demo:
        all_capabilities += (ToolCapability(tool="synthetic", source_kind="SYNTHETIC",
                             supported_artifact_types=("TABLE", "EVIDENCE", "FEATURE")),)
    def tools(requirements):
        result = {"shared-knowledge": KnowledgeTool(knowledge, requirements)}
        if web_fetcher is not None:
            result["web-evidence"] = WebEvidenceTool(web_fetcher,
                evidence_extractor or RuleBasedEvidenceExtractor(id_factory=ids), requirements)
        if demo:
            result["synthetic"] = SyntheticDataTool(requirements, row_count=0 if empty else 1200)
        elif field_mapping is not None:
            player_names = {
                item.entity_key.partition(":")[2]: item.display_name
                for item in dictionary.entities() if item.entity_type == "PLAYER"
            }
            result["statcast-parquet"] = ParquetStatcastTool(
                requirements, field_mapping,
                DuckDBReadOnlyExecutor(settings.parquet_archive_path),
                archive_glob=str(settings.parquet_archive_path / "mlb_statcast_*.parquet"),
                player_names=player_names)
            result["statcast-postgres"] = PostgresStatcastTool(
                requirements, field_mapping,
                PostgresReadOnlyExecutor(settings, allowed_tables=("statcast_pitches",
                                                                   "player_dictionary")),
                player_names=player_names)
        if tool_factory is not None:
            injected = tool_factory(requirements)
            result.update(injected if isinstance(injected, dict) else {injected.name: injected})
        return result
    registry = ArtifactRegistry()
    result = AnalysisPipeline(
        SemanticNormalizer(RuleBasedObjectiveExtractor(ids), EntityResolver(dictionary, ids), dictionary, ids,
                           **({"today": today} if today is not None else {})),
        RuleBasedRequirementDecomposer(ids), RuleBasedPlanner(id_factory=ids),
        Router(all_capabilities, id_factory=ids), AssessmentService(registry, RuleBasedJudge(), ids), registry,
        tool_factory=tools, context_service=context, recorder=recorder, id_factory=ids,
        source_mapping_resolver=None if demo else SourceMappingResolver(metric_registry,
            {item.source_kind: item.tool for item in capabilities}))
    result._owned_resources = tuple(owned)
    return result
