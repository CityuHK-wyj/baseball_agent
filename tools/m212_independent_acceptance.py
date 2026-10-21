#!/usr/bin/env python3
"""Independent acceptance infrastructure. No prior audit generators/oracles used."""
from __future__ import annotations
import argparse
import copy
import dataclasses
import datetime as dt
import decimal
import hashlib
import json
import math
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
OUT = Path('docs/audit/m212_independent')
SEEDS = dict(corpus=1052026212, rows=1052026283, attacks=1052026297)

def enc(v):
    if hasattr(v, 'model_dump'): return v.model_dump(mode='json')
    if dataclasses.is_dataclass(v):
        return {f.name: enc(getattr(v, f.name)) for f in dataclasses.fields(v) if f.name != 'trace'}
    if isinstance(v, dict): return {str(k): enc(x) for k, x in v.items()}
    if isinstance(v, (tuple, list)): return [enc(x) for x in v]
    if isinstance(v, (dt.date, dt.datetime, Path)): return str(v)
    if isinstance(v, decimal.Decimal): return float(v)
    return v

def save(name, v):
    (OUT / name).write_text(json.dumps(enc(v), indent=2, ensure_ascii=False, default=str)+'\n')

def freeze():
    actual = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path('app').rglob('*.py'))}
    expected = json.loads(Path('docs/audit/m212r3/IMPLEMENTATION_FROZEN.json').read_text())['production_hashes']
    assert actual == expected, 'production differs from frozen manifest'
    return actual

def corpus():
    r = random.Random(SEEDS['corpus']); n = r.choice([4,6]); minimum = r.randrange(167,194)
    items=[]
    def add(id, q, year=2025, role='BATTER', metric='pa', player=None, **s):
        spec=dict(year=year, role=role, metric=metric, player=player, **s)
        ledger={k:v for k,v in spec.items() if v is not None}
        ledger['competition']='REGULAR_SEASON';ledger['game_types']=['R']
        ledger['time_scope']=dict(start=s.get('start',f'{year}-01-01'),end=s.get('end',f'{year}-12-31')) if not s.get('years') else {'seasons':s['years'],'difference':'2025 minus 2022'}
        ledger['qualification']={'basis':s.get('basis','PA'),'minimum':s['minimum']} if s.get('minimum') is not None else None
        ledger['expected_grouping']=([('batter_id' if role=='BATTER' else 'pitcher_id')] if s.get('rank') else [])+s.get('group',[])
        ledger['expected_ordering']=['metric DESC','MLBAM ID ASC'] if s.get('rank') else []
        items.append(dict(id=id, query=q, spec=spec, semantic_ledger=ledger))
    add('batter_scalar', 'Give me Marcus Semien’s doubles total as a batter for the 2025 regular season.',metric='doubles',player=543760)
    add('pitcher_scalar', 'For Logan Webb pitching in the 2024 regular season, report the mean release speed in mph.',year=2024,role='PITCHER',metric='avg_release_speed',player=657277)
    add('recent_pg', 'How many pitches did George Kirby deliver in regular-season games between 2026-08-06 and 2026-08-18, including both endpoints?',year=2026,role='PITCHER',metric='pitches',player=669923,start='2026-08-06',end='2026-08-18')
    add('archive_a', 'Count Joey Votto’s walks as a batter during the 2017 regular season.',year=2017,metric='walks',player=458015)
    add('archive_b', '2019年常规赛，打者 Andrew McCutchen 一共打出多少支二垒安打？',year=2019,metric='doubles',player=457705)
    add('archive_c', 'For Ichiro Suzuki as a batter, count singles in the 2016 regular season.',year=2016,metric='singles',player=400085)
    add('batter_population',f'Across all batters in the 2025 regular season, order the leading {n} by doubles, descending; use ascending batter MLBAM ID for ties.',metric='doubles',rank=n)
    add('pitcher_population',f'把2024常规赛所有投手按三振数从高到低排列，给前{n}名和MLBAM ID；同分按ID升序。',year=2024,role='PITCHER',metric='strikeouts',rank=n)
    add('qualified',f'Which top {n} batters led the 2025 regular season in walks after requiring a minimum of {minimum} PA? Ties: ascending MLBAM ID.',metric='walks',rank=n,minimum=minimum,basis='PA')
    add('split','For Ian Happ as a batter in the 2024 regular season, split plate appearances by opposing pitcher handedness.',year=2024,player=664023,group=['p_throws'])
    add('filter','Count all 2025 regular-season pitches with pitch_type IN (\'FC\', \'ST\') for pitchers.',role='PITCHER',metric='pitches',filters=[['pitch_type','IN',['FC','ST']]])
    add('hand','2025常规赛，打者 Steven Kwan 面对左投手的打席数是多少？',player=680757,filters=[['p_throws','=','L']])
    add('inning','How many walks did Alex Bregman draw as a batter in innings 8 and later in the 2025 regular season?',metric='walks',player=608324,filters=[['inning','>=',8]])
    add('exact_window','Show Pete Alonso’s batter strikeouts in regular-season games from 2025-06-09 through 2025-06-23 inclusive.',metric='strikeouts',player=624413,start='2025-06-09',end='2025-06-23')
    add('rate','Compute Ian Happ’s BB% for the 2024 regular season as a batter: walks divided by plate appearances, expressed as a percentage.',year=2024,metric='bb_pct',player=664023)
    add('comparison','Compare Matt Olson’s doubles as a batter in the 2022 and 2025 regular seasons, and give 2025 minus 2022.',metric='doubles',player=621566,years=[2022,2025],difference=True)
    add('sample_group',f'For all pitchers in the 2024 regular season, group average release speed by pitch type and pitcher ID; require at least {minimum+29} PITCHES per group. Rank top {n} by speed descending, then pitcher ID ascending.',year=2024,role='PITCHER',metric='avg_release_speed',rank=n,minimum=minimum+29,basis='PITCHES',group=['pitch_type'])
    add('ambiguous','What was Smith’s batting line during the 2025 regular season?',negative='ambiguous')
    add('unsupported_metric','Give Seth Lugo’s FIP as a pitcher for the 2025 regular season.',role='PITCHER',metric='fip',player=607625,negative='unsupported_metric')
    add('unsupported_population','How many walks did Marcus Semien draw as a batter in the 2025 regular season in games televised nationally?',metric='walks',player=543760,negative='unsupported_population',unsupported='national television')
    add('compound_two','In the 2025 regular season, count Marcus Semien’s batter plate appearances AND walks, only against right-handed pitchers.',metric=['pa','walks'],player=543760,filters=[['p_throws','=','R']])
    add('compound_many','During the 2024 regular season, show Ian Happ’s batter walks AND strikeouts AND plate appearances, restricted to left-handed pitchers AND inning >= 6.',year=2024,metric=['walks','strikeouts','pa'],player=664023,filters=[['p_throws','=','L'],['inning','>=',6]])
    add('unresolved','Count Marcus Semien’s batter plate appearances against lefties on a getaway afternoon in the 2025 regular season.',player=543760,negative='unresolved',filters=[['p_throws','=','L']],unsupported='getaway afternoon')
    add('nested','For batters in the 2025 regular season, count pitches against lefties with two strikes.',metric='pitches',filters=[['p_throws','=','L'],['strikes','=',2]])
    add('context','Count 2025 regular-season pitches for pitchers in tie games, restricted to inning >= 5.',role='PITCHER',metric='pitches',filters=[['inning','>=',5]],negative='unsupported_population',unsupported='tied score')
    add('mixed','Marcus Semien，2025 regular season 的 BB%（100 × 保送 / 打席）是多少？',metric='bb_pct',player=543760)
    add('qualification_bbe',f'Rank top {n} batters by hard-hit percentage in the 2025 regular season with at least {minimum+11} BBE; order ties by ascending MLBAM ID.',metric='hard_hit_pct',rank=n,minimum=minimum+11,basis='BBE')
    add('qualification_games',f'For 2025 regular-season batters, list top {n} by walks among those appearing in at least 113 GAMES; ties by ascending MLBAM ID.',metric='walks',rank=n,minimum=113,basis='GAMES')
    return items

EXPRESSIONS={
 'pa':'COUNT(events)', 'pitches':'COUNT(*)',
 'doubles':"COUNT(*) FILTER (WHERE events='double')",
 'singles':"COUNT(*) FILTER (WHERE events='single')",
 'walks':"COUNT(*) FILTER (WHERE events IN ('walk','intent_walk'))",
 'strikeouts':"COUNT(*) FILTER (WHERE events IN ('strikeout','strikeout_double_play'))",
 'avg_release_speed':'AVG(release_speed)',
 'bb_pct':"100.0*COUNT(*) FILTER (WHERE events IN ('walk','intent_walk'))/NULLIF(COUNT(events),0)",
 'hard_hit_pct':'100.0*COUNT(*) FILTER (WHERE launch_speed>=95)/NULLIF(COUNT(launch_speed),0)'}

def pg_connection():
    import psycopg2
    from app.config import get_config
    c=get_config().postgres
    return psycopg2.connect(host=c.host,port=c.port,dbname=c.db,user=c.user,password=c.password,
        connect_timeout=6, options='-c default_transaction_read_only=on -c statement_timeout=25000',application_name='m212_independent_reference')

def direct(sql, source):
    if source=='POSTGRES':
        con=pg_connection()
        try:
            with con:
                with con.cursor() as cur:
                    cur.execute(sql); cols=[d.name for d in cur.description]
                    return enc([dict(zip(cols,row)) for row in cur.fetchall()])
        finally: con.close()
    import duckdb
    con=duckdb.connect(':memory:')
    try:
        rel=con.execute(sql); cols=[d[0] for d in rel.description]
        return enc([dict(zip(cols,row)) for row in rel.fetchall()])
    finally: con.close()

def lit(v):
    if v is None:return 'NULL'
    if isinstance(v,(int,float)):return str(v)
    return "'"+str(v).replace("'","''")+"'"

def reference_sql(spec, year):
    from app.config import get_config
    source='POSTGRES' if year>=2024 else 'PARQUET'
    table='public.statcast_pitches' if source=='POSTGRES' else f"read_parquet({lit(str(Path(get_config().parquet.archive_path)/f'mlb_statcast_{year}.parquet'))})"
    idfield=('batter_id' if spec['role']=='BATTER' else 'pitcher_id') if source=='POSTGRES' else ('batter' if spec['role']=='BATTER' else 'pitcher')
    logical='batter_id' if spec['role']=='BATTER' else 'pitcher_id'
    groups=([idfield] if spec.get('rank') else [])+spec.get('group',[])
    metrics=spec['metric'] if isinstance(spec['metric'],list) else [spec['metric']]
    selects=[(idfield+' AS '+logical) if g==idfield else g for g in groups]+[EXPRESSIONS[m]+' AS '+m for m in metrics]
    sql='SELECT '+','.join(selects)+' FROM '+table+f" WHERE game_type='R' AND game_date BETWEEN '{year}-01-01' AND '{year}-12-31'"
    if spec.get('player'):sql+=f' AND {idfield}={int(spec["player"])}'
    for field,op,v in spec.get('filters',[]):
        sql+=' AND '+field+' '+op+' '+('('+','.join(lit(x) for x in v)+')' if op=='IN' else lit(v))
    if spec.get('start'):sql+=f" AND game_date BETWEEN '{spec['start']}' AND '{spec['end']}'"
    if groups:sql+=' GROUP BY '+','.join(groups)
    if spec.get('minimum') is not None:
        basis={'PA':'COUNT(events)','PITCHES':'COUNT(*)','BBE':'COUNT(launch_speed)','GAMES':'COUNT(DISTINCT game_pk)','RESULTS':'COUNT(events)'}[spec.get('basis','PA')]
        sql+=' HAVING '+basis+'>='+str(spec['minimum'])
    if spec.get('rank'):sql+=' ORDER BY '+metrics[0]+' DESC NULLS LAST,'+idfield+' ASC'+(' ,'+','.join(g+' ASC NULLS LAST' for g in groups[1:]) if len(groups)>1 else '')+' LIMIT '+str(spec['rank'])
    elif groups:sql+=' ORDER BY '+','.join(g+' ASC NULLS LAST' for g in groups)
    return source,sql

def references():
    refs=[]
    for t in json.loads((OUT/'corpus.json').read_text()):
        s=t['spec']
        if s.get('negative'):continue
        for year in s.get('years',[s['year']]):
            source,sql=reference_sql(s,year); row=dict(id=t['id'],year=year,source=source,sql=sql,expected_semantics=t['semantic_ledger'])
            try: row['rows']=direct(sql,source)
            except Exception as e: row['error_type']=type(e).__name__
            refs.append(row); save('references.json',refs)
            print('reference',t['id'],year,source,'ok' if 'rows' in row else row['error_type'],flush=True)

def live():
    from app.config import get_config
    from app.runtime.engine import Engine
    import openai
    c=get_config()
    if not c.llm.configured:
        save('live_summary.json',dict(status='NOT EXECUTED',reason='credential absent'));return
    tasks=json.loads((OUT/'corpus.json').read_text())
    repeat={'nested','unresolved','qualified','pitcher_population','compound_many','archive_b'}
    runs=[(t,j) for t in tasks for j in range(2 if t['id'] in repeat else 1)]
    def run(pair):
        t,j=pair; tag=t['id']+'_'+str(j); capture=dict(task=t,run=j,semantic_calls=[],planner=[],binder=[],requests=[],candidates=[],judge=[],provider_errors=[])
        cfg=dataclasses.replace(c,llm=dataclasses.replace(c.llm,timeout_seconds=30),max_rounds=3,runtime_dir=Path('/tmp/m212-independent-live')/(tag+'_'+str(time.time_ns())))
        engine=Engine(cfg)
        engine.llm._client=openai.OpenAI(api_key=c.llm.api_key,base_url=c.llm.base_url,timeout=30,max_retries=0)
        original=engine.llm.complete_json
        def json_call(**kw):
            try:
                result=original(**kw)
                capture['semantic_calls'].append(dict(role=kw['role'],model=result.model,data=enc(result.data),latency_ms=result.latency_ms))
                return result
            except Exception as e:
                capture['provider_errors'].append(dict(role=kw['role'],type=type(e).__name__))
                raise
        engine.llm.complete_json=json_call
        def wrap(obj,name,target,record):
            method=getattr(obj,name)
            def call(*a,**kw):
                entry=record(a,kw);target.append(entry)
                try:
                    result=method(*a,**kw);entry['result']=enc(result);return result
                except Exception as e:entry['error_type']=type(e).__name__;raise
            setattr(obj,name,call)
        wrap(engine.planner,'plan',capture['planner'],lambda a,k:{x:enc(k.get(x)) for x in ['goal','semantic','needs','artifacts','outcomes','round_index','feedback']})
        wrap(engine.binder,'bind',capture['binder'],lambda a,k:{'need':enc(k.get('need') or (a[0] if a else None))})
        wrap(engine.judge,'evaluate',capture['judge'],lambda a,k:dict(goal=enc(a[0]),artifacts=enc(a[1]),needs=enc(a[2])))
        for name in engine.registry.names():
            wrap(engine.registry.get(name),'execute',capture['requests'],lambda a,k:dict(request=enc(a[0])))
        wrap(engine.registry.get('local_analytics'),'_build_ir',capture['candidates'],lambda a,k:dict(intent=enc(a[0]),source=enc(a[2]),feedback=enc(a[3])))
        started=time.monotonic()
        try:capture['turn']=enc(engine.ask('acceptance_'+tag,t['query'],trace_level='off'))
        except Exception as e:capture['error_type']=type(e).__name__
        capture['elapsed']=time.monotonic()-started;capture['usage']=enc(engine.llm.usage_records)
        save('live_'+tag+'.json',capture)
        turn=capture.get('turn',{});status=turn.get('projection',{}).get('goal_status')
        print('live',tag,status or capture.get('error_type'),'calls',len(capture['usage']),flush=True)
        return dict(id=t['id'],run=j,status=status,provider_errors=len(capture['provider_errors']),elapsed=capture['elapsed'])
    with ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(run,runs))
    save('live_summary.json',dict(status='EXECUTED',provider_host=c.llm.base_url.split('//')[-1].split('/')[0],models={k:getattr(c.llm,k+'_model') for k in ['semantic','planner','analytics','response']},runs=results))

def generated_rows():
    r=random.Random(SEEDS['rows']); rows=[]
    for j in range(384):
        rows.append(dict(game_date=dt.date(2025,5,2)+dt.timedelta(days=j%27),game_type=r.choice(['R','R','R','S']),
            batter_id=4700001+j%7,pitcher_id=4800001+j%5,game_pk=970000+j//12,
            events=r.choice([None,None,'walk','intent_walk','single','double','strikeout','strikeout_double_play','field_out']),
            p_throws=r.choice(['L','R',None]),stand=r.choice(['R','L',None]),pitch_type=r.choice(['FF','ST','FC',None]),
            inning=r.randint(1,12),balls=r.randint(0,3),strikes=r.randint(0,2),release_speed=r.choice([None,84.25,91.0,96.75]),
            launch_speed=r.choice([None,87.0,99.5])))
    return rows

def oracle(rows, spec):
    """Separate Python reduction; inputs are reviewer semantics, never Artifacts."""
    selected=[]
    for row in rows:
        if row['game_type']!='R':continue
        if not spec.get('start','2025-01-01')<=str(row['game_date'])<=spec.get('end','2025-12-31'):continue
        if spec.get('entity') and row[spec.get('identity','batter_id')]!=spec['entity']:continue
        ok=True
        for field,op,v in spec.get('filters',[]):
            x=row[field]
            ok &= x is not None and {'=':lambda:x==v,'>=':lambda:x>=v,'IN':lambda:x in v}[op]()
        if ok:selected.append(row)
    groups={}
    for row in selected:groups.setdefault(tuple(row[x] for x in spec.get('group',[])),[]).append(row)
    if not spec.get('group') and not groups:groups[()]=[]
    result=[]
    for key,rs in groups.items():
        q=spec.get('qualification')
        basis={'PA':sum(x['events'] is not None for x in rs),'PITCHES':len(rs),'BBE':sum(x['launch_speed'] is not None for x in rs),'GAMES':len({x['game_pk'] for x in rs}),'RESULTS':sum(x['events'] is not None for x in rs)}
        if q and basis[q['basis']]<q['minimum']:continue
        vals=dict(pa=basis['PA'],pitches=len(rs),walks=sum(x['events'] in {'walk','intent_walk'} for x in rs),strikeouts=sum(x['events'] in {'strikeout','strikeout_double_play'} for x in rs))
        vals['bb_pct']=100*vals['walks']/vals['pa'] if vals['pa'] else None
        speeds=[x['release_speed'] for x in rs if x['release_speed'] is not None]
        vals['avg_release_speed']=sum(speeds)/len(speeds) if speeds else None
        result.append({**dict(zip(spec.get('group',[]),key)),**{m:vals[m] for m in spec.get('metrics',['pa'])}})
    for name,desc in reversed(spec.get('order',[])):
        result=sorted([x for x in result if x[name] is not None],key=lambda x:x[name],reverse=desc)+[x for x in result if x[name] is None]
    return result[:spec['limit']] if spec.get('limit') else result

def fixed_case(role='BATTER', metric='pa', **spec):
    from app.contracts.models import UserRequirement,Goal
    s=dict(role=role,metrics=[metric],**spec)
    label={'pa':'plate appearances','pitches':'pitches','walks':'walks','strikeouts':'strikeouts','bb_pct':'BB%','avg_release_speed':'average release speed'}[metric]
    rawtext='Report '+('batter ' if role=='BATTER' else 'pitcher ')+label
    reqs=[dict(id='season',kind='SEASON',value=2025,statement='2025',source='user'),
          dict(id='population',kind='GAME_POPULATION',value='REGULAR_SEASON',statement='regular season',source='user'),
          dict(id='measure',kind='MEASURE',value={'key':metric,'derivable':True},statement=label,source='user'),
          dict(id='analysis',kind='ANALYSIS',value={'objective':rawtext},statement=rawtext,source='user')]
    dimensions={'analytical_role':role}
    if s.get('filters'):dimensions['filters']=[{'op':'IN','field':f,'values':v} if op=='IN' else {'op':'COMPARE','field':f,'comparator':op,'value':v} for f,op,v in s['filters']]
    if s.get('group'):dimensions['group_by']=s['group']
    if s.get('qualification'):dimensions['qualification']=s['qualification']
    if s.get('limit'):dimensions['limit']=s['limit']
    if s.get('order'):dimensions['ordering']=[{'metric':f,'direction':'DESC' if desc else 'ASC'} for f,desc in s['order']]
    reqs.append(dict(id='dimensions',kind='CONSTRAINT',value=dimensions,statement='User analytical dimensions',source='user'))
    if s.get('entity'):
        reqs.append(dict(id='entity',kind='ENTITY',value={'mention':'Acceptance Player','canonical_id':s['entity'],'identity_verified':True,'role':role},statement='Acceptance Player',source='user'))
    if s.get('start'):
        reqs.append(dict(id='dates',kind='TIME',value={'start':s['start'],'end':s['end']},statement='Exact dates',source='user'))
    for req in reqs:req['required']=True;req['original_intent']=rawtext
    aggregates={'pa':dict(name='pa',function='COUNT_NON_NULL',field='events'),
        'pitches':dict(name='pitches',function='COUNT',field='*'),
        'walks':dict(name='walks',function='COUNT_IF',condition=dict(op='IN',field='events',values=['walk','intent_walk'])),
        'strikeouts':dict(name='strikeouts',function='COUNT_IF',condition=dict(op='IN',field='events',values=['strikeout','strikeout_double_play'])),
        'avg_release_speed':dict(name='avg_release_speed',function='AVG',field='release_speed')}
    raw=dict(relation='pitch',analytical_role=role,season=2025,population='REGULAR_SEASON',filters=copy.deepcopy(dimensions.get('filters',[])),group_by=s.get('group',[]),aggregates=[aggregates[metric]] if metric!='bb_pct' else [aggregates['walks'],aggregates['pa']],derived=[],sort=[{'name':f,'direction':'DESC' if d else 'ASC'} for f,d in s.get('order',[])],limit=s.get('limit'),qualification=s.get('qualification'))
    if metric=='bb_pct':raw['derived']=[dict(name='bb_pct',op='PCT',left={'kind':'aggregate','name':'walks'},right={'kind':'aggregate','name':'pa'})]
    if s.get('entity'):raw['entity_filters']=[dict(role=role,ids=[s['entity']])]
    if s.get('start'):raw['date_range']={'start':s['start'],'end':s['end']}
    inputs=dict(analytical_requirements=reqs,raw_query=rawtext,bound_user_intent=rawtext,entity_role=role,requested_measures=[{'key':metric}],entity_sets=[dict(role=role,ids=[s['entity']])] if s.get('entity') else [],intent={},constraints={})
    # Candidate mutations must never mutate the separately authored requirements.
    return dict(spec=copy.deepcopy(s),raw=copy.deepcopy(raw),inputs=copy.deepcopy(inputs))

class CandidateProvider:
    configured=True
    def __init__(self,raw):self.raw=raw
    def complete_json(self,**kw):
        from types import SimpleNamespace
        return SimpleNamespace(data=copy.deepcopy(self.raw))

class FixtureExecutor:
    def __init__(self,table,rows,availability=None):self.table=table;self.rows=rows;self.avail=availability
    def run(self,sql,source):
        from app.capabilities.analytics.executor import QueryResult
        if 'player_dictionary' in sql:return QueryResult([],[],0,source,0,'fixture-names')
        import duckdb
        con=duckdb.connect(':memory:')
        try:
            result=con.execute(sql);cols=[dict(name=x[0],type=str(x[1])) for x in result.description]
            rows=enc([dict(zip([x['name'] for x in cols],row)) for row in result.fetchall()])
            return QueryResult(cols,rows,len(rows),source,0,hashlib.sha256(sql.encode()).hexdigest())
        finally:con.close()
    def availability(self,source,relation):
        from app.capabilities.analytics.conformance import SourceAvailability
        return self.avail or SourceAvailability(source=source,relation=relation,known=True,start=dt.date(2025,1,1),end=dt.date(2025,12,31),seasons=(2025,),note='reviewer generated fixture; not real-source completeness')

def fixture(rows):
    from app.capabilities.analytics.catalog import CATALOG,SchemaCatalog
    columns=list(rows[0]);values=','.join('('+','.join(('DATE '+lit(v)) if isinstance(v,dt.date) else lit(v) for v in row.values())+')' for row in rows)
    table='(VALUES '+values+') AS independent_fixture('+','.join(columns)+')'
    spec=dataclasses.replace(CATALOG.table('pitch'),physical={'POSTGRES':table,'PARQUET':table},key_map={'POSTGRES':CATALOG.table('pitch').key_map['POSTGRES'],'PARQUET':CATALOG.table('pitch').key_map['POSTGRES']})
    return SchemaCatalog({'pitch':spec}),FixtureExecutor(table,rows)

def build_contract(c):
    from app.capabilities.analytics.tool import LocalAnalyticsTool
    from app.capabilities.analytics.conformance import requested_contract
    from app.contracts.models import ToolRequest
    request=ToolRequest(need_id='acceptance',capability='local_analytics',objective='Report metrics',inputs=c['inputs'])
    return requested_contract(LocalAnalyticsTool(None,None)._intent(request),c['inputs'],[])

def exercise(c,catalog,ex,current=None):
    from app.capabilities.analytics.tool import LocalAnalyticsTool
    from app.capabilities.analytics.compiler import SQLCompiler
    from app.contracts.models import ToolRequest,UserRequirement,Goal,PlannerNeed,Artifact
    from app.verification.judge import Judge
    request=ToolRequest(need_id='acceptance',capability='local_analytics',objective='Report metrics',inputs=copy.deepcopy(c['inputs']))
    archive=getattr(getattr(ex,'duckdb',None),'archive_path','')
    tool=LocalAnalyticsTool(ex,CandidateProvider(c['raw']),parquet_archive=archive);tool.catalog=catalog;tool.compiler=SQLCompiler(catalog,parquet_archive=archive)
    result=tool.execute(request);goalcase=current or c
    goal=Goal(raw_query=goalcase['inputs']['bound_user_intent'],statement='Report metrics',requirements=[UserRequirement(**r) for r in goalcase['inputs']['analytical_requirements']])
    need=PlannerNeed(id='acceptance',objective='Report metrics',capability='local_analytics',requires=[r.id for r in goal.requirements])
    arts=[result.artifact] if result.artifact else []
    entity=goalcase['spec'].get('entity')
    if entity:arts.append(Artifact(kind='ENTITY_RESOLUTION',structured_data={'resolved':[{'mention':'Acceptance Player','id':entity,'name':'Acceptance Player'}],'identity':{'Acceptance Player':{'id':entity,'level':'CANONICAL'}}}))
    projection=Judge().evaluate(goal,arts,[need])
    return dict(request=enc(request),candidate=c['raw'],goal=enc(goal),need=enc(need),result=enc(result),judge=enc(projection),rows=result.artifact.structured_data.get('rows',[]) if result.artifact else [],contract=enc(build_contract(c)))

def same_rows(a,b,ordered=True):
    if len(a)!=len(b):return False
    if not ordered and b:
        keys=list(b[0]);key=lambda x:repr([x.get(k) for k in keys])
        a=sorted(a,key=key);b=sorted(b,key=key)
    for x,y in zip(a,b):
        for k,v in y.items():
            z=x.get(k)
            if isinstance(v,(int,float)) and not isinstance(v,bool):
                if z is None or not math.isclose(float(z),v,rel_tol=1e-8,abs_tol=1e-8):return False
            elif z!=v:return False
    return True

def deterministic():
    from app.capabilities.analytics.authority import resolve_authority
    from app.capabilities.analytics.conformance import constraint_coverage,SourceAvailability
    rows=generated_rows();save('mirrored_rows.json',rows);catalog,ex=fixture(rows);records=[]
    def record(id,family,c,expected,unsafe=False,current=None,loss=0,authority=0,allow_complete=False):
        try:
            trace=exercise(c,catalog,ex,current=current);complete=trace['judge']['goal_status']=='COMPLETE'
            correct=same_rows(trace['rows'],expected,ordered=bool(c['spec'].get('order')))
            wrong=complete and ((unsafe and not allow_complete) or not correct)
            row=dict(id=id,family=family,attack=unsafe,independent_semantic_ledger=enc((current or c)['spec']),expected=expected,incorrect_complete=wrong,silent_losses=loss,unproved_authority=authority,trace=trace)
            row['numerical_correct']=correct
        except Exception as e:row=dict(id=id,family=family,attack=unsafe,error_type=type(e).__name__,incorrect_complete=False,silent_losses=loss,unproved_authority=authority)
        records.append(row);return row
    base=fixed_case();expected=oracle(rows,base['spec']);record('base','control',base,expected)
    # New authority graph attacks. Proof meaning is independently checked, not label-based.
    for name,extra,valid in [
        ('no_parent',{},False),('ghost',{'parent_requirement_ids':['missing']},False),
        ('unrelated_parent',{'parent_requirement_ids':['measure'],'derivation_kind':'schema_field_binding','derivation_proof':'Trust me: restrict to inning 11'},False),
        ('valid_parent_changed',{'parent_requirement_ids':['analysis'],'derivation_kind':'interpreter_requirement_binding','derivation_proof':'planner chooses inning 11'},False),
        ('forged_status',{'authority_status':'AUTHORITATIVE','derivation_proof':'claimed verified'},False),
        ('default_payload',{'derivation_kind':'domain_default','derivation_proof':'full_eligible_row_population'},False),
        ('multi_parent',{'parent_requirement_ids':['analysis','missing'],'derivation_kind':'schema_field_binding','derivation_proof':'p'},False),
        ('valid_derivation',{'parent_requirement_ids':['dimensions'],'derivation_kind':'schema_field_binding','derivation_proof':'same authoritative BATTER role'},True)]:
        c=copy.deepcopy(base)
        value={'analytical_role':'BATTER'} if valid else {'filters':[dict(op='COMPARE',field='inning',comparator='=',value=11)]}
        req=dict(id='derived',kind='CONSTRAINT',source='inferred',statement='Derived semantic proposal',value=value,required=True,**extra)
        c['inputs']['analytical_requirements'].append(req)
        auth,unproven,issues=resolve_authority(c['inputs']['analytical_requirements']);admitted=any(x['id']=='derived' for x in auth)
        if not valid:c['raw']['filters']=value['filters']
        record('authority_'+name,'authority',c,expected,unsafe=not valid,authority=int(admitted and not valid))
    for source in ['planner','need','tool']:
        c=copy.deepcopy(base);req=dict(id='origin_'+source,kind='CONSTRAINT',source=source,statement='Choose a subset',value={'limit':2},required=True)
        c['inputs']['analytical_requirements'].append(req)
        auth,unp,issues=resolve_authority(c['inputs']['analytical_requirements'])
        # Origin labels are rejected by typed UserRequirement too; contract is tested directly.
        records.append(dict(id='authority_'+source,family='authority',attack=True,contract=enc(build_contract(c)),authority=enc(auth),unproven=enc(unp),incorrect_complete=False,silent_losses=0,unproved_authority=int(any(x['id']==req['id'] for x in auth))))
    c=copy.deepcopy(base)
    for id,parent in [('cycle_a','cycle_b'),('cycle_b','cycle_a')]:
        c['inputs']['analytical_requirements'].append(dict(id=id,kind='CONSTRAINT',source='inferred',statement='cycle',value={'filters':[]},required=True,parent_requirement_ids=[parent],derivation_kind='schema_field_binding',derivation_proof='p'))
    record('authority_cycle','authority',c,expected,unsafe=True)
    # Independent residue ledgers include a recognized synonym plus an unrecognized modifier.
    clauses=[
        ('against lefties with two strikes',[['p_throws','=','L'],['strikes','=',2]]),
        ('against righties when balls are 3',[['p_throws','=','R'],['balls','=',3]]),
        ('against lefties on national television',None),
        ('against righties in tied games',None),
        ('against lefties split by count state',None),
        ('against righties after May 19',None),
        ('against lefties with a minimum sample of 47 plate appearances',None),
        ('against lefties under a closed roof',None),
        ('in inning 11',[['inning','=',11]]),
        ('strikes = 2 AND p_throws IN (\'L\')',[['strikes','=',2],['p_throws','=','L']]),
        ('for only L batters',[['stand','=','L']]),
        ('in a 3-0 count',[['balls','=',3],['strikes','=',0]]),
        ('with the score level',None),('under lights',None),('on artificial turf',None)]
    for idx,(clause,wanted) in enumerate(clauses):
        for placement in ['source','analysis','constraint']:
            c=copy.deepcopy(base)
            if placement=='source':c['inputs']['bound_user_intent']+=' '+clause
            elif placement=='analysis':c['inputs']['analytical_requirements'][3]['value']['objective']+=' '+clause
            else:c['inputs']['analytical_requirements'].append(dict(id='modifier',kind='CONSTRAINT',source='user',statement=clause,value=clause,required=True,original_intent=clause))
            cov=constraint_coverage(clause,'BATTER','plate appearances',analysis=placement=='analysis')
            ct=build_contract(c);c['raw']['filters']=list(ct.filters)
            intended=dict(base['spec'],filters=wanted or [])
            if idx==4:intended.update(filters=[['p_throws','=','L']],group=['balls','strikes'])
            if idx==5:intended.update(filters=[['p_throws','=','R']],start='2025-05-20',end='2025-12-31')
            if idx==6:intended.update(filters=[['p_throws','=','L']],qualification={'basis':'PA','minimum':47})
            exp=oracle(rows,intended) if wanted or idx in {4,5,6} else []
            loss=0
            if not ct.issues:
                if wanted:
                    actual={(p.get('field'),p.get('comparator'),p.get('value')) for p in ct.filters}
                    loss=sum((f,op,v) not in actual for f,op,v in wanted)
                    # Singleton IN is an allowed structural equivalent for L restriction.
                    for p in ct.filters:
                        if p.get('op')=='IN' and len(p.get('values',[]))==1:
                            if [p['field'],'=',p['values'][0]] in wanted:loss=max(0,loss-1)
                else:loss=1
            result=record(f'residue_{idx}_{placement}','semantic_conservation',c,exp,unsafe=True,loss=loss,allow_complete=bool(wanted) and loss==0)
            result['independent_semantic_ledger']=dict(intended,original_clause=clause,unavailable_dimension=clause if wanted is None and idx not in {4,5,6} else None)
    # Direct Clause deletion and relabeling attacks on actual source wording.
    full='at least 43 PITCHES AND strikes = 1 AND group by p_throws'
    for variant in ['delete_first','delete_middle','delete_last','relabel_analysis','nonanalytical','partial','broaden','words_no_structure']:
        import app.capabilities.analytics.conformance as module
        c=copy.deepcopy(base);c['inputs']['bound_user_intent']+=' AND '+full
        text=full
        if variant.startswith('delete_'):
            index={'delete_first':0,'delete_middle':1,'delete_last':2}[variant];text=' AND '.join(x for i,x in enumerate(full.split(' AND ')) if i!=index)
        elif variant=='broaden':text=full.replace('strikes = 1','strikes >= 0')
        c['inputs']['analytical_requirements'].append(dict(id='compound',kind='ANALYSIS' if variant=='relabel_analysis' else 'CONSTRAINT',value={'objective':text} if variant=='relabel_analysis' else text,statement=text,required=True,source='user'))
        original=module.constraint_coverage
        def corrupt(v,*a,**kw):
            cov=copy.deepcopy(original(v,*a,**kw))
            if v==full and variant in {'nonanalytical','partial','words_no_structure'}:
                if variant=='nonanalytical':
                    for x in cov['recognized']:
                        if x.get('clause_index')==1:x['obligations']={'analysis':'presentation'}
                elif variant=='partial':cov['recognized']=[x for x in cov['recognized'] if x.get('clause_index')!=1]
                else:
                    cov['recognized']=[dict(clause=full,clause_index=0,obligations={'analysis':'understood words'})];cov['obligations']={}
            return cov
        try:
            module.constraint_coverage=corrupt
            ct=build_contract(c);c['raw'].update(filters=list(ct.filters),group_by=list(ct.group_by),qualification=ct.qualification)
            record('clause_'+variant,'clause_loss',c,oracle(rows,dict(base['spec'],filters=[['strikes','=',1]],group=['p_throws'],qualification={'basis':'PITCHES','minimum':43})),unsafe=True)
        finally:module.constraint_coverage=original
    # Planner/Need inputs must not redefine anything, even with a matching wrong IR.
    hints={
        'filters':{'filters':[dict(op='COMPARE',field='balls',comparator='=',value=3)]},
        'group':{'group_by':['stand']},'limit':{'group_by':['batter_id'],'limit':2},
        'qualification':{'qualification':{'basis':'PITCHES','minimum':311}},
        'role':{'analytical_role':'PITCHER'},'date':{'date_range':{'start':'2025-05-11','end':'2025-05-27'}},
        'population':{'population':'SPRING_TRAINING'},'order':{'group_by':['batter_id'],'ordering':[{'metric':'pa','direction':'ASC'}]}}
    for name,hint in hints.items():
        c=copy.deepcopy(base);c['inputs']['constraints']=copy.deepcopy(hint);c['inputs']['intent']=copy.deepcopy(hint)
        c['raw'].update({k:v for k,v in hint.items() if k!='ordering'})
        if 'ordering' in hint:c['raw']['sort']=[dict(name='pa',direction='ASC')]
        record('need_'+name,'need_laundering',c,expected,unsafe=True)
    # Self-consistent wrong producer contracts confronted with correct independent Goal.
    for name,change in [('filters',dict(filters=[['balls','=',3]])),('group',dict(group=['stand'])),('qualification',dict(qualification={'basis':'PITCHES','minimum':311})),('role',dict(role='PITCHER')),('dates',dict(start='2025-05-11',end='2025-05-27')),('topn',dict(group=['batter_id'],limit=2,order=[('pa',True),('batter_id',False)]))]:
        c=fixed_case(**change);record('self_'+name,'self_certification',c,expected,unsafe=True,current=base)
    # Execution-side protections; controls cover independently varied shapes.
    controls=[fixed_case(),fixed_case(role='PITCHER',metric='pitches'),fixed_case(entity=4700003),
        fixed_case(filters=[['strikes','=',1]]),fixed_case(group=['p_throws']),
        fixed_case(group=['batter_id'],qualification={'basis':'PA','minimum':17},limit=3,order=[('pa',True),('batter_id',False)]),
        fixed_case(metric='bb_pct'),fixed_case(start='2025-05-08',end='2025-05-21'),fixed_case(group=['pitcher_id'],role='PITCHER',metric='avg_release_speed',limit=3,order=[('avg_release_speed',True),('pitcher_id',False)])]
    for i,c in enumerate(controls):record('control_'+str(i),'control',c,oracle(rows,c['spec']))
    mutations=[('wrong_entity',2,lambda q:q['entity_filters'][0].update(ids=[4700004])),
        ('missing_entity',2,lambda q:q.update(entity_filters=[])),('wrong_role',2,lambda q:q.update(analytical_role='PITCHER')),
        ('population_role_swap',5,lambda q:q.update(analytical_role='PITCHER',group_by=['pitcher_id'],sort=[dict(name='pa',direction='DESC'),dict(name='pitcher_id',direction='ASC')])),
        ('unexpected_filter',0,lambda q:q.update(filters=[dict(op='COMPARE',field='balls',comparator='=',value=3)])),
        ('missing_filter',3,lambda q:q.update(filters=[])),('qualification_omission',5,lambda q:q.update(qualification=None)),
        ('qualification_threshold',5,lambda q:q['qualification'].update(minimum=19)),('qualification_basis',5,lambda q:q['qualification'].update(basis='PITCHES')),
        ('date_drift',7,lambda q:q['date_range'].update(start='2025-05-09')),
        ('competition_drift',0,lambda q:q.update(population='SPRING_TRAINING')),
        ('measure_alias',0,lambda q:q.update(aggregates=[dict(name='pa',function='COUNT',field='*')])),
        ('derived_base',6,lambda q:q['aggregates'][0].update(condition=dict(op='COMPARE',field='events',comparator='=',value='walk'))),
        ('grouping_drift',4,lambda q:q.update(group_by=['stand'])),
        ('ranking_drift',8,lambda q:q.update(sort=[dict(name='pitcher_id',direction='ASC')])),
        ('topn_drift',5,lambda q:q.update(limit=4))]
    for name,i,mutate in mutations:
        c=copy.deepcopy(controls[i]);mutate(c['raw']);record('execution_'+name,'execution_drift',c,oracle(rows,controls[i]['spec']),unsafe=True)
    for name,avail in [('unavailable',SourceAvailability(source='POSTGRES',relation='pitch',known=False)),('partial',SourceAvailability(source='POSTGRES',relation='pitch',known=True,start=dt.date(2025,5,1),end=dt.date(2025,5,14)))]:
        ex.avail=avail;record('source_'+name,'execution_drift',base,expected,unsafe=True)
    ex.avail=None
    # Positive semantic equivalents; reviewer defines equivalence before execution.
    eq=fixed_case(filters=[['strikes','=',1],['p_throws','=','R']])
    eqmut=[('singleton',lambda q:q.update(filters=[dict(op='IN',field='strikes',values=[1]),dict(op='IN',field='p_throws',values=['R'])])),
        ('reordered',lambda q:q['filters'].reverse()),('integral',lambda q:q['filters'][0].update(value=1.0)),
        ('defaults',lambda q:q['filters'][0].update(values=None,low=None,high=None,conditions=None))]
    for name,mutation in eqmut:
        c=copy.deepcopy(eq);mutation(c['raw']);record('equivalent_'+name,'equivalence',c,oracle(rows,eq['spec']))
    c=fixed_case(filters=[['strikes','IN',[0,2]]]);c['raw']['filters']=[dict(op='OR',conditions=[dict(op='COMPARE',field='strikes',comparator='=',value=v) for v in [2,0]])]
    record('equivalent_or','equivalence',c,oracle(rows,c['spec']))
    c=copy.deepcopy(controls[2]);c['raw']['entity_filters'][0]['ids']=['4700003'];record('equivalent_id','equivalence',c,oracle(rows,c['spec']))
    c=copy.deepcopy(controls[7]);c['raw'].pop('date_range');c['raw']['filters']=[dict(op='BETWEEN',field='game_date',low='2025-05-08',high='2025-05-21')];record('equivalent_dates','equivalence',c,oracle(rows,c['spec']))
    # Finite qualification parser forms and matched/mismatched candidates, fresh bases.
    for basis,threshold in [('PA',19),('BBE',11),('PITCHES',31),('GAMES',8),('RESULTS',17)]:
        c=fixed_case(group=['batter_id'],qualification={'basis':basis,'minimum':threshold})
        for form in ['structured','english','chinese']:
            cc=copy.deepcopy(c)
            if form!='structured':
                cc['inputs']['analytical_requirements'][-1]['value'].pop('qualification')
                text=f'minimum {threshold} {basis}' if form=='english' else f'至少{threshold}个'+('打席' if basis=='PA' else basis)
                cc['inputs']['analytical_requirements'].append(dict(id='qual',kind='CONSTRAINT',value=text,statement=text,source='user',required=True))
            record(f'qual_{basis}_{form}','qualification_control',cc,oracle(rows,c['spec']))
    save('deterministic.json',records)
    print('deterministic',len(records),'attacks',sum(x['attack'] for x in records),'incorrect_COMPLETE',sum(x['incorrect_complete'] for x in records),'silent_losses',sum(x.get('silent_losses',0) for x in records),'unproved_authority',sum(x.get('unproved_authority',0) for x in records),flush=True)

def parity():
    import duckdb
    from app.capabilities.analytics.compiler import SQLCompiler
    from app.capabilities.analytics.ir import AnalyticalQuery,validate_query
    from app.capabilities.analytics.catalog import CATALOG,SchemaCatalog
    from app.capabilities.analytics.conformance import attest,compare,SourceAvailability
    rows=generated_rows();catalog,fixture_ex=fixture(rows)
    file=OUT/'independent_mirror_1052026283.parquet'
    db=duckdb.connect(':memory:')
    db.execute('CREATE TABLE mirror AS SELECT game_date, game_type, batter_id AS batter, pitcher_id AS pitcher, game_pk, events, p_throws, stand, pitch_type, inning, balls, strikes, release_speed, launch_speed FROM '+fixture_ex.table)
    db.execute('COPY mirror TO '+lit(str(file.resolve()))+' (FORMAT PARQUET)');db.close()
    spec=dataclasses.replace(CATALOG.table('pitch'),physical={'POSTGRES':fixture_ex.table,'PARQUET':'read_parquet('+lit(str(file.resolve()))+')'})
    compiler=SQLCompiler(SchemaCatalog({'pitch':spec}))
    cases=[('scalar',fixed_case()),('group_nullable',fixed_case(group=['p_throws'])),
        ('rate',fixed_case(metric='bb_pct')),('qualified',fixed_case(group=['batter_id'],qualification={'basis':'PA','minimum':23})),
        ('rank',fixed_case(group=['batter_id'],order=[('pa',True),('batter_id',False)])),
        ('topn',fixed_case(group=['batter_id'],limit=4,order=[('pa',True),('batter_id',False)])),
        ('dates',fixed_case(start='2025-05-09',end='2025-05-22')),
        ('row_population',fixed_case(filters=[['strikes','IN',[1,2]],['p_throws','=','L']])),
        ('pitchers',fixed_case(role='PITCHER',metric='pitches',group=['pitcher_id'],limit=3,order=[('pitches',True),('pitcher_id',False)])),
        ('games',fixed_case(group=['batter_id'],qualification={'basis':'GAMES','minimum':9}))]
    results=[]
    for name,c in cases:
        expected=oracle(rows,c['spec']);query=validate_query(AnalyticalQuery.model_validate(c['raw']));contract=build_contract(c)
        record=dict(id=name,expected=expected,engines={})
        for source in ['POSTGRES','PARQUET']:
            compiled=compiler.compile(query,source)
            try:
                actual=direct(compiled.sql,source)
                av=SourceAvailability(source=source,relation='pitch',known=True,seasons=(2025,),note='generated mirror')
                receipt=attest(query,compiled,availability=av,executed=True)
                record['engines'][source]=dict(sql=compiled.sql,rows=actual,correct=same_rows(actual,expected,ordered=bool(c['spec'].get('order'))),conformance=enc(compare(contract,receipt)))
            except Exception as e:record['engines'][source]=dict(sql=compiled.sql,error_type=type(e).__name__)
        record['parity']=same_rows(record['engines']['POSTGRES'].get('rows',[]),record['engines']['PARQUET'].get('rows',[]),ordered=bool(c['spec'].get('order')))
        results.append(record);save('parity.json',results);print('parity',name,record['parity'],flush=True)

def lifecycle():
    """Actual Engine replan with deterministic adversarial Planner responses."""
    from app.config import get_config
    from app.runtime.engine import Engine
    from app.runtime.context import ExecutionContext
    from app.observe.trace import Trace
    from app.contracts.models import Goal,UserRequirement,PlannerNeed,SemanticInterpretation,AnalysisRequest
    from app.capabilities.analytics.conformance import constraint_coverage
    from app.capabilities.analytics.tool import LocalAnalyticsTool
    from app.capabilities.analytics.compiler import SQLCompiler
    from app.verification.judge import Judge
    rows=generated_rows();catalog,ex=fixture(rows);results=[]
    cfg=dataclasses.replace(get_config(),llm=dataclasses.replace(get_config().llm,api_key=''),max_rounds=3,runtime_dir=Path('/tmp/m212-independent-lifecycle'))
    for name,clause in [('A_synonym','against lefties'),('B_unsupported','in televised games'),('C_unresolved','when the score is level')]:
        c=fixed_case();c['inputs']['bound_user_intent']+=' '+clause
        c['inputs']['analytical_requirements'].append(dict(id='original_clause',kind='CONSTRAINT',source='user',value=clause,statement=clause,original_intent=clause,required=True))
        if name=='A_synonym':c['raw']['filters']=[dict(op='COMPARE',field='p_throws',comparator='=',value='L')]
        engine=Engine(cfg);tool=LocalAnalyticsTool(ex,CandidateProvider(c['raw']));tool.catalog=catalog;tool.compiler=SQLCompiler(catalog);engine.registry.register(tool)
        goal=Goal(raw_query=c['inputs']['bound_user_intent'],statement='Report metrics',requirements=[UserRequirement(**r) for r in c['inputs']['analytical_requirements']])
        semantic=SemanticInterpretation(raw_query=goal.raw_query,understanding=goal.raw_query,analysis_requests=[AnalysisRequest(text=goal.raw_query,objective=goal.raw_query)])
        plans=[];contracts=[]
        def plan(**kw):
            plans.append({k:enc(kw.get(k)) for k in ['goal','round_index','outcomes','feedback','needs']})
            return [PlannerNeed(id='retry'+str(kw['round_index']),capability='local_analytics',objective='Report batter plate appearances',requires=[r.id for r in goal.requirements],constraints={'season':2025,'game_population':'REGULAR_SEASON','ignore_unresolved':True})], 'bounded weakening attempt',[]
        engine.planner.plan=plan
        original_exec=tool.execute
        def execute(req,ctx=None):
            from app.capabilities.analytics.conformance import requested_contract
            ct=requested_contract(tool._intent(req),req.inputs,[]);contracts.append(enc(ct));return original_exec(req,ctx)
        tool.execute=execute
        ctx=ExecutionContext(goal=goal,conversation=engine.conversation.get('lifecycle_'+name),config=cfg,llm=engine.llm,clock=engine.clock,trace=Trace('off'))
        needs,outcomes,artifacts,rejected,notes=engine._run_plan(ctx,semantic,Trace('off'))
        projection=Judge().evaluate(goal,artifacts,needs)
        attempt_keys=[[(a['resolver'],a['input']) for u in ct['unresolved_obligations'] for a in u['attempts']] for ct in contracts]
        results.append(dict(id=name,original_text=clause,original_provenance='user/original_clause',requirements=enc(goal.requirements),planner=plans,contracts=contracts,outcomes=enc(outcomes),artifacts=enc(artifacts),judge=enc(projection),repeated_attempt_keys=attempt_keys,identical_across_rounds=len(attempt_keys)>1 and attempt_keys[0]==attempt_keys[1]))
    # Shared unsupported clause should not remove evidence for architecturally independent A.
    a=fixed_case(metric='walks');trace=exercise(a,catalog,ex)
    parent=copy.deepcopy(a);text='Report batter pitches on artificial turf'
    parent['inputs']['analytical_requirements'].append(dict(id='independent_b',kind='ANALYSIS',source='user',value={'objective':text},statement=text,original_intent=text,required=True))
    from app.contracts.models import Artifact
    goal=Goal(raw_query='Report batter walks AND '+text,statement='Independent questions',requirements=[UserRequirement(**r) for r in parent['inputs']['analytical_requirements']])
    need=PlannerNeed(id='acceptance',capability='local_analytics',objective='Report batter walks',requires=['analysis','measure'])
    artifact=Artifact.model_validate(trace['result']['artifact'])
    judge=Judge().evaluate(goal,[artifact],[need])
    results.append(dict(id='partial_independent',A_trace=trace,parent_goal=enc(goal),judge=enc(judge),retained_A_claims=len(judge.claims)))
    # Finite same-call resolver outcomes, distinguished from persisted lifecycle states.
    for text in ['against lefties','in night games','when the score is level']:
        cov=constraint_coverage(text,'BATTER','pa')
        results.append(dict(id='resolver_'+text,coverage=cov))
    save('lifecycle.json',results);print('lifecycle',[(x['id'],x.get('judge',{}).get('goal_status')) for x in results],flush=True)

def supplemental():
    """Minimized failures and real-source revalidation; no production repairs."""
    from app.config import get_config
    from app.capabilities.analytics.tool import LocalAnalyticsTool
    from app.capabilities.analytics.executor import AnalyticalExecutor
    from app.contracts.models import Artifact,Goal,UserRequirement,PlannerNeed
    from app.response.composer import ResponseComposer
    from app.capabilities.analytics.conformance import constraint_coverage
    rows=generated_rows();catalog,ex=fixture(rows);items=[]
    # Smallest numerical witness: all four rows are otherwise identical eligible pitches.
    small=[dict(rows[0],game_type='R',events='walk',p_throws='L',strikes=0,inning=1),
           dict(rows[0],game_type='R',events='walk',p_throws='L',strikes=2,inning=11),
           dict(rows[0],game_type='R',events='walk',p_throws='R',strikes=2,inning=1),
           dict(rows[0],game_type='R',events='walk',p_throws='R',strikes=0,inning=1)]
    smallcat,smallex=fixture(small)
    for repeat in range(2):
        c=fixed_case();clause='against lefties with two strikes';c['inputs']['bound_user_intent']+=' '+clause
        c['raw']['filters']=[dict(op='COMPARE',field='p_throws',comparator='=',value='L')]
        trace=exercise(c,smallcat,smallex);expected=oracle(small,dict(c['spec'],filters=[['p_throws','=','L'],['strikes','=',2]]))
        items.append(dict(id='minimal_residue_'+str(repeat),attack=True,family='semantic_conservation',rows=small,expected=expected,trace=trace,incorrect_complete=trace['judge']['goal_status']=='COMPLETE' and not same_rows(trace['rows'],expected),silent_losses=1,unproved_authority=0))
    for repeat in range(2):
        c=fixed_case();c['inputs']['analytical_requirements'].append(dict(id='forged',kind='CONSTRAINT',source='inferred',required=True,statement='Unasked inning restriction',value={'filters':[dict(op='COMPARE',field='inning',comparator='=',value=11)]},parent_requirement_ids=['measure'],derivation_kind='schema_field_binding',derivation_proof='this nonempty string is not a derivation'))
        c['raw']['filters']=[dict(op='COMPARE',field='inning',comparator='=',value=11)]
        trace=exercise(c,smallcat,smallex);expected=oracle(small,c['spec'])
        items.append(dict(id='minimal_authority_'+str(repeat),attack=True,family='authority',rows=small,expected=expected,trace=trace,incorrect_complete=trace['judge']['goal_status']=='COMPLETE' and not same_rows(trace['rows'],expected),silent_losses=0,unproved_authority=1))
    # Existing unproven second parent, rather than only a missing parent.
    c=fixed_case();c['inputs']['analytical_requirements'] += [
        dict(id='unproven_parent',kind='CONSTRAINT',source='inferred',required=True,statement='unproved',value={'limit':2}),
        dict(id='child',kind='CONSTRAINT',source='inferred',required=True,statement='derived',value={'filters':[]},parent_requirement_ids=['analysis','unproven_parent'],derivation_kind='schema_field_binding',derivation_proof='p')]
    trace=exercise(c,catalog,ex);items.append(dict(id='multi_existing_unproven',attack=True,family='authority',trace=trace,expected=oracle(rows,c['spec']),incorrect_complete=trace['judge']['goal_status']=='COMPLETE',silent_losses=0,unproved_authority=0))
    # Relevance unknown: short Chinese/ASCII terms must not be discarded merely by length.
    for clause,wanted in [('仅L打者',[['stand','=','L']]),('for L hitters',[['stand','=','L']]),('第8局',[['inning','=',8]]),('for R pitchers',[['p_throws','=','R']])]:
        c=fixed_case();c['inputs']['bound_user_intent']+=' '+clause;ct=build_contract(c);c['raw']['filters']=list(ct.filters)
        trace=exercise(c,catalog,ex);expected=oracle(rows,dict(c['spec'],filters=wanted))
        lost=int(not ct.issues and not ct.filters)
        items.append(dict(id='short_'+clause,attack=True,family='semantic_conservation',expected=expected,trace=trace,incorrect_complete=trace['judge']['goal_status']=='COMPLETE' and not same_rows(trace['rows'],expected),silent_losses=lost,unproved_authority=0))
    # Real data through Tool and Judge. Reference uses separately authored direct SQL.
    realex=AnalyticalExecutor(get_config().postgres,get_config().parquet.archive_path)
    from app.capabilities.analytics.catalog import CATALOG
    for year,role,metric,identity in [(2024,'BATTER','walks',664023),(2025,'BATTER','pa',680757),(2017,'BATTER','walks',458015),(2019,'BATTER','walks',457705),(2016,'BATTER','pa',400085)]:
        c=fixed_case(role=role,metric=metric,entity=identity)
        c['raw']['season']=year
        c['inputs']['analytical_requirements'][0]['value']=year
        source,sql=reference_sql(dict(role=role,metric=metric,player=identity),year);expected=direct(sql,source)
        trace=exercise(c,CATALOG,realex);items.append(dict(id='real_'+str(year)+'_'+str(identity),attack=False,family='real_source_control',source=source,reference_sql=sql,expected=expected,trace=trace,numerical_correct=same_rows(trace['rows'],expected),incorrect_complete=False,silent_losses=0,unproved_authority=0))
        print('real control',year,trace['judge']['goal_status'],same_rows(trace['rows'],expected),flush=True)
    # Same restriction-loss mechanism on the actual current-era data, not a mirror.
    c=fixed_case(metric='pa');clause='against lefties with two strikes'
    c['inputs']['bound_user_intent']+=' '+clause;c['raw']['filters']=[dict(op='COMPARE',field='p_throws',comparator='=',value='L')]
    source,sql=reference_sql(dict(role='BATTER',metric='pa',filters=[['p_throws','=','L'],['strikes','=',2]]),2025)
    expected=direct(sql,source);trace=exercise(c,CATALOG,realex)
    items.append(dict(id='real_residue',attack=True,family='semantic_conservation',source=source,reference_sql=sql,expected=expected,trace=trace,incorrect_complete=trace['judge']['goal_status']=='COMPLETE' and not same_rows(trace['rows'],expected),silent_losses=1,unproved_authority=0))
    # Comparison catches semantic drift even when it happens to leave values equal.
    # New compound sample constraints include one supported dimension and one unknown one.
    c=fixed_case();c['inputs']['bound_user_intent']+=' against lefties with at least 47 PA'
    c['raw']['filters']=[dict(op='COMPARE',field='p_throws',comparator='=',value='L')]
    trace=exercise(c,catalog,ex);ct=trace['contract'];items.append(dict(id='qualification_compound',attack=True,family='semantic_conservation',trace=trace,expected=oracle(rows,dict(c['spec'],filters=[['p_throws','=','L']],qualification={'basis':'PA','minimum':47})),incorrect_complete=trace['judge']['goal_status']=='COMPLETE' and ct['qualification'] is None,silent_losses=int(not ct['issues'] and ct['qualification'] is None),unproved_authority=0))
    # Ranking drift reached from an executable new pitcher-population control.
    c=fixed_case(role='PITCHER',metric='pitches',group=['pitcher_id'],limit=2,order=[('pitches',True),('pitcher_id',False)])
    expected=oracle(rows,c['spec']);trace=exercise(c,catalog,ex)
    items.append(dict(id='pitcher_rank_control',attack=False,family='control',expected=expected,trace=trace,numerical_correct=same_rows(trace['rows'],expected),incorrect_complete=False,silent_losses=0,unproved_authority=0))
    c['raw']['aggregates'].append(dict(name='walks',function='COUNT_IF',condition=dict(op='IN',field='events',values=['walk','intent_walk'])))
    c['raw']['sort'][0]['name']='walks';trace=exercise(c,catalog,ex)
    items.append(dict(id='ranking_metric_supplement',attack=True,family='execution_drift',expected=expected,trace=trace,incorrect_complete=trace['judge']['goal_status']=='COMPLETE',silent_losses=0,unproved_authority=0))
    for form in ['structured','prose']:
        c=fixed_case(group=['batter_id'],filters=[['p_throws','=','L']],qualification={'basis':'PA','minimum':7})
        if form=='prose':
            c['inputs']['analytical_requirements'][-1]['value'].pop('filters');c['inputs']['analytical_requirements'][-1]['value'].pop('qualification')
            c['inputs']['analytical_requirements'].append(dict(id='compound_sample',kind='CONSTRAINT',source='user',required=True,statement='at least 7 PA AND p_throws IN (\'L\')',value='at least 7 PA AND p_throws IN (\'L\')'))
        expected=oracle(rows,c['spec']);trace=exercise(c,catalog,ex)
        items.append(dict(id='qual_population_'+form,attack=False,family='qualification_control',expected=expected,trace=trace,numerical_correct=same_rows(trace['rows'],expected,ordered=False),incorrect_complete=False,silent_losses=0,unproved_authority=0))
    identity=[]
    for name,id in [('Joey Votto',458015),('Andrew McCutchen',457705),('Ichiro Suzuki',400085)]:
        sql='SELECT player_id,player_name FROM public.player_dictionary WHERE player_id='+str(id)
        identity.append(dict(name=name,id=id,sql=sql,rows=direct(sql,'POSTGRES')))
    save('identity_references.json',identity)
    # Supplemental discovery: CJK adjacency defeats the raw-source year guard.
    # Only the intermediate season interpretation is mutated; original user year remains.
    source,sql=reference_sql(dict(role='BATTER',metric='pa'),2025);expected=direct(sql,source)
    for wording in ['Report batter plate appearances in 2025','Report 2025年 batter plate appearances']:
        c=fixed_case();c['raw']['season']=2024
        c['inputs']['analytical_requirements'][0]['value']=2024
        c['inputs']['analytical_requirements'][0]['statement']='Intermediate interpretation: 2024'
        c['inputs']['analytical_requirements'][0]['original_intent']=wording
        c['inputs']['bound_user_intent']=wording;c['inputs']['raw_query']=wording
        trace=exercise(c,CATALOG,realex);wrong=trace['judge']['goal_status']=='COMPLETE'
        items.append(dict(id='year_cjk' if '年' in wording else 'year_english',attack=True,family='semantic_conservation',original_year=2025,mutated_interpretation_year=2024,reference_sql=sql,expected=expected,trace=trace,incorrect_complete=wrong,silent_losses=int(wrong),unproved_authority=0))
    save('supplemental.json',items)
    print('supplemental',len(items),'attacks',sum(x['attack'] for x in items),'incorrect_COMPLETE',sum(x['incorrect_complete'] for x in items),'silent_losses',sum(x['silent_losses'] for x in items),flush=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['prepare','references','live','deterministic','parity','lifecycle','supplemental']);args=p.parse_args()
    freeze();OUT.mkdir(parents=True,exist_ok=True)
    if args.mode=='prepare':save('corpus.json',corpus());save('seeds.json',SEEDS);print('fixed corpus',len(corpus()))
    elif args.mode=='references':references()
    elif args.mode=='live':live()
    elif args.mode=='deterministic':deterministic()
    elif args.mode=='parity':parity()
    elif args.mode=='lifecycle':lifecycle()
    elif args.mode=='supplemental':supplemental()
    freeze()

if __name__=='__main__':main()
