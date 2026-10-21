#!/usr/bin/env python3
"""Additional frozen scope obligations absent from entity-filter mutations.

Keep failures: no production retuning, no replacement primary holdout.
"""
import copy,dataclasses,datetime as dt,hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.m212_cases import case,reference
from tools.evaluate_m212_analytics import IR
from tools.m212_evaluation_support import encode
from app.config import get_config
from app.capabilities.analytics.catalog import CATALOG,SchemaCatalog
from app.capabilities.analytics.compiler import SQLCompiler,render_literal
from app.capabilities.analytics.executor import PostgresExecutor,DuckDBExecutor,QueryResult
from app.capabilities.analytics.conformance import SourceAvailability
from app.capabilities.analytics.tool import LocalAnalyticsTool
from app.contracts.models import Artifact,Scope,Goal,UserRequirement,RequirementKind,ToolRequest
from app.verification.judge import Judge
OUT=Path('docs/audit/m212');frozen=json.loads((OUT/'IMPLEMENTATION_FROZEN.json').read_text())
for p,h in frozen['production_hashes'].items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==h
records=[]
for seed in [21299000,21299001]:
 c=case(seed);q=copy.deepcopy(c['raw']);q['qualification']=None;c['raw']['qualification']['minimum']=0
 inputs=copy.deepcopy(c['inputs']);inputs['intent']['qualification']=None
 path=Path('/tmp/m212-scope-edges');path.mkdir(exist_ok=True);file=path/f'mlb_statcast_{q["season"]}.parquet'
 columns=list(c['rows'][0]);values=', '.join('('+', '.join(('DATE '+render_literal(row[k])) if isinstance(row[k],dt.date) else render_literal(row[k]) for k in columns)+')' for row in c['rows'])
 pgtable='(VALUES '+values+') AS fixture('+', '.join(columns)+')'
 import duckdb
 db=duckdb.connect(':memory:');db.execute('CREATE TABLE fixture AS SELECT * FROM '+pgtable);db.execute(f"COPY fixture TO '{file}' (FORMAT PARQUET)");db.close()
 source='POSTGRES' if q['season']>=2024 else 'PARQUET'
 table=pgtable if source=='POSTGRES' else f"read_parquet('{file}')"
 spec=dataclasses.replace(CATALOG.table('pitch'),physical={'POSTGRES':pgtable,'PARQUET':f"read_parquet('{file}')"},key_map={'POSTGRES':CATALOG.table('pitch').key_map['POSTGRES'],'PARQUET':CATALOG.table('pitch').key_map['POSTGRES']})
 cfg=get_config();pg=PostgresExecutor(cfg.postgres);duck=DuckDBExecutor(str(path))
 class Executor:
  def run(self,sql,source):
   if 'public.player_dictionary' in sql:return QueryResult([],[],0,source,0,'name-lookup')
   return pg.run(sql) if source=='POSTGRES' else duck.run(sql)
  def availability(self,source,relation):return SourceAvailability(source=source,relation=relation,known=True,start=min(r['game_date'] for r in c['rows']),end=max(r['game_date'] for r in c['rows']),seasons=(q['season'],),version=hashlib.sha256(file.read_bytes()).hexdigest(),note='identical generated fixture observed extent')
 ex=Executor();tool=LocalAnalyticsTool(ex,None);tool.catalog=SchemaCatalog({'pitch':spec});tool.compiler=SQLCompiler(tool.catalog)
 requirements=[UserRequirement(kind=RequirementKind.SEASON,statement='season',value=q['season']),UserRequirement(kind=RequirementKind.GAME_POPULATION,statement='population',value=q['population']),UserRequirement(kind=RequirementKind.TIME,statement='window',value=q['date_range']),UserRequirement(kind=RequirementKind.MEASURE,statement='AVG',value={'key':'avg','derivable':True}),UserRequirement(kind=RequirementKind.ANALYSIS,statement='Compute the requested batting average',value={'objective':'Compute the requested batting average'})]
 artifacts=[]
 if seed==21299000:
  player=q['entity_filters'][0]['ids'][0];role=q['entity_filters'][0]['role']
  requirements.insert(0,UserRequirement(kind=RequirementKind.ENTITY,statement='bound player',value={'mention':'generated subject','role':role}))
  artifacts=[Artifact(kind='ENTITY_RESOLUTION',structured_data={'identity':{'generated subject':{'level':'CANONICAL','id':player}}})]
  mutation=copy.deepcopy(q);mutation['filters']=[dict(op='COMPARE',field='events',comparator='=',value='home_run')]
  expected=reference(c);name='unexpected_plate_appearance_subset'
 else:
  inputs['entity_sets']=[];inputs['require_entities']=False;inputs['entity_role']='BATTER';inputs['constraints']={};q['entity_filters']=[];q['group_by']=['batter_id'];q['sort']=[dict(name='rate_value',direction='DESC'),dict(name='batter_id',direction='ASC')]
  mutation=copy.deepcopy(q);mutation['group_by']=['pitcher_id'];mutation['sort'][1]['name']='pitcher_id';name='whole_population_role'
  ref_sql=f"SELECT batter_id,1.0*COUNT(*) FILTER(WHERE events IN ('single','double','triple','home_run'))/NULLIF(COUNT(*) FILTER(WHERE events IS NOT NULL AND events NOT IN ('walk','intent_walk','hit_by_pitch','catcher_interf','sac_fly','sac_bunt','sac_fly_double_play','sac_bunt_double_play','truncated_pa','game_advisory')),0) AS rate_value FROM {table} WHERE game_type='{c['code']}' AND game_date BETWEEN '{q['date_range']['start']}' AND '{q['date_range']['end']}' GROUP BY batter_id ORDER BY rate_value DESC NULLS LAST,batter_id ASC LIMIT {q['limit']}"
  expected=ex.run(ref_sql,source).rows
 goal=Goal(statement=name,raw_query='Compute batting average for the bound player and window' if seed==21299000 else 'Rank batters by batting average within the bound window',requirements=requirements)
 request=ToolRequest(need_id='a',capability='local_analytics',objective='Compute batting average grouped results' if seed==21299000 else 'Rank batters by batting average',inputs=inputs)
 results=[]
 for label,raw in [('control',q),('drift',mutation)]:
  tool.llm=IR(raw);result=tool.execute(request);projection=Judge().evaluate(copy.deepcopy(goal),artifacts+([result.artifact] if result.artifact else []),[])
  results.append(dict(label=label,result=encode(result),projection=encode(projection)))
 records.append(dict(seed=seed,name=name,request=encode(request),goal=encode(goal),control_ir=q,drift_ir=mutation,reference=expected,results=results,contained=results[1]['projection']['goal_status']!='COMPLETE'))
 print(seed,name,[(r['label'],r['projection']['goal_status']) for r in results],flush=True)
(OUT/'holdout-scope-edges.json').write_text(json.dumps(dict(frozen_sha=frozen['implementation_sha'],seeds=[21299000,21299001],checks=records),indent=2,default=str)+'\n')
