"""Live end-to-end evidence for the v0.3 artifact runtime.

Runs the real Parquet archive, the real read-only PostgreSQL store, the live MLB
StatsAPI roster and live web research. Prints one JSON line per check.

Run: PYTHONPATH=. python3 docs/reviews/v03-artifact-runtime/live_e2e.py
"""

from __future__ import annotations

import json
from datetime import date

from app.artifact_runtime.analytical_ir import (Aggregate, AggregateRef, AnalyticalQuery,
                                                BinaryOperand, Compare, Selection)
from app.artifact_runtime.engine import ArtifactRuntime
from app.artifact_runtime.ir_compiler import compile_analytical_query
from app.artifact_runtime.planner import ScriptedPlanner, RuleBasedSemanticInterpreter
from app.artifact_runtime.response import DeterministicResponseComposer
from app.artifact_runtime.roster import MLBTeamRosterProvider
from app.artifact_runtime.schema_catalog import catalog_from_registry
from app.artifact_runtime.tool_base import ToolRegistry
from app.artifact_runtime.tools_analytics import BattingTool, ComputeTool, LocalAnalyticsTool
from app.artifact_runtime.tools_evidence import (EntityResolutionTool, EvidenceEntityTool,
                                                 KnowledgeTool, RosterTool, WebResearchTool)
from app.config import settings
from app.models.artifact_runtime import Need
from app.models.contracts import TimeRange
from app.tools.execution import DuckDBReadOnlyExecutor, PostgresReadOnlyExecutor
from app.tools.web_research import WebResearchTool as LiveWeb


def check_parquet_derived_metric():
    catalog = catalog_from_registry()
    query = AnalyticalQuery(
        query_id="live-parquet", source_kind="PARQUET", table="mlb_statcast_archive",
        selections=(
            Selection(alias="batter", kind="GROUP_KEY", field="batter"),
            Selection(alias="hard_hit_rate", kind="DERIVED", expression=BinaryOperand(
                op="PCT", left=AggregateRef(alias="hh"), right=AggregateRef(alias="m"))),
            Selection(alias="hh", kind="AGGREGATE", aggregate=Aggregate(
                op="COUNT_IF", alias="hh", condition=Compare(
                    field="launch_speed", operator="GTE", value=95.0))),
            Selection(alias="m", kind="AGGREGATE", aggregate=Aggregate(
                op="COUNT_NON_NULL", field="launch_speed", alias="m")),
        ),
        filters=(Compare(field="game_type", operator="EQ", value="R"),
                 Compare(field="description", operator="EQ", value="hit_into_play")),
        order_by="hard_hit_rate", direction="DESC", limit=3, min_rows=300,
        window=TimeRange(start=date(2023, 4, 1), end=date(2023, 10, 1)))
    glob = str(settings.parquet_archive_path / "mlb_statcast_*.parquet")
    compiled = compile_analytical_query(query, catalog,
                                        relation_sql=f"read_parquet('{glob}')")
    if not compiled.ok:
        return {"check": "parquet_derived_metric", "ok": False, "code": compiled.code}
    executor = DuckDBReadOnlyExecutor(settings.parquet_archive_path)
    result, rows = executor.execute_with_rows(compiled.sql)
    return {"check": "parquet_derived_metric", "ok": result.status == "OK",
            "sql": compiled.sql, "rows": [list(row) for row in rows][:3]}


def check_postgres_aggregate():
    catalog = catalog_from_registry()
    query = AnalyticalQuery(
        query_id="live-pg", source_kind="POSTGRES", table="statcast_pitches",
        selections=(
            Selection(alias="batter", kind="GROUP_KEY", field="batter_id"),
            Selection(alias="avg_ev", kind="AGGREGATE", aggregate=Aggregate(
                op="AVG", field="launch_speed", alias="avg_ev")),
            Selection(alias="n", kind="AGGREGATE", aggregate=Aggregate(
                op="COUNT_NON_NULL", field="launch_speed", alias="n")),
        ),
        filters=(Compare(field="game_type", operator="EQ", value="R"),),
        order_by="avg_ev", direction="DESC", limit=3, min_rows=200,
        window=TimeRange(start=date(2025, 4, 1), end=date(2025, 9, 1)))
    compiled = compile_analytical_query(query, catalog)
    if not compiled.ok:
        return {"check": "postgres_aggregate", "ok": False, "code": compiled.code}
    executor = PostgresReadOnlyExecutor(settings, allowed_tables=("statcast_pitches",))
    result, rows = executor.execute_with_rows(compiled.sql)
    return {"check": "postgres_aggregate", "ok": result.status == "OK",
            "rows": [list(row) for row in rows][:3]}


def _local_ir():
    return {"query_id": "q", "source_kind": "POSTGRES", "table": "statcast_pitches",
            "selections": [
                {"alias": "batter", "kind": "GROUP_KEY", "field": "batter_id"},
                {"alias": "avg_ev", "kind": "AGGREGATE", "aggregate": {
                    "op": "AVG", "field": "launch_speed", "alias": "avg_ev"}},
                {"alias": "n", "kind": "AGGREGATE", "aggregate": {
                    "op": "COUNT_NON_NULL", "field": "launch_speed", "alias": "n"}}],
            "entity_set": {"field": "batter_id", "export_ref": ""},
            "order_by": "avg_ev", "limit": 3, "min_rows": 50}


def check_roster_to_sql_composition():
    needs = (
        Need(need_id="roster", objective="New York Yankees roster",
             proposed_capability="roster", preferred_capabilities=("PLAYER_ID_SET",),
             parameters={"team": "New York Yankees"}),
        Need(need_id="sql", objective="team avg exit velocity",
             proposed_capability="local_analytics",
             preferred_capabilities=("STATISTICAL_RESULT",),
             parameters={"analytical_query": _local_ir()}, depends_on=("roster",)),
    )
    executor = PostgresReadOnlyExecutor(settings, allowed_tables=("statcast_pitches",))
    runtime = ArtifactRuntime(
        interpreter=RuleBasedSemanticInterpreter(), planner=ScriptedPlanner(needs),
        registry=ToolRegistry((KnowledgeTool(), WebResearchTool(), EntityResolutionTool(),
                               EvidenceEntityTool(), RosterTool(), BattingTool(),
                               LocalAnalyticsTool(), ComputeTool())),
        composer=DeterministicResponseComposer(),
        roster_provider=MLBTeamRosterProvider(), postgres_executor=executor)
    result = runtime.send_message(runtime.start_conversation(),
                                  "2025 Yankees average exit velocity")
    roster = [a for a in result.artifacts if a.kind == "team_roster"]
    analytics = [a for a in result.artifacts if a.kind == "analytics"]
    return {"check": "roster_to_sql_composition", "ok": result.status == "COMPLETE",
            "status": result.status,
            "roster_size": len(roster[0].structured_data["players"]) if roster else 0,
            "lineage": analytics[0].lineage if analytics else [],
            "sql_uses_canonical_ids": bool(analytics)
            and "batter_id IN (" in analytics[0].structured_data.get("sql", ""),
            "top_rows": analytics[0].structured_data.get("rows", [])[:3] if analytics else []}


def check_live_web():
    runtime = ArtifactRuntime(
        interpreter=RuleBasedSemanticInterpreter(),
        planner=ScriptedPlanner((Need(need_id="web", objective="MLB news today",
                                      proposed_capability="web_research",
                                      preferred_capabilities=("WEB_EVIDENCE",)),)),
        registry=ToolRegistry((KnowledgeTool(), WebResearchTool())),
        composer=DeterministicResponseComposer(), web=LiveWeb())
    result = runtime.send_message(runtime.start_conversation(), "MLB news today")
    web = [a for a in result.artifacts if a.kind == "web_evidence"]
    findings = web[0].structured_data["findings"] if web else []
    return {"check": "live_web", "ok": bool(findings), "status": result.status,
            "findings": len(findings),
            "grounded": sum(1 for item in findings if item["grounded"])}


CHECKS = (check_parquet_derived_metric, check_postgres_aggregate,
          check_roster_to_sql_composition, check_live_web)

if __name__ == "__main__":
    for check in CHECKS:
        try:
            print(json.dumps(check(), ensure_ascii=False), flush=True)
        except Exception as error:  # noqa: BLE001 - a live failure must not hide the rest
            print(json.dumps({"check": check.__name__, "ok": False,
                              "error": f"{type(error).__name__}: {error}"}), flush=True)
