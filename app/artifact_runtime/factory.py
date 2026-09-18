"""Composition root for the v0.3 artifact runtime."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import uuid4

from app.config import settings
from app.knowledge.candidates import CandidateKnowledge, CandidateKnowledgeStore
from app.knowledge.entities import entity_dictionary_from_knowledge
from app.knowledge.loader import seed_store
from app.knowledge.service import KnowledgeBase
from app.knowledge.store import SqliteKnowledgeStore
from app.artifact_runtime.engine import ArtifactRuntime
from app.artifact_runtime.planner import (DeterministicPlanner, LLMPlanner, LLMSemanticInterpreter,
                                 RuleBasedSemanticInterpreter)
from app.artifact_runtime.response import DeterministicResponseComposer, LLMResponseComposer
from app.artifact_runtime.roster import MLBTeamRosterProvider
from app.artifact_runtime.sufficiency import CoverageJudge
from app.artifact_runtime.tool_base import ToolRegistry
from app.artifact_runtime.tools_analytics import BattingTool, ComputeTool, LocalAnalyticsTool
from app.artifact_runtime.tools_evidence import (EntityResolutionTool, EvidenceEntityTool,
                                                 KnowledgeTool, RosterTool,
                                                 WebResearchTool)
from app.semantic.entity_resolver import EntityResolver
from app.semantic.field_mapping import FieldMappingRegistry
from app.tools.batting import BattingStatsTool
from app.tools.entity_lookup import EntityLookup, MLBPeopleSearch
from app.tools.execution import DuckDBReadOnlyExecutor, PostgresReadOnlyExecutor
from app.tools.pitching import PitchingStatsTool
from app.tools.web_research import WebResearchTool as LiveWebResearchTool


def build_runtime(*, runtime_dir: Path | None = None, use_llm: bool = True,
                  today=date.today, knowledge: KnowledgeBase | None = None,
                  web=None, batting=None, pitching=None, roster_provider=None,
                  store=None, judge=None, interpreter=None, planner=None, composer=None):
    """Compose the artifact runtime. LLM cognition is used when a credential exists."""
    ids = lambda prefix: f"{prefix}-{uuid4().hex}"  # noqa: E731
    root = Path(runtime_dir) if runtime_dir is not None else settings.operational_store_path.parent
    owned: list = []

    if knowledge is None:
        path = root / "knowledge.db" if runtime_dir is not None else settings.knowledge_store_path
        path.parent.mkdir(parents=True, exist_ok=True)
        knowledge_store = SqliteKnowledgeStore(path)
        owned.append(knowledge_store)
        if knowledge_store.item_count() == 0:
            seed_store(knowledge_store, settings.knowledge_source_path,
                       settings.knowledge_seed_path)
        knowledge = KnowledgeBase(knowledge_store)

    dictionary = entity_dictionary_from_knowledge(knowledge.store)
    resolver = EntityResolver(dictionary, id_factory=ids)
    entity_lookup = EntityLookup(dictionary, resolver, MLBPeopleSearch())

    web = web if web is not None else LiveWebResearchTool()
    batting = batting if batting is not None else BattingStatsTool()
    pitching = pitching if pitching is not None else PitchingStatsTool()
    if roster_provider is None:
        roster_provider = MLBTeamRosterProvider()

    candidate_path = (root / "candidate_knowledge.db" if runtime_dir is not None
                      else settings.operational_store_path.parent / "candidate_knowledge.db")
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidates = CandidateKnowledgeStore(candidate_path)
    owned.append(candidates)

    def candidate_sink(payload: dict) -> None:
        candidates.submit(CandidateKnowledge(
            candidate_id=ids("candidate"), knowledge_type="CONTEXT_REFERENCE",
            proposed_type="CONTEXT_REFERENCE", surface=str(payload.get("surface", "")),
            language=str(payload.get("language") or "und"),
            context=str(payload.get("context", "")),
            confidence="LOW",
            discovered_from_query=str(payload.get("discovered_from_query", "")),
            evidence=tuple(payload.get("provenance", ()) or ()),
            provenance=tuple(payload.get("provenance", ()) or ()),
            reason_reusable=str(payload.get("reason", ""))))

    parquet_executor = DuckDBReadOnlyExecutor(settings.parquet_archive_path)
    postgres_executor = None
    if settings.postgres_password:
        postgres_executor = PostgresReadOnlyExecutor(
            settings, allowed_tables=("statcast_pitches", "player_dictionary",
                                      "batting_events"))

    provider = None
    if use_llm and settings.deepseek_api_key:
        from app.llm.openai_provider import OpenAICompatibleProvider
        provider = OpenAICompatibleProvider(settings)

    if interpreter is None:
        if provider is not None:
            interpreter = LLMSemanticInterpreter(
                provider, settings.llm_semantic_model,
                timeout=max(settings.llm_request_timeout_seconds, 60.0),
                max_tokens=settings.llm_semantic_max_tokens,
                reasoning_effort=settings.llm_reasoning_effort,
                deadline=settings.llm_deadline_seconds)
        else:
            interpreter = RuleBasedSemanticInterpreter()
    if planner is None:
        if provider is not None:
            planner = LLMPlanner(provider, settings.llm_planner_model,
                                 timeout=max(settings.llm_request_timeout_seconds, 60.0),
                                 max_tokens=settings.llm_planner_max_tokens,
                                 reasoning_effort=settings.llm_reasoning_effort,
                                 deadline=settings.llm_deadline_seconds)
        else:
            planner = DeterministicPlanner()
    if composer is None:
        if provider is not None:
            composer = LLMResponseComposer(provider, settings.llm_response_model,
                                           timeout=max(settings.llm_request_timeout_seconds, 60.0),
                                           max_tokens=settings.llm_response_max_tokens,
                                           reasoning_effort=settings.llm_reasoning_effort,
                                           deadline=settings.llm_deadline_seconds)
        else:
            composer = DeterministicResponseComposer()

    registry = ToolRegistry((
        KnowledgeTool(), WebResearchTool(), EntityResolutionTool(), EvidenceEntityTool(),
        RosterTool(), BattingTool(), LocalAnalyticsTool(), ComputeTool()))

    player_names = {item.entity_key.partition(":")[2]: item.display_name
                    for item in dictionary.entities() if item.entity_type == "PLAYER"}

    runtime = ArtifactRuntime(
        interpreter=interpreter, planner=planner, registry=registry, composer=composer,
        judge=judge or CoverageJudge(), knowledge=knowledge, entity_lookup=entity_lookup,
        web=web, batting=batting, pitching=pitching, roster_provider=roster_provider,
        postgres_executor=postgres_executor, parquet_executor=parquet_executor,
        parquet_glob=str(settings.parquet_archive_path / "mlb_statcast_*.parquet"),
        player_names=player_names, field_mapping=FieldMappingRegistry(),
        candidate_sink=candidate_sink, store=store, today=today, id_factory=ids)
    runtime._owned_resources = tuple(owned)  # noqa: SLF001 - closed by close()
    return runtime
