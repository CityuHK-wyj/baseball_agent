#!/usr/bin/env python3
"""M2.12 live user traces; generation and references remain evaluation-only."""
import copy,dataclasses,datetime as dt,hashlib,json,random,sys,subprocess,time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import get_config
from app.runtime.engine import Engine
from app.runtime.context import SystemClock
from tools.m212_evaluation_support import encode
OUT=Path('docs/audit/m212')
class FixedClock(SystemClock):
    def today(self):return dt.date(2026,9,14)
    def now(self):return dt.datetime(2026,9,14,12,tzinfo=dt.timezone.utc)

def tasks(stage):
    base=21215000 if stage=='development' else 21296000
    r=random.Random(base)
    choices=[('Manny Machado',592518),('Corey Seager',608369),('Marcus Semien',543760),('Matt Olson',621566),('Trea Turner',607208),('Nolan Arenado',571448)]
    r.shuffle(choices);p,pid=choices[0];h,hid=choices[1];year=r.choice([2024,2025]);history=r.choice([2021,2022]);minimum=r.choice([183,247,367]);month=r.choice([5,7,8])
    items=[('scalar',f'How many doubles did {p} hit in the {year} regular season?',dict(player=pid,year=year,metric='doubles')),
           ('historical',f'How many walks did {h} have in the {history} regular season?',dict(player=hid,year=history,metric='walks')),
           ('derived',f'What was {p}\'s batting average in the {year} regular season?',dict(player=pid,year=year,metric='avg')),
           ('splits',f'Give {p}\'s plate appearances against left-handed and right-handed pitchers in the {year} regular season, separately.',dict(player=pid,year=year,metric='pa',group='p_throws')),
           ('rank',f'Which three batters had the most triples in the {year} regular season? Show their MLBAM IDs and triple totals, break ties by ascending MLBAM ID.',dict(year=year,metric='triples',rank=3)),
           ('qualified_rank',f'Which three batters had the most hits in the {year} regular season among batters with at least {minimum} plate appearances? Show MLBAM IDs and hits, break ties by ascending MLBAM ID.',dict(year=year,metric='hits',rank=3,minimum=minimum)),
           ('window',f'How many pitches did {p} see in regular-season games from {year}-{month:02}-04 through {year}-{month:02}-21 inclusive?',dict(player=pid,year=year,metric='pitches',start=f'{year}-{month:02}-04',end=f'{year}-{month:02}-21')),
           ('cross_source',f'How many triples did {h} hit in the {history} and {year} regular seasons separately, and what is {year} minus {history}?',dict(player=hid,years=[history,year],metric='triples')),
           ('invalid',f'Calculate {p}\'s earned-run average as a batter in the {year} regular season.',dict(invalid=True)),
           ('ambiguous','How many hits did Garcia have in the 2025 regular season?',dict(ambiguous=True))]
    return [dict(id=name,query=query,spec=spec,seed=base+i) for i,(name,query,spec) in enumerate(items)]

def main(stage):
    if stage=='holdout':
        frozen=json.loads((OUT/'IMPLEMENTATION_FROZEN.json').read_text())
        for path,expected in frozen['production_hashes'].items():assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==expected
    ts=tasks(stage);(OUT/(stage+'-user-tasks.json')).write_text(json.dumps(ts,indent=2)+'\n')
    def run(task):
        cfg=get_config();cfg=dataclasses.replace(cfg,llm=dataclasses.replace(cfg.llm,runtime_model='deepseek-chat',semantic_model='deepseek-chat',planner_model='deepseek-chat',analytics_model='deepseek-chat',response_model='deepseek-chat',timeout_seconds=25),runtime_dir=Path('/tmp/m212-'+stage+'-'+task['id']+'-'+str(time.time_ns())))
        engine=Engine(cfg);engine.clock=FixedClock();capture=dict(task=task,requests=[],candidates=[],planner=[],judge=[],binder=[])
        def wrap(obj,name,items):
            original=getattr(obj,name)
            def call(*args,**kw):
                record=dict(args=encode(args),kwargs=encode(kw)) if name=='_build_ir' else dict(request=encode(args[0]) if args else encode(kw)) if name=='execute' else dict(kwargs=encode(kw))
                value=original(*args,**kw);record['result']=encode(value);items.append(record);return value
            setattr(obj,name,call)
        wrap(engine.planner,'plan',capture['planner']);wrap(engine.judge,'evaluate',capture['judge']);wrap(engine.binder,'bind',capture['binder'])
        for capability in engine.registry.names():wrap(engine.registry.get(capability),'execute',capture['requests'])
        wrap(engine.registry.get('local_analytics'),'_build_ir',capture['candidates'])
        try:capture['turn']=encode(engine.ask('m212_'+stage+'_'+task['id'],task['query'],trace_level='off'))
        except Exception as e:capture['exception']=type(e).__name__+': '+str(e)
        (OUT/(stage+'-user-'+task['id']+'.json')).write_text(json.dumps(capture,indent=2,default=str)+'\n')
        print(stage,task['id'],capture.get('turn',{}).get('projection',{}).get('goal_status',capture.get('exception')),flush=True)
        return dict(id=task['id'],status=capture.get('turn',{}).get('projection',{}).get('goal_status'),exception=capture.get('exception'))
    with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(run,ts))
    # References are computed only after the natural-language task set has been fixed.
    from app.capabilities.analytics.executor import AnalyticalExecutor
    cfg=get_config();ex=AnalyticalExecutor(cfg.postgres,cfg.parquet.archive_path);references=[]
    for task in ts:
        spec=task['spec']
        if spec.get('invalid') or spec.get('ambiguous'):continue
        for year in spec.get('years',[spec.get('year')]):
            source='PARQUET' if year<2024 else 'POSTGRES';table='public.statcast_pitches' if source=='POSTGRES' else f"read_parquet('{cfg.parquet.archive_path}/mlb_statcast_{year}.parquet')";key='batter_id' if source=='POSTGRES' else 'batter'
            metric=spec['metric'];expressions={'doubles':"COUNT(*) FILTER(WHERE events='double')",'triples':"COUNT(*) FILTER(WHERE events='triple')",'walks':"COUNT(*) FILTER(WHERE events IN ('walk','intent_walk'))",'hits':"COUNT(*) FILTER(WHERE events IN ('single','double','triple','home_run'))",'pa':'COUNT(events)','pitches':'COUNT(*)','avg':"1.0*COUNT(*) FILTER(WHERE events IN ('single','double','triple','home_run'))/NULLIF(COUNT(*) FILTER(WHERE events IS NOT NULL AND events NOT IN ('walk','intent_walk','hit_by_pitch','catcher_interf','sac_fly','sac_bunt','sac_fly_double_play','sac_bunt_double_play','truncated_pa','game_advisory')),0)"}
            group=key if spec.get('rank') else spec.get('group');sql=f"SELECT {group+' AS '+('batter_id' if spec.get('rank') else group)+',' if group else ''}{expressions[metric]} AS value FROM {table} WHERE game_type='R' AND EXTRACT(YEAR FROM game_date)={year}"
            if spec.get('player'):sql+=f" AND {key}={spec['player']}"
            if spec.get('start'):sql+=f" AND game_date BETWEEN '{spec['start']}' AND '{spec['end']}'"
            if group:sql+=' GROUP BY '+group
            if spec.get('minimum'):sql+=f" HAVING COUNT(events)>={spec['minimum']}"
            if group:sql+=f" ORDER BY {'value DESC NULLS LAST, '+key+' ASC' if spec.get('rank') else group+' ASC NULLS LAST'}"
            if spec.get('rank'):sql+=f" LIMIT {spec['rank']}"
            result=ex.run(sql,source);references.append(dict(id=task['id'],year=year,sql=sql,result=encode(result)))
    (OUT/(stage+'-user-references.json')).write_text(json.dumps(references,indent=2,default=str)+'\n')
    (OUT/(stage+'-user-summary.json')).write_text(json.dumps(results,indent=2)+'\n')
if __name__=='__main__':main(sys.argv[1])
