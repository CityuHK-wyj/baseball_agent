#!/usr/bin/env python3
"""Fresh user tasks fixed before independently authored real-source references."""
import argparse
import copy
import dataclasses
import datetime as dt
import json
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import get_config
from app.runtime.engine import Engine
from tools.evaluate_m212_live import FixedClock
from tools.evaluate_m212r import OUT, freeze_check
from tools.m212_evaluation_support import encode


def tasks(seed,stage):
    r=random.Random(seed)
    batters=[("Jose Ramirez",608070),("Francisco Lindor",596019),("Ketel Marte",606466),("Christian Yelich",592885)]
    pitchers=[("Logan Webb",657277),("Framber Valdez",664285),("George Kirby",669923),("Corbin Burnes",669203)]
    # Separate entity pools are selected for development and post-freeze holdout.
    batter,bid=r.choice(batters[:2] if stage == "development" else batters[2:])
    pitcher,pid=r.choice(pitchers[:2] if stage == "development" else pitchers[2:])
    historical,hid=("Miguel Cabrera",408234) if stage == "development" else ("Joey Votto",458015)
    minimum=r.randint(151,289);year=r.choice([2024,2025]);month=r.choice([4,6,8]);n=r.randint(3,5)
    items=[("individual_batter",f"How many strikeouts did {batter} have as a batter in the {year} regular season?",dict(player=bid,year=year,metric="strikeouts",role="BATTER")),
           ("individual_pitcher",f"What was {pitcher}'s average release speed as a pitcher in the {year} regular season?",dict(player=pid,year=year,metric="avg_release_speed",role="PITCHER")),
           ("batter_ranking",f"List the top {n} batters by walks in the {year} regular season, with MLBAM IDs and walk totals. Break ties by ascending MLBAM ID.",dict(year=year,metric="walks",rank=n,role="BATTER")),
           ("pitcher_ranking",f"List the top {n} pitchers by strikeouts in the {year} regular season, with MLBAM IDs and strikeout totals. Break ties by ascending MLBAM ID.",dict(year=year,metric="strikeouts",rank=n,role="PITCHER")),
           ("categorical_split",f"Report {batter}'s plate appearances as a batter split by opposing pitcher handedness in the {year} regular season.",dict(player=bid,year=year,metric="pa",group="p_throws",role="BATTER")),
           ("row_filtered",f"How many plate appearances did {batter} have as a batter against right-handed pitchers in the {year} regular season? Restrict the population to p_throws equal to R.",dict(player=bid,year=year,metric="pa",filter="p_throws='R'",role="BATTER")),
           ("qualified_rank",f"Which {n} batters had the most strikeouts in the {year} regular season among batters with at least {minimum} plate appearances? Show MLBAM IDs and strikeouts, with ties ordered by ascending MLBAM ID.",dict(year=year,metric="strikeouts",rank=n,minimum=minimum,role="BATTER")),
           ("recent_postgres",f"How many pitches did {pitcher} throw as a pitcher in regular-season games from 2026-08-03 through 2026-08-24 inclusive?",dict(player=pid,year=2026,metric="pitches",start="2026-08-03",end="2026-08-24",role="PITCHER")),
           ("historical_parquet",f"How many doubles did {historical} hit as a batter in the 2019 regular season?",dict(player=hid,year=2019,metric="doubles",role="BATTER")),
           ("derived_rate",f"What was {batter}'s walk rate (BB%, 100 times walks divided by plate appearances) as a batter in the {year} regular season?",dict(player=bid,year=year,metric="bb_pct",role="BATTER")),
           ("ambiguous",f"What were Lopez's overall statistics as a player in the {year} regular season?",dict(ambiguous=True)),
           ("unsupported",f"What was {pitcher}'s WHIP as a pitcher in the {year} regular season?",dict(invalid=True))]
    return [dict(id=name,query=query,spec=spec,seed=seed+i) for i,(name,query,spec) in enumerate(items)]


def main(stage,seed):
    if stage == "holdout": freeze_check()
    ts=tasks(seed,stage);OUT.mkdir(exist_ok=True,parents=True)
    (OUT/(stage+"-user-tasks.json")).write_text(json.dumps(ts,indent=2)+"\n")
    def run(task):
        cfg=get_config();cfg=dataclasses.replace(cfg,llm=dataclasses.replace(cfg.llm,runtime_model="deepseek-chat",semantic_model="deepseek-chat",planner_model="deepseek-chat",analytics_model="deepseek-chat",response_model="deepseek-chat",timeout_seconds=25),runtime_dir=Path("/tmp/m212r-"+stage+"-"+task["id"]+"-"+str(time.time_ns())))
        engine=Engine(cfg);engine.clock=FixedClock();capture=dict(task=task,requests=[],candidates=[],planner=[],judge=[],binder=[])
        def wrap(obj,name,items):
            original=getattr(obj,name)
            def call(*args,**kw):
                record=dict(args=encode(args),kwargs=encode(kw)) if name == "_build_ir" else dict(request=encode(args[0]) if args else encode(kw)) if name == "execute" else dict(kwargs=encode(kw))
                value=original(*args,**kw);record["result"]=encode(value);items.append(record);return value
            setattr(obj,name,call)
        wrap(engine.planner,"plan",capture["planner"]);wrap(engine.judge,"evaluate",capture["judge"]);wrap(engine.binder,"bind",capture["binder"])
        for capability in engine.registry.names(): wrap(engine.registry.get(capability),"execute",capture["requests"])
        wrap(engine.registry.get("local_analytics"),"_build_ir",capture["candidates"])
        try: capture["turn"]=encode(engine.ask("m212r_"+stage+"_"+task["id"],task["query"],trace_level="off"))
        except Exception as error: capture["exception"]=type(error).__name__+": "+str(error)
        (OUT/(stage+"-user-"+task["id"]+".json")).write_text(json.dumps(capture,indent=2,default=str)+"\n")
        print(stage,task["id"],capture.get("turn",{}).get("projection",{}).get("goal_status",capture.get("exception")),flush=True)
        return dict(id=task["id"],status=capture.get("turn",{}).get("projection",{}).get("goal_status"),exception=capture.get("exception"))
    with ThreadPoolExecutor(max_workers=3) as pool: results=list(pool.map(run,ts))
    # No numerical reference is available to any model invocation above.
    from app.capabilities.analytics.executor import AnalyticalExecutor
    cfg=get_config();ex=AnalyticalExecutor(cfg.postgres,cfg.parquet.archive_path);refs=[]
    expressions={"strikeouts":"COUNT(*) FILTER (WHERE events IN ('strikeout','strikeout_double_play'))", "walks":"COUNT(*) FILTER (WHERE events IN ('walk','intent_walk'))", "doubles":"COUNT(*) FILTER (WHERE events='double')", "avg_release_speed":"AVG(release_speed)", "pa":"COUNT(events)", "pitches":"COUNT(*)", "bb_pct":"100.0*COUNT(*) FILTER (WHERE events IN ('walk','intent_walk'))/NULLIF(COUNT(events),0)"}
    for task in ts:
        s=task["spec"]
        if s.get("ambiguous") or s.get("invalid"): continue
        year=s["year"];source="POSTGRES" if year >= 2024 else "PARQUET"
        table="public.statcast_pitches" if source == "POSTGRES" else f"read_parquet('{cfg.parquet.archive_path}/mlb_statcast_{year}.parquet')"
        identity="batter_id" if s["role"] == "BATTER" else "pitcher_id"
        physical=identity if source == "POSTGRES" else "batter" if identity == "batter_id" else "pitcher"
        group=physical if s.get("rank") else s.get("group")
        sql=f"SELECT {group+' AS '+(identity if s.get('rank') else group)+',' if group else ''}{expressions[s['metric']]} AS value FROM {table} WHERE game_type='R' AND EXTRACT(YEAR FROM game_date)={year}"
        if s.get("player"): sql+=f" AND {physical}={s['player']}"
        if s.get("filter"): sql+=" AND "+s["filter"]
        if s.get("start"): sql+=f" AND game_date BETWEEN '{s['start']}' AND '{s['end']}'"
        if group: sql+=" GROUP BY "+group
        if s.get("minimum"): sql+=f" HAVING COUNT(events)>={s['minimum']}"
        if group: sql+=" ORDER BY "+(f"value DESC NULLS LAST,{physical} ASC" if s.get("rank") else group+" ASC NULLS LAST")
        if s.get("rank"): sql+=f" LIMIT {s['rank']}"
        result=ex.run(sql,source);refs.append(dict(id=task["id"],sql=sql,source=source,result=encode(result)))
    (OUT/(stage+"-user-references.json")).write_text(json.dumps(refs,indent=2,default=str)+"\n")
    (OUT/(stage+"-user-summary.json")).write_text(json.dumps(results,indent=2)+"\n")
    if stage == "holdout": freeze_check()


if __name__ == "__main__":
    p=argparse.ArgumentParser();p.add_argument("stage",choices=["development","holdout"]);p.add_argument("--seed",type=int,required=True)
    args=p.parse_args();main(args.stage,args.seed)
