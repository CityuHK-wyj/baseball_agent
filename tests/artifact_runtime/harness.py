"""Harness: build an ArtifactRuntime with fakes for tests."""

from __future__ import annotations

from app.artifact_runtime.engine import ArtifactRuntime
from app.artifact_runtime.planner import ScriptedPlanner
from app.artifact_runtime.response import DeterministicResponseComposer
from app.artifact_runtime.tool_base import ToolRegistry
from app.artifact_runtime.tools_analytics import BattingTool, ComputeTool, LocalAnalyticsTool
from app.artifact_runtime.tools_evidence import (EntityResolutionTool, EvidenceEntityTool,
                                                 KnowledgeTool, RosterTool, WebResearchTool)
from app.tools.batting import BattingStatsTool

from tests.artifact_runtime.fakes import (FakeBattingClient, RecordingExecutor, ScriptedInterpreter, dfa_item,
                    entity_lookup, fake_roster, knowledge_base)


def build_test_runtime(*, needs=(), web=None, knowledge_items=None, interpreter=None,
                       executor=None, parquet_executor=None, batting_client=None,
                       roster_provider=fake_roster, player_names=None,
                       pending_clarification_question="", store=None):
    if interpreter is None:
        from app.models.artifact_runtime import SemanticBrief
        brief = SemanticBrief(brief_id="b", clarification_question=pending_clarification_question,
                              source="scripted")
        interpreter = ScriptedInterpreter(brief)
    items = knowledge_items if knowledge_items is not None else (dfa_item(),)
    kb = knowledge_base(items) if items is not None else None
    batting = BattingStatsTool(client=batting_client or FakeBattingClient()) \
        if batting_client is not None else None
    postgres = executor if executor is not None else RecordingExecutor()
    runtime = ArtifactRuntime(
        interpreter=interpreter, planner=ScriptedPlanner(tuple(needs)),
        registry=ToolRegistry((KnowledgeTool(), WebResearchTool(), EntityResolutionTool(),
                               EvidenceEntityTool(), RosterTool(), BattingTool(),
                               LocalAnalyticsTool(), ComputeTool())),
        composer=DeterministicResponseComposer(), knowledge=kb,
        entity_lookup=entity_lookup(), web=web, batting=batting, pitching=None,
        roster_provider=roster_provider, postgres_executor=postgres,
        parquet_executor=parquet_executor, player_names=player_names or {},
        field_mapping=None, store=store,
        today=lambda: __import__("datetime").date(2025, 9, 30))
    return runtime, postgres
