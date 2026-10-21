#!/usr/bin/env python3
"""Replay frozen M2.12 contract failures as legacy gates, never development seeds."""
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.capabilities.analytics.conformance import attest, bind_obligations, compare, requested_contract
from app.capabilities.analytics.compiler import SQLCompiler
from app.capabilities.analytics.ir import AnalyticalQuery, validate_query
from app.capabilities.analytics.tool import LocalAnalyticsTool
from app.contracts.models import ToolRequest
from tools.m212_cases import case, mutations
from tools.evaluate_m212r import OUT, FixedIR
from tests.test_analytical_conformance_m212 import Source


def run(request,raw):
    request=ToolRequest.model_validate(request);tool=LocalAnalyticsTool(None,None)
    intent,issues=bind_obligations(tool._intent(request),request.inputs)
    wanted=requested_contract(intent,{**request.inputs,"obligation_issues":issues},tool._requested_measures(request,intent))
    q=validate_query(AnalyticalQuery.model_validate(raw));a=attest(q,SQLCompiler().compile(q,"POSTGRES"))
    return compare(wanted,a,check_availability=False)


def main():
    records=[]
    for seed in range(21294000,21294040):
        c=case(seed);request=ToolRequest(need_id="legacy",capability="local_analytics",objective="Report batting average",inputs=c["inputs"])
        comparison=run(request.model_dump(),c["raw"])
        records.append(dict(seed=seed,name="positive_control",status=comparison.status,ok=comparison.status == "MATCH"))
        for name,raw in mutations(c).items():
            comparison=run(request.model_dump(),raw)
            records.append(dict(seed=seed,name=name,status=comparison.status,ok=comparison.status == "MISMATCH"))
    edges=json.loads(Path("docs/audit/m212/holdout-scope-edges.json").read_text())["checks"]
    for edge in edges:
        comparison=run(edge["request"],edge["drift_ir"])
        records.append(dict(seed=edge["seed"],name=edge["name"],status=comparison.status,ok=comparison.status == "MISMATCH",differences=comparison.model_dump(mode="json"),provenance="unaltered saved frozen request and drift IR"))
    encoding=json.loads(Path("docs/audit/m212/holdout-filter-encoding.json").read_text())
    comparison=run(encoding["request"],encoding["query"])
    records.append(dict(seed=encoding["seed"],name="equivalent_filter",status=comparison.status,ok=comparison.status == "MATCH",provenance="unaltered saved request and validated IR"))
    OUT.mkdir(exist_ok=True,parents=True)
    (OUT/"legacy-m212.json").write_text(json.dumps(dict(checks=records,passed=sum(x["ok"] for x in records),total=len(records)),indent=2)+"\n")
    print(len(records),sum(x["ok"] for x in records),[x for x in records if not x["ok"]])


if __name__ == "__main__": main()
