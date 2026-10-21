#!/usr/bin/env python3
"""Read-only mirrored engines, independent references and Tool/Judge drift gates."""
import argparse
import copy
import dataclasses
import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.capabilities.analytics.catalog import CATALOG, SchemaCatalog
from app.capabilities.analytics.compiler import SQLCompiler, render_literal
from app.capabilities.analytics.conformance import SourceAvailability, attest, compare, requested_contract, bind_obligations, receipt_usable
from app.capabilities.analytics.executor import PostgresExecutor, DuckDBExecutor, QueryResult
from app.capabilities.analytics.ir import AnalyticalQuery, validate_query
from app.capabilities.analytics.tool import LocalAnalyticsTool
from app.config import get_config
from app.contracts.models import Artifact, Goal, RequirementKind, ToolRequest, UserRequirement
from app.verification.judge import Judge
from tools.m212r_cases import case, mutations, reference
from tools.m212_evaluation_support import equal, encode

OUT=Path("docs/audit/m212r")


class FixedIR:
    configured=True
    def __init__(self,raw): self.raw=raw
    def complete_json(self,**kwargs): return SimpleNamespace(data=copy.deepcopy(self.raw))


def freeze_check():
    frozen=json.loads((OUT/"IMPLEMENTATION_FROZEN.json").read_text())
    current={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in Path("app").rglob("*.py")}
    assert current == frozen["production_hashes"], "production changed after freeze"
    return frozen


def make_goal(c):
    q=c["raw"]
    reqs=[UserRequirement(kind=RequirementKind.SEASON,statement="season",value=q["season"]),
          UserRequirement(kind=RequirementKind.GAME_POPULATION,statement="competition",value=q["population"]),
          UserRequirement(kind=RequirementKind.TIME,statement="inclusive window",value=q["date_range"]),
          UserRequirement(kind=RequirementKind.ANALYSIS,statement="calculate requested metrics"),
          *[UserRequirement(kind=RequirementKind.MEASURE,statement=m["key"],value={**m,"derivable":True}) for m in c["inputs"]["requested_measures"]]]
    artifacts=[]
    if q["entity_filters"]:
        reqs.append(UserRequirement(kind=RequirementKind.ENTITY,statement="subject",value=dict(mention="fresh subject",role=q["analytical_role"])))
        artifacts.append(Artifact(kind="ENTITY_RESOLUTION",structured_data={"identity":{"fresh subject":{"level":"CANONICAL","id":c["subject"]}}}))
    constraints=copy.deepcopy(c["inputs"]["constraints"])
    constraints={k:v for k,v in constraints.items() if v is not None}
    reqs.append(UserRequirement(kind=RequirementKind.CONSTRAINT,statement="bound execution constraints",value=constraints))
    if q["qualification"]:
        reqs.append(UserRequirement(kind=RequirementKind.CONSTRAINT,statement="finite qualification",value=q["qualification"]))
    goal=Goal(statement="Fresh population metrics",raw_query="Calculate batting metrics" if q["analytical_role"] == "BATTER" else "Calculate pitching metrics",requirements=reqs)
    return goal,artifacts


def mirror(c,path,pg):
    import duckdb
    columns=list(c["rows"][0])
    values=", ".join("("+", ".join(("DATE "+render_literal(row[k])) if isinstance(row[k],dt.date) else render_literal(row[k]) for k in columns)+")" for row in c["rows"])
    physical="(VALUES "+values+") AS fixture("+", ".join(columns)+")"
    file=path/f"mlb_statcast_{c['raw']['season']}.parquet"
    db=duckdb.connect(":memory:");db.execute("CREATE TABLE fixture AS SELECT * FROM "+physical)
    db.execute(f"COPY fixture TO '{file}' (FORMAT PARQUET)");db.close()
    spec=dataclasses.replace(CATALOG.table("pitch"),physical={"POSTGRES":physical,"PARQUET":f"read_parquet('{file}')"},
                             key_map={"POSTGRES":CATALOG.table("pitch").key_map["POSTGRES"],"PARQUET":CATALOG.table("pitch").key_map["POSTGRES"]})
    catalog=SchemaCatalog({"pitch":spec})
    class Executor:
        calls=0
        def run(self,sql,source):
            if "public.player_dictionary" in sql: return QueryResult([],[],0,source,0,"names")
            self.calls+=1
            return pg.run(sql) if source == "POSTGRES" else DuckDBExecutor(str(path)).run(sql)
        def availability(self,source,relation):
            return SourceAvailability(source=source,relation=relation,known=True,start=min(r["game_date"] for r in c["rows"]),end=max(r["game_date"] for r in c["rows"]),
                                      seasons=(c["raw"]["season"],),version=hashlib.sha256(file.read_bytes()).hexdigest(),note="observed identical generated fixture")
    return catalog,Executor(),file


def main(stage,start,count):
    OUT.mkdir(parents=True,exist_ok=True)
    frozen=freeze_check() if stage == "holdout" else None
    cfg=get_config();pg=PostgresExecutor(cfg.postgres)
    probe=pg.run("SELECT 1 AS ok")
    if probe.error: raise RuntimeError(probe.error)
    records=[];path=Path("/tmp/m212r-mirror-"+stage);path.mkdir(exist_ok=True)
    def check(category,seed,ok,**detail): records.append(dict(category=category,seed=seed,ok=bool(ok),**encode(detail)))
    for seed in range(start,start+count):
        c=case(seed);catalog,ex,file=mirror(c,path,pg);compiler=SQLCompiler(catalog)
        tool=LocalAnalyticsTool(ex,FixedIR(c["raw"]));tool.catalog=catalog;tool.compiler=compiler
        request=ToolRequest(need_id="fresh",capability="local_analytics",objective="Calculate bound metrics",inputs=c["inputs"])
        intent,issues=bind_obligations(tool._intent(request),request.inputs)
        wanted=requested_contract(intent,{**request.inputs,"obligation_issues":issues},tool._requested_measures(request,intent))
        q=validate_query(AnalyticalQuery.model_validate(c["raw"]));expected=reference(c);results={}
        for source in ("POSTGRES","PARQUET"):
            compiled=compiler.compile(q,source);actual=ex.run(compiled.sql,source)
            check("reference_"+source,seed,not actual.error and equal(actual.rows,expected),actual=actual.rows,expected=expected,error=actual.error,sql=compiled.sql)
            a=attest(q,compiled,availability=ex.availability(source,"pitch"),executed=True)
            check("valid_match_"+source,seed,compare(wanted,a).status == "MATCH",conformance=compare(wanted,a))
            results[source]=actual.rows
        check("engine_parity",seed,equal(results["POSTGRES"],results["PARQUET"]),fixture_digest=hashlib.sha256(file.read_bytes()).hexdigest())
        tool.llm=FixedIR(c["raw"]);control=tool.execute(request);goal,identities=make_goal(c)
        projection=Judge().evaluate(copy.deepcopy(goal),identities+([control.artifact] if control.artifact else []),[])
        actual=[{k:v for k,v in row.items() if k in expected[0]} for row in control.artifact.structured_data["rows"]] if control.artifact and expected else control.artifact.structured_data["rows"] if control.artifact else None
        check("valid_tool_execution",seed,control.artifact is not None and equal(actual,expected),result=control,judge=projection,request=request,ir=c["raw"])
        check("valid_receipt",seed,control.artifact is not None and receipt_usable(control.artifact))
        # Positive completion gate only when source observations and requested facts exist.
        if control.artifact and control.artifact.status == "ACCEPTED":
            check("valid_judge_complete",seed,projection.goal_status.value == "COMPLETE",projection=projection)
        for mutation in mutations(c):
            query=validate_query(AnalyticalQuery.model_validate(mutation["ir"]));a=attest(query,compiler.compile(query,"POSTGRES"))
            result=compare(wanted,a,check_availability=False);tool.llm=FixedIR(mutation["ir"]);before=ex.calls;r=tool.execute(request)
            judged=Judge().evaluate(copy.deepcopy(goal),identities+([r.artifact] if r.artifact else []),[])
            check("mutation_"+mutation["name"],seed,result.status == mutation["expected"],dimension=mutation["dimension"],expected=mutation["expected"],conformance=result,
                  syntactically_valid=True,ir=mutation["ir"],outcome=r.outcome,executed_queries=ex.calls-before,judge_status=judged.goal_status.value)
            if mutation["expected"] != "MATCH":
                check("incorrect_COMPLETE",seed,judged.goal_status.value != "COMPLETE",family=mutation["name"],incorrect_COMPLETE=judged.goal_status.value == "COMPLETE")
            else:
                check("semantic_equivalence_acceptance",seed,r.artifact is not None and r.outcome.code in {"OK","VALID_EMPTY"},family=mutation["name"],outcome=r.outcome)
        # Shorter observation coverage is not full conformance, with unchanged logical scope.
        a=attest(q,compiler.compile(q,"POSTGRES"),availability=ex.availability("POSTGRES","pitch").model_copy(update={"start":q.date_range.start+dt.timedelta(days=2)}),executed=True)
        check("source_coverage_mismatch",seed,compare(wanted,a).status == "PARTIAL")
        print(stage,seed,projection.goal_status.value,flush=True)
    summary={}
    for row in records:
        tally=summary.setdefault(row["category"],dict(passed=0,failed=0));tally["passed" if row["ok"] else "failed"]+=1
    payload=dict(stage=stage,sha=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),frozen_sha=frozen["implementation_sha"] if frozen else None,
                 seeds=list(range(start,start+count)),summary=summary,checks=records)
    (OUT/(stage+"-analytics.json")).write_text(json.dumps(payload,indent=2,default=str)+"\n")
    if frozen: freeze_check()
    print(json.dumps(summary),flush=True)


if __name__ == "__main__":
    p=argparse.ArgumentParser();p.add_argument("stage",choices=["development","holdout"]);p.add_argument("--seed-start",type=int,required=True);p.add_argument("--count",type=int,required=True)
    args=p.parse_args();main(args.stage,args.seed_start,args.count)
