#!/usr/bin/env python3
"""Replay M2.11 saved analytical controls as legacy gates, without changing evidence."""
import copy,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.evaluate_m212_analytics import IR
from tools.m212_evaluation_support import encode
from app.config import get_config
from app.capabilities.analytics.executor import AnalyticalExecutor
from app.capabilities.analytics.tool import LocalAnalyticsTool
from app.contracts.models import ToolRequest,Artifact,Scope,Goal,UserRequirement,RequirementKind
from app.verification.judge import Judge

cfg=get_config();executor=AnalyticalExecutor(cfg.postgres,cfg.parquet.archive_path)
tool=LocalAnalyticsTool(executor,None,parquet_archive=cfg.parquet.archive_path)
saved=json.loads(Path('docs/audit/m212/legacy-inputs/controlled-tool-traces.json').read_text());base=saved[0]['ir_injected'];records=[]
entity=Artifact(kind='ENTITY_RESOLUTION',structured_data={'identity':{'audit player':{'level':'CANONICAL','id':base['entity_filters'][0]['ids'][0],'name':'audit player'}}})
def run(name,raw,request,goal=None,chosen=None):
 chosen=chosen or tool;chosen.llm=IR(raw);result=chosen.execute(ToolRequest.model_validate(request))
 projection=Judge().evaluate(Goal.model_validate(goal),[entity]+([result.artifact] if result.artifact else []),[]) if goal else None
 if name=='valid':ok=projection and projection.goal_status.value=='COMPLETE'
 elif name in {'cross_boundary','data_after_snapshot','missing_partition'}:ok=result.artifact is not None and result.artifact.status!='ACCEPTED' and result.outcome.code!='VALID_EMPTY'
 elif name=='empty_population':ok=result.outcome.code=='VALID_EMPTY'
 else:ok=result.outcome.code=='ANALYTICAL_NONCONFORMANCE' and (projection is None or projection.goal_status.value!='COMPLETE')
 records.append(dict(name=name,ok=bool(ok),request=request,ir=raw,result=encode(result),projection=encode(projection)))
for item in saved:
 name=item['name'];raw=copy.deepcopy(item.get('ir_injected') or base);request=item['request'];intent=request['inputs']['intent']
 if name in {'cross_boundary','data_after_snapshot'}:raw.update(season=intent['season'],date_range=intent['date_range'])
 if name=='empty_population':raw['filters']=[dict(op='COMPARE',field='release_speed',comparator='>',value=200)];raw['aggregates'].insert(0,dict(name='n',function='COUNT',field='*'))
 goal=item.get('goal') or (item.get('projection') and saved[0]['goal'])
 if name=='qualification_omitted':
  goal=copy.deepcopy(saved[0]['goal']);goal['requirements'].append(UserRequirement(kind=RequirementKind.CONSTRAINT,statement='minimum PA 100000000',value={'basis':'PA','minimum':100000000}).model_dump(mode='json'))
 run(name,raw,request,goal)
request=copy.deepcopy(saved[0]['request'])
raw=copy.deepcopy(base);raw['entity_filters'][0]['role']='PITCHER';run('wrong_role',raw,request,saved[0]['goal'])
raw=copy.deepcopy(base);raw['date_range']={'start':'2024-08-01','end':'2024-08-30'};request['inputs']['intent']['date_range']={'start':'2024-06-01','end':'2024-06-30'};run('different_dates',raw,request,saved[0]['goal'])
supplement=json.loads(Path('docs/audit/m212/legacy-inputs/supplement-checks.json').read_text())
raw=next(r['injected_ir'] for r in supplement['checks'] if r['name']=='wrong_base_avg_attestation')
request=copy.deepcopy(saved[0]['request']);request['objective']='report batting average under threshold';request['inputs']['intent']['objective']=request['objective'];run('metric_laundering',raw,request)
raw=copy.deepcopy(raw);raw['entity_filters']=[];raw['group_by']=['batter_id'];raw['sort']=[dict(name='avg',direction='DESC')];raw['limit']=30;raw['aggregates'][1].update(function='COUNT_NON_NULL',field='events');raw['derived'][0]['op']='SUB';request['inputs']['entity_sets']=[];request['objective']='rank batting average';request['inputs']['intent']['objective']=request['objective'];run('ranking_attestation_drift',raw,request)
missing=Path('/tmp/m212-legacy-missing');missing.mkdir(exist_ok=True)
link=missing/'mlb_statcast_2023.parquet'
if not link.exists():link.symlink_to(Path(cfg.parquet.archive_path)/'mlb_statcast_2023.parquet')
ex=AnalyticalExecutor(cfg.postgres,str(missing));mt=LocalAnalyticsTool(ex,None,parquet_archive=str(missing))
request=copy.deepcopy(saved[0]['request']);request['inputs']['entity_sets']=[];request['objective']='count pitches';request['inputs']['intent'].update(objective='count pitches',season=2022)
run('missing_partition',dict(season=2022,population='REGULAR_SEASON',aggregates=[dict(name='n',function='COUNT',field='*')]),request,chosen=mt)
Path('docs/audit/m212/legacy.json').write_text(json.dumps(dict(passed=sum(r['ok'] for r in records),total=len(records),checks=records),indent=2,default=str)+'\n')
print([(r['name'],r['ok']) for r in records])
