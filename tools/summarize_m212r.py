#!/usr/bin/env python3
"""Evidence accounting; independent reference values remain the ground truth."""
import gzip
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.m212_evaluation_support import equal
from tools.evaluate_m212r import OUT, freeze_check


def read(path):
    if path.exists(): return json.loads(path.read_text())
    with gzip.open(str(path)+".gz", "rt") as stream: return json.load(stream)


def suite(name):
    path=OUT/(name+".xml")
    if not path.exists(): return dict(pending=True)
    root=ET.parse(path).getroot();s=root[0].attrib
    failures=[dict(name=t.attrib.get("classname","")+"."+t.attrib["name"],message=t.find("failure").attrib.get("message","") if t.find("failure") is not None else t.find("error").attrib.get("message","")) for t in root.iter("testcase") if t.find("failure") is not None or t.find("error") is not None]
    return dict(passed=int(s["tests"])-int(s["failures"])-int(s["errors"])-int(s["skipped"]),failed=int(s["failures"]),errors=int(s["errors"]),skipped=int(s["skipped"]),failures=failures)


def users(stage):
    refs={x["id"]:x for x in read(OUT/(stage+"-user-references.json"))};out=[]
    for task in read(OUT/(stage+"-user-tasks.json")):
        capture=read(OUT/(stage+"-user-"+task["id"]+".json"));turn=capture.get("turn",{});s=task["spec"];arts=turn.get("artifacts",[])
        db=[a for a in arts if a["kind"] == "DATABASE_RESULT"];found=False
        ref=refs.get(task["id"])
        for a in db:
            if not ref: continue
            att=a["structured_data"]["execution_receipt"]["attestation"]
            if att["analytical_role"] != s.get("role"): continue
            for m in att["measures"]:
                if not m.get("verified") or m.get("key") != s.get("metric"): continue
                group={"BATTER":"batter_id","PITCHER":"pitcher_id"}[s["role"]] if s.get("rank") else s.get("group")
                values=[{**({group:r.get(group)} if group else {}),"value":r.get(m["column"])} for r in a["structured_data"]["rows"]]
                if equal(values,ref["result"]["rows"]): found=True
        before=capture.get("judge",[])[-1]["result"]["goal_status"] if capture.get("judge") else None
        negative=bool(s.get("ambiguous") or s.get("invalid"))
        # Final prose is reviewed independently against refs; zero-observation receipt
        # correctly remains a typed empty result but the prose does not report zero.
        final_correct=(turn.get("projection",{}).get("goal_status") == "WAITING_FOR_USER") if s.get("ambiguous") else not db if s.get("invalid") else found and task["id"] != "recent_postgres" if stage == "holdout" else found
        out.append(dict(id=task["id"],seed=task["seed"],query=task["query"],reference=ref["result"]["rows"] if ref else None,
                        numerical_correct=found if not negative else None,final_answer_correct=final_correct,negative=negative,before_consistency=before,
                        final_status=turn.get("projection",{}).get("goal_status"),answer=turn.get("answer"),database_artifacts=len(db),
                        nonexecution=not negative and not db,outcome_codes=[x["code"] for x in turn.get("outcomes",[])],
                        incorrect_COMPLETE=not negative and not found and before == "COMPLETE"))
    return out


def main():
    freeze_check()
    primary=read(OUT/"holdout-analytics.json");supp=read(OUT/"holdout-postfreeze-review.json")
    main_drift=[r for r in primary["checks"] if r["category"] == "incorrect_COMPLETE"]
    extra_drift=[r for r in supp["checks"] if r["kind"] == "drift"]
    extra_positive=[r for r in supp["checks"] if r["kind"] == "positive"]
    result=dict(primary_analytics=primary["summary"],postfreeze_review=supp["summary"],development_analytics=read(OUT/"development-analytics.json")["summary"],
                incorrect_COMPLETE=dict(primary=sum(r["incorrect_COMPLETE"] for r in main_drift),primary_denominator=len(main_drift),supplement=sum(r["incorrect_COMPLETE"] for r in extra_drift),supplement_denominator=len(extra_drift),
                                       combined=sum(r["incorrect_COMPLETE"] for r in main_drift+extra_drift),combined_denominator=len(main_drift)+len(extra_drift)),
                utility=dict(primary_tool_controls=primary["summary"]["valid_tool_execution"],primary_equivalence=primary["summary"]["semantic_equivalence_acceptance"],
                             supplemental_positive_total=len(extra_positive),supplemental_false_rejection=sum(not r["ok"] for r in extra_positive),
                             supplemental_conformance_false_rejection=sum(r["conformance"] != "MATCH" for r in extra_positive),
                             supplemental_judge_false_rejection=sum(r["conformance"] == "MATCH" and r["judge_status"] != "COMPLETE" for r in extra_positive)),
                legacy=read(OUT/"legacy-m212.json"),development_users=users("development"),holdout_users=users("holdout"))
    for name in ("development-offline","development-integration-live","final-offline","final-integration"): result[name]=suite(name)
    result["natural_language_metrics"]={}
    for stage in ("development","holdout"):
        rows=result[stage+"_users"];valid=[x for x in rows if not x["negative"]]
        result["natural_language_metrics"][stage]=dict(valid_tasks=len(valid),calculation_correct=sum(x["numerical_correct"] for x in valid),final_answer_correct=sum(x["final_answer_correct"] for x in valid),valid_nonexecution=sum(x["nonexecution"] for x in valid),
                                                      incorrect_COMPLETE=sum(x["incorrect_COMPLETE"] for x in valid),before_consistency_complete=sum(x["before_consistency"] == "COMPLETE" for x in valid),final_complete=sum(x["final_status"] == "COMPLETE" for x in valid),negative_correct=sum(x["final_answer_correct"] for x in rows if x["negative"]))
    (OUT/"metrics.json").write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps({k:result[k] for k in ("incorrect_COMPLETE","utility","natural_language_metrics","final-offline","final-integration")},indent=2))


if __name__ == "__main__": main()
