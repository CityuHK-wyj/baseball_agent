#!/usr/bin/env python3
"""Post-freeze review, authored after a2f5f54: challenge request ownership itself.

Unlike the primary candidate-only framework, Goals here are authored independently
from Need constraints. Defaults are left implicit in the Goal, as real Requirements
do. This intentionally searches for another contract-dimension escape.
"""
import copy
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.capabilities.analytics.compiler import SQLCompiler
from app.capabilities.analytics.conformance import SourceAvailability
from app.capabilities.analytics.executor import PostgresExecutor
from app.capabilities.analytics.ir import AnalyticalQuery, validate_query
from app.capabilities.analytics.tool import LocalAnalyticsTool
from app.config import get_config
from app.contracts.models import Artifact, Goal, RequirementKind, ToolRequest, UserRequirement
from app.verification.judge import Judge
from tools.evaluate_m212r import OUT, FixedIR, freeze_check, mirror
from tools.m212r_cases import case
from tools.m212_evaluation_support import equal, encode


def main():
    frozen=freeze_check();seed_manifest=json.loads((OUT/"UNSEEN_SEEDS.json").read_text())
    records=[];pg=PostgresExecutor(get_config().postgres);path=Path("/tmp/m212r-postfreeze");path.mkdir(exist_ok=True)
    probe=pg.run("SELECT 1 AS ok")
    if probe.error: raise RuntimeError(probe.error)
    for seed in seed_manifest["post_freeze_review"]:
        c=case(seed);base=c["raw"];role=base["analytical_role"];subject="batter" if role == "BATTER" else "pitcher"
        catalog,ex,file=mirror(c,path,pg);compiler=SQLCompiler(catalog)
        raw=dict(relation="pitch",analytical_role=role,season=base["season"],date_range=base["date_range"],population=base["population"],
                 entity_filters=base["entity_filters"],filters=[],group_by=[],aggregates=[dict(name="pa",function="COUNT_NON_NULL",field="events")])
        inputs=dict(intent=dict(season=base["season"],date_range=base["date_range"],population=base["population"],objective="Report total plate appearances"),
                    entity_sets=copy.deepcopy(c["inputs"]["entity_sets"]),entity_role=role,require_entities=bool(raw["entity_filters"]),requested_measures=[dict(key="pa")],constraints={})
        requirements=[UserRequirement(kind=RequirementKind.SEASON,statement="season",value=base["season"]),
                      UserRequirement(kind=RequirementKind.TIME,statement="window",value=base["date_range"]),
                      UserRequirement(kind=RequirementKind.GAME_POPULATION,statement="competition",value=base["population"]),
                      UserRequirement(kind=RequirementKind.MEASURE,statement="Plate appearances",value=dict(key="pa",derivable=True)),
                      UserRequirement(kind=RequirementKind.ANALYSIS,statement="Report total plate appearances for the complete eligible row population",value=dict(objective="Report total plate appearances for the complete eligible row population"))]
        identities=[]
        if raw["entity_filters"]:
            requirements.append(UserRequirement(kind=RequirementKind.ENTITY,statement="subject",value=dict(mention="post-freeze subject",role=role)))
            identities=[Artifact(kind="ENTITY_RESOLUTION",structured_data={"identity":{"post-freeze subject":{"level":"CANONICAL","id":c["subject"]}}})]
        goal=Goal(statement="Complete requested population",raw_query=f"Report total plate appearances as a {subject} for all eligible observations in the bound window and competition.",requirements=requirements)
        eligible=[r for r in c["rows"] if r["game_type"] in c["codes"] and base["date_range"]["start"] <= r["game_date"].isoformat() <= base["date_range"]["end"] and (not raw["entity_filters"] or r[c["identity"]] == c["subject"])]
        expected=[dict(pa=sum(r["events"] is not None for r in eligible))]
        def run(name,q,ins,g,expected_rows,expected_status,kind):
            validate_query(AnalyticalQuery.model_validate(q))
            ins=copy.deepcopy(ins);ins.update(raw_query=g.raw_query,analytical_requirements=[r.model_dump(mode="json") for r in g.requirements])
            request=ToolRequest(need_id="postfreeze",capability="local_analytics",objective=ins["intent"]["objective"],inputs=ins)
            tool=LocalAnalyticsTool(ex,FixedIR(q));tool.catalog=catalog;tool.compiler=compiler
            result=tool.execute(request);projection=Judge().evaluate(copy.deepcopy(g),identities+([result.artifact] if result.artifact else []),[])
            rows=result.artifact.structured_data["rows"] if result.artifact else None
            status=result.artifact.structured_data["execution_receipt"]["conformance"]["status"] if result.artifact else result.outcome.code
            correct=rows is not None and equal([{k:r.get(k) for k in expected_rows[0]} for r in rows],expected_rows) if expected_rows else rows == []
            incorrect_complete=kind == "drift" and projection.goal_status.value == "COMPLETE"
            record=dict(seed=seed,name=name,kind=kind,expected=expected_rows,expected_status=expected_status,conformance=status,judge_status=projection.goal_status.value,
                        reference_correct=correct,incorrect_COMPLETE=incorrect_complete,ok=(correct and status == "MATCH" and projection.goal_status.value == "COMPLETE") if kind == "positive" else projection.goal_status.value != "COMPLETE",
                        request=encode(request),goal=encode(g),ir=q,result=encode(result),projection=encode(projection),syntactically_valid=True)
            records.append(record)
        run("independent_goal_default_control",raw,inputs,goal,expected,"MATCH","positive")
        # The current Goal has no explicit filter: default means every eligible row.
        # Add one restriction to the Planner request as well as the execution IR.
        p=dict(op="COMPARE",field="strikes",comparator=">=",value=1)
        q=copy.deepcopy(raw);q["filters"]=[p];ins=copy.deepcopy(inputs);ins["constraints"]["filters"]=[p]
        run("unexpected_restriction_laundered_through_need",q,ins,goal,expected,"NOT_COMPLETE","drift")
        # Alter output grain through Need metadata while the independent Goal asks
        # for one total. Same scope and governed COUNT(events), syntactically valid.
        q=copy.deepcopy(raw);q["group_by"]=["strikes"];ins=copy.deepcopy(inputs);ins["constraints"]["group_by"]=["strikes"]
        run("unexpected_grouping_laundered_through_need",q,ins,goal,expected,"NOT_COMPLETE","drift")
        # A split is explicitly required, but no truncation is authorized.
        split_goal=copy.deepcopy(goal);split_goal.requirements[-1 if not raw["entity_filters"] else -2].statement="Split plate appearances by pitcher handedness"
        analysis=next(r for r in split_goal.requirements if r.kind == RequirementKind.ANALYSIS)
        analysis.statement="Split plate appearances by pitcher handedness";analysis.value=dict(objective=analysis.statement)
        split_goal.raw_query=f"Report plate appearances as a {subject} split by pitcher handedness for the complete eligible population."
        groups={}
        for row in eligible:groups.setdefault(row["p_throws"],[]).append(row)
        split_expected=[dict(p_throws=side,pa=sum(x["events"] is not None for x in rows)) for side,rows in sorted(groups.items(),key=lambda item:(item[0] is None,item[0] or ""))]
        q=copy.deepcopy(raw);q.update(group_by=["p_throws"],sort=[dict(name="p_throws",direction="ASC")])
        ins=copy.deepcopy(inputs);ins["intent"]["objective"]=analysis.statement;ins["constraints"].update(group_by=["p_throws"],ordering=[dict(field="p_throws",direction="ASC")])
        run("independent_goal_full_split_control",q,ins,split_goal,split_expected,"MATCH","positive")
        q["limit"]=1;ins["constraints"]["limit"]=1
        run("unexpected_limit_laundered_through_need",q,ins,split_goal,split_expected,"NOT_COMPLETE","drift")
        # A single normalized constraint carries two obligations. Finite adapter
        # must not recognize the PA threshold and silently ignore the IN predicate.
        compound=copy.deepcopy(goal);minimum=max(0,len(eligible)//9)
        compound.requirements.append(UserRequirement(kind=RequirementKind.CONSTRAINT,statement="sample and side",value=f"at least {minimum} PA and p_throws IN ('R')"))
        compound.raw_query=f"Report plate appearances as a {subject} for right-handed pitchers only, with at least {minimum} PA."
        q=copy.deepcopy(raw);q["qualification"]=dict(basis="PA",minimum=minimum)
        side_expected=[dict(pa=sum(r["events"] is not None and r["p_throws"] == "R" for r in eligible))]
        ins=copy.deepcopy(inputs);ins["intent"]["qualification"]=q["qualification"]
        run("partially_recognized_compound_obligation",q,ins,compound,side_expected,"NOT_COMPLETE","drift")
        # Finite same-field OR-equality and IN are identical under SQL NULL semantics.
        eq_goal=copy.deepcopy(goal);p=dict(op="IN",field="pitch_type",values=["SI","FC"])
        eq_goal.requirements.append(UserRequirement(kind=RequirementKind.CONSTRAINT,statement="pitch population",value=dict(filters=[p])))
        q=copy.deepcopy(raw);q["filters"]=[dict(op="OR",conditions=[dict(op="COMPARE",field="pitch_type",comparator="=",value="FC"),dict(op="COMPARE",field="pitch_type",comparator="=",value="SI")])]
        eq_expected=[dict(pa=sum(r["events"] is not None and r["pitch_type"] in {"SI","FC"} for r in eligible))]
        run("finite_or_equals_vs_in",q,inputs,eq_goal,eq_expected,"MATCH","positive")
        # Decimal/integer season equality is accepted by IR and compiles identically.
        q=copy.deepcopy(raw);q["season"]=None;q["filters"]=[dict(op="COMPARE",field="season",comparator="=",value=float(base["season"]))]
        run("integer_valued_float_season_predicate",q,inputs,goal,expected,"MATCH","positive")
        # Same exact effective time window, with redundant season field omitted.
        q=copy.deepcopy(raw);q.pop("season")
        run("same_window_without_redundant_season",q,inputs,goal,expected,"MATCH","positive")
        # Supplementary coverage denial uses observed availability metadata only.
        original=ex.availability
        ex.availability=lambda source,relation: SourceAvailability(source=source,relation=relation,known=True,start=dt.date(base["season"]+1,1,1),end=dt.date(base["season"]+1,12,31))
        run("unavailable_source_cannot_complete",raw,inputs,goal,expected,"NOT_COMPLETE","drift")
        ex.availability=original
        print(seed,[(r["name"],r["judge_status"],r["incorrect_COMPLETE"]) for r in records if r["seed"] == seed],flush=True)
    summary={}
    for r in records:
        s=summary.setdefault(r["name"],dict(passed=0,failed=0,incorrect_COMPLETE=0));s["passed" if r["ok"] else "failed"]+=1;s["incorrect_COMPLETE"]+=r["incorrect_COMPLETE"]
    payload=dict(frozen_sha=frozen["implementation_sha"],authored_after_freeze=True,sha=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),seeds=seed_manifest["post_freeze_review"],summary=summary,checks=records)
    (OUT/"holdout-postfreeze-review.json").write_text(json.dumps(payload,indent=2,default=str)+"\n")
    freeze_check();print(json.dumps(summary),flush=True)


if __name__ == "__main__": main()
