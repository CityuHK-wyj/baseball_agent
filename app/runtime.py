"""Default composition root. External data adapters stay explicitly injectable."""

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
from app.semantic.schema_registry import SchemaRegistry
from app.tools.knowledge import KnowledgeTool
from app.tools.synthetic import SyntheticDataTool


def build_pipeline(*, runtime_dir: Path | None = None, knowledge=None, recorder=None,
                   persist: bool = True, demo: bool = False, empty: bool = False,
                   tool_factory=None, capabilities=(), metric_registry=None, schema_registry=None):
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
    schema_registry = schema_registry or SchemaRegistry()
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
    all_capabilities = (knowledge_capability, *capabilities)
    if demo:
        all_capabilities += (ToolCapability(tool="synthetic", source_kind="SYNTHETIC",
                             supported_artifact_types=("TABLE", "EVIDENCE", "FEATURE")),)
    def tools(requirements):
        result = {"shared-knowledge": KnowledgeTool(knowledge, requirements)}
        if demo:
            result["synthetic"] = SyntheticDataTool(requirements, row_count=0 if empty else 1200)
        if tool_factory is not None:
            injected = tool_factory(requirements)
            result.update(injected if isinstance(injected, dict) else {injected.name: injected})
        return result
    registry = ArtifactRegistry()
    result = AnalysisPipeline(
        SemanticNormalizer(RuleBasedObjectiveExtractor(ids), EntityResolver(dictionary, ids), dictionary, ids),
        RuleBasedRequirementDecomposer(ids), RuleBasedPlanner(id_factory=ids),
        Router(all_capabilities, id_factory=ids), AssessmentService(registry, RuleBasedJudge(), ids), registry,
        tool_factory=tools, context_service=context, recorder=recorder, id_factory=ids,
        source_mapping_resolver=None if demo else SourceMappingResolver(metric_registry,
            {item.source_kind: item.tool for item in capabilities}))
    result._owned_resources = tuple(owned)
    return result
