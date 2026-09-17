"""v0.3 artifact-runtime reproductions for the v0.2 audit failure classes.

These checks target problem *classes*, not the known dogfooding queries. They use
injected fakes so they need no network, LLM or database. They assert the fixed behavior
of the new runtime; the preserved v0.2 audit script remains runnable as historical
evidence and as a regression gate on the deprecated path.

Run: PYTHONPATH=. python3 docs/reviews/v03-artifact-runtime/reproduce.py
"""

from __future__ import annotations

from datetime import date

from app.artifact_runtime.engine import ArtifactRuntime
from app.artifact_runtime.planner import ScriptedPlanner
from app.artifact_runtime.response import DeterministicResponseComposer
from app.artifact_runtime.scope import compare_scope
from app.artifact_runtime.sufficiency import CoverageJudge
from app.artifact_runtime.tool_base import ToolRegistry
from app.artifact_runtime.tools_analytics import LocalAnalyticsTool
from app.artifact_runtime.tools_evidence import KnowledgeTool, RosterTool, WebResearchTool
from app.artifact_runtime.roster import RosterUnavailable
from app.models.artifact_runtime import (Goal, Need, RuntimeArtifact, Scope, SemanticBrief,
                                         ToolCapabilityContract)
from app.models.contracts import TimeRange
from app.tools.results import ToolResult


def _artifact(artifact_id, *, actual_scope=None, status="OK", confidence=0.8):
    return RuntimeArtifact(artifact_id=artifact_id, kind="t", actual_scope=actual_scope,
                           status=status, confidence=confidence, text_content="x")


def check_unrelated_evidence_does_not_complete():
    goal = Goal(goal_id="g", statement="precise scoped question",
                scope=Scope(entities=("Team A",), seasons=(2025,), metric="OPS"))
    need = Need(need_id="n", objective="o", required_scope=goal.scope,
                linked_artifacts=("unrelated",))
    unrelated = _artifact("unrelated", actual_scope=Scope(entities=("Team B",), seasons=(2024,),
                                                          metric="ERA"))
    assessment = CoverageJudge().assess_need(goal, need, (unrelated,))
    return {"verdict": assessment.verdict, "core_goal_supported": assessment.core_goal_supported,
            "gaps": list(assessment.gaps)}


def check_temporal_scope_is_preserved():
    requested = Scope(time_range=TimeRange(start=date(2025, 8, 1), end=date(2025, 8, 31)))
    season = Scope(time_range=TimeRange(start=date(2025, 1, 1), end=date(2025, 12, 31)))
    exact = Scope(time_range=TimeRange(start=date(2025, 8, 1), end=date(2025, 8, 31)))
    broader = compare_scope(requested, season)
    matched = compare_scope(requested, exact)
    return {"season_evidence_blocking": broader.blocking,
            "season_evidence_gaps": list(broader.gaps),
            "exact_window_matches": not matched.blocking}


class _Executor:
    def __init__(self, rows):
        self.rows = rows
        self.statements: list[str] = []

    def execute_with_rows(self, sql):
        self.statements.append(sql)
        return (ToolResult.ok(len(self.rows)), self.rows) if self.rows \
            else (ToolResult.no_data(), ())


class _Web:
    def __init__(self, fetched):
        self._fetched = fetched

    def research(self, query, *, fetch_pages=None):
        from app.models.agent_runtime import EvidenceItem
        return (EvidenceItem(kind="WEB", summary="hit", source="example.test",
                             reference="https://example.test/a", text="page or snippet",
                             data={"title": "hit", "snippet": "snippet", "url":
                                   "https://example.test/a", "fetched": self._fetched},
                             accepted=self._fetched),)


def check_team_population_is_authoritative():
    def provider(team):
        if team == "Team A":
            return ({"player_id": "1", "name": "A", "team_name": "Team A"},
                    {"player_id": "2", "name": "B", "team_name": "Team A"})
        raise RosterUnavailable(f"ambiguous or unknown team {team!r}")

    ir = {"query_id": "q", "source_kind": "POSTGRES", "table": "statcast_pitches",
          "selections": [
              {"alias": "batter", "kind": "GROUP_KEY", "field": "batter_id"},
              {"alias": "n", "kind": "AGGREGATE", "aggregate": {
                  "op": "COUNT_NON_NULL", "field": "launch_speed", "alias": "n"}}],
          "entity_set": {"field": "batter_id", "export_ref": ""},
          "order_by": "n", "limit": 5}
    needs = (
        Need(need_id="r", objective="roster", proposed_capability="roster",
             preferred_capabilities=("PLAYER_ID_SET",), parameters={"team": "Team A"}),
        Need(need_id="s", objective="analysis", proposed_capability="local_analytics",
             preferred_capabilities=("STATISTICAL_RESULT",),
             parameters={"analytical_query": ir}, depends_on=("r",)),
    )
    executor = _Executor(((1, 10),))
    runtime = ArtifactRuntime(
        interpreter=_Interp(), planner=ScriptedPlanner(needs),
        registry=ToolRegistry((KnowledgeTool(), RosterTool(), LocalAnalyticsTool())),
        composer=DeterministicResponseComposer(), roster_provider=provider,
        postgres_executor=executor)
    runtime.send_message(runtime.start_conversation(), "Team A analysis")
    return {"sql": executor.statements[0] if executor.statements else "",
            "uses_canonical_ids": bool(executor.statements)
            and "batter_id IN (1, 2)" in executor.statements[0]}


def check_invalid_ir_is_not_silently_dropped():
    ir = {"query_id": "q", "source_kind": "POSTGRES", "table": "statcast_pitches",
          "selections": [
              {"alias": "batter", "kind": "GROUP_KEY", "field": "batter_id"},
              {"alias": "n", "kind": "AGGREGATE", "aggregate": {
                  "op": "AVG", "field": "not_a_field", "alias": "n"}}]}
    need = Need(need_id="s", objective="analysis", proposed_capability="local_analytics",
                preferred_capabilities=("STATISTICAL_RESULT",),
                parameters={"analytical_query": ir})
    executor = _Executor(())
    runtime = ArtifactRuntime(
        interpreter=_Interp(), planner=ScriptedPlanner((need,)),
        registry=ToolRegistry((KnowledgeTool(), LocalAnalyticsTool())),
        composer=DeterministicResponseComposer(), postgres_executor=executor)
    result = runtime.send_message(runtime.start_conversation(), "q")
    return {"status": result.status, "executed": bool(executor.statements),
            "recovery_steps": [step for step in result.trace.steps if "FIELD" in step]}


def check_web_snippet_is_not_grounded_evidence():
    def build(fetched):
        need = Need(need_id="w", objective="q", proposed_capability="web_research",
                    preferred_capabilities=("WEB_EVIDENCE",))
        runtime = ArtifactRuntime(
            interpreter=_Interp(), planner=ScriptedPlanner((need,)),
            registry=ToolRegistry((WebResearchTool(),)),
            composer=DeterministicResponseComposer(), web=_Web(fetched))
        result = runtime.send_message(runtime.start_conversation(), "q")
        artifact = result.artifacts[0]
        return {"status": result.status, "artifact_status": artifact.status,
                "grounded": artifact.structured_data["findings"][0]["grounded"]}
    return {"snippet_only": build(False), "fetched_page": build(True)}


class _Interp:
    def brief(self, *, message, history, resolved_entities, unknowns, today):
        return SemanticBrief(brief_id="b", goal_statement=message)


CHECKS = {
    "P1-01 unrelated evidence cannot complete the goal":
        check_unrelated_evidence_does_not_complete,
    "P1-02 requested temporal scope is preserved": check_temporal_scope_is_preserved,
    "P1-03 team population is authoritative": check_team_population_is_authoritative,
    "P1-04/18 unsupported input returns a recovery signal":
        check_invalid_ir_is_not_silently_dropped,
    "P2-02 web snippets are not grounded support":
        check_web_snippet_is_not_grounded_evidence,
}


if __name__ == "__main__":
    for title, check in CHECKS.items():
        print(f"{title}: {check()}")
