"""Composition root for the LLM-first conversational runtime."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import uuid4

from app.agent.agent import BaseballAgent
from app.agent.cognition import DeterministicCognition, LLMCognition
from app.agent.local_analytics import LocalAnalyticsRunner
from app.config import settings
from app.knowledge.candidates import CandidateKnowledgeStore
from app.knowledge.entities import entity_dictionary_from_knowledge
from app.knowledge.loader import seed_store
from app.knowledge.service import KnowledgeBase
from app.knowledge.store import SqliteKnowledgeStore
from app.semantic.entity_resolver import EntityResolver
from app.semantic.field_mapping import FieldMappingRegistry
from app.tools.batting import BattingStatsTool
from app.tools.entity_lookup import EntityLookup, MLBPeopleSearch
from app.tools.pitching import PitchingStatsTool
from app.tools.web_research import WebResearchTool


def build_agent(*, runtime_dir: Path | None = None, use_llm: bool = True,
                today=date.today, knowledge: KnowledgeBase | None = None,
                web: WebResearchTool | None = None, batting: BattingStatsTool | None = None,
                pitching: PitchingStatsTool | None = None,
                local: LocalAnalyticsRunner | None = None):
    """Compose the conversational agent. LLM cognition is used when a credential exists."""
    ids = lambda prefix: f"{prefix}-{uuid4().hex}"  # noqa: E731
    root = Path(runtime_dir) if runtime_dir is not None else settings.operational_store_path.parent
    owned: list = []

    if knowledge is None:
        path = root / "knowledge.db" if runtime_dir is not None else settings.knowledge_store_path
        path.parent.mkdir(parents=True, exist_ok=True)
        store = SqliteKnowledgeStore(path)
        owned.append(store)
        if store.item_count() == 0:
            seed_store(store, settings.knowledge_source_path, settings.knowledge_seed_path)
        knowledge = KnowledgeBase(store)

    dictionary = entity_dictionary_from_knowledge(knowledge.store)
    resolver = EntityResolver(dictionary, id_factory=ids)
    entity_lookup = EntityLookup(dictionary, resolver, MLBPeopleSearch())

    web = web or WebResearchTool()
    batting = batting or BattingStatsTool()
    pitching = pitching or PitchingStatsTool()
    if local is None:
        player_names = {
            item.entity_key.partition(":")[2]: item.display_name
            for item in dictionary.entities() if item.entity_type == "PLAYER"}
        local = LocalAnalyticsRunner(FieldMappingRegistry(), player_names=player_names)

    if use_llm and settings.deepseek_api_key:
        from app.llm.openai_provider import OpenAICompatibleProvider
        cognition = LLMCognition(
            OpenAICompatibleProvider(settings), settings.llm_planner_model,
            timeout=max(settings.llm_request_timeout_seconds, 60.0),
            answer_model=settings.llm_response_model, fallback=DeterministicCognition())
    else:
        cognition = DeterministicCognition()

    candidate_path = (root / "candidate_knowledge.db" if runtime_dir is not None
                      else settings.operational_store_path.parent / "candidate_knowledge.db")
    candidates = CandidateKnowledgeStore(candidate_path)
    owned.append(candidates)

    agent = BaseballAgent(cognition, knowledge, entity_lookup, web=web, batting=batting,
                          pitching=pitching, local=local, candidates=candidates,
                          today=today, id_factory=ids)
    agent._owned_resources = tuple(owned)  # noqa: SLF001 - closed by close()
    return agent
