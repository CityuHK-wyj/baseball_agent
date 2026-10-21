#!/usr/bin/env python3
"""Frozen-code diagnostics for final failures; no repair and no network rerun."""
import copy
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.capabilities.analytics.tool import LocalAnalyticsTool
from app.capabilities.analytics.conformance import constraint_obligations
from app.config import get_config
from app.contracts.models import PlannerNeed, ToolRequest
from app.runtime.engine import Engine
from tests.test_integration_live import _hand_built_goal, _minimal_semantic, _RANK_IR
from tools.evaluate_m212r import OUT, FixedIR, freeze_check
from tools.m212_evaluation_support import encode


def main():
    freeze_check();records=[];engine=Engine.__new__(Engine);engine.config=get_config()
    goal=_hand_built_goal();ctx=SimpleNamespace(goal=goal)
    for comparison in (False,True):
        need=PlannerNeed(id="n1",objective="rank 2025 home runs",capability="local_analytics",constraints={"season":2025,"game_population":"REGULAR_SEASON","measure":"home_runs"},requires=["r1"])
        needs=[need]
        if comparison: needs.append(PlannerNeed(id="n2",objective="compute",capability="python_compute",depends_on=["n1"]))
        request=engine._build_request(need,ctx,_minimal_semantic(),[],SimpleNamespace(bindings=[]),needs)
        result=LocalAnalyticsTool(None,FixedIR(_RANK_IR)).execute(request)
        records.append(dict(test="test_f5_comparison_input_enumerates_complete_population" if comparison else "test_f2_rank_to_web_cross_tool_execution",request=encode(request),result=encode(result),diagnosis="missing independently bound role/grouping; candidate is blocked before database execution"))
    q=dict(relation="pitch",population="REGULAR_SEASON",season=2025,group_by=["batter_id","game_pk"],aggregates=[dict(name="pitches",function="COUNT",field="*")],sort=[dict(name="pitches",direction="DESC")],limit=5,intent_summary="batter-game pitch counts")
    request=ToolRequest(need_id="n1",capability="local_analytics",objective="batter game pitch counts",inputs={"intent":{"objective":"batter game pitch counts","season":2025,"population":"REGULAR_SEASON"},"for_comparison":True})
    result=LocalAnalyticsTool(None,FixedIR(q)).execute(request)
    records.append(dict(test="test_f5_row_budget_measures_population_count",request=encode(request),result=encode(result),diagnosis="unbound group grain is compared to scalar default before the governed complete-scan stage"))
    conn=sqlite3.connect("file:/tmp/m212r-final-integration/conversations.db?mode=ro",uri=True)
    conversations={sid:json.loads(payload) for sid,payload in conn.execute("SELECT session_id,payload FROM conversations")};conn.close()
    source=Path("/tmp/m212r-final-integration/diagnostics")
    archive=OUT/"final-runtime-diagnostics";archive.mkdir(exist_ok=True)
    for path in source.glob("*.json"): shutil.copyfile(path,archive/path.name)
    for sid in ("it_split","it_rank","it_xsrc","it_empty"):
        state=conversations[sid];g=state["goals"][-1]
        constraints=[r["value"] for r in g["requirements"] if r["kind"] == "CONSTRAINT"]
        diagnostic=next(archive.glob(sid+"__*.json"))
        judge=next(r["data"] for r in json.loads(diagnostic.read_text()) if r["stage"] == "judge_decision")
        records.append(dict(session_id=sid,origin="actual frozen integration conversation store, read-only",goal=g,answer=state["messages"][-1]["text"],constraints=constraints,
                            finite_adaptations=[constraint_obligations(c,"BATTER", "rank batters by home runs") for c in constraints],
                            actual_judge_decision=judge,runtime_diagnostic=str(diagnostic)))
    (OUT/"final-integration-diagnostics.json").write_text(json.dumps(dict(frozen_sha=freeze_check()["implementation_sha"],records=records),indent=2,default=str)+"\n")
    print([(r.get("test") or r.get("session_id"),r.get("result",{}).get("outcome",{}).get("code")) for r in records])


if __name__ == "__main__":main()
