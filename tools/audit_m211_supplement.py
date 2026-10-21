#!/usr/bin/env python3
"""Additional M2.11 boundary controls; no production changes or source writes."""
import copy
import dataclasses
import datetime as dt
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.audit_m211 import Recorder, dump, encode, OUT, ROOT
from app.config import get_config
from app.contracts.models import *
from app.capabilities.analytics.catalog import CATALOG, SchemaCatalog, FieldSpec
from app.capabilities.analytics.ir import AnalyticalQuery, validate_query
from app.capabilities.analytics.compiler import SQLCompiler
from app.capabilities.analytics.executor import AnalyticalExecutor, QueryResult
from app.capabilities.analytics.tool import LocalAnalyticsTool
from app.capabilities.analytics.measures import declare_measures
from app.runtime.engine import Engine
from app.runtime.context import ExecutionContext
from app.runtime.binder import BindingResult
from app.semantic.goals import RequirementBuilder
from app.verification.enumeration import check_compatibility
from app.verification.judge import Judge
from app.response.answer_contract import build_answer_package

def main():
    rec=Recorder();cfg=get_config();ex=AnalyticalExecutor(cfg.postgres,cfg.parquet.archive_path)
    meta=json.loads((OUT/'generated-manifest.json').read_text());player=meta['entity_ids'][0]
    tool=LocalAnalyticsTool(ex,None,parquet_archive=cfg.parquet.archive_path)
    class IR:
        configured=True
        def __init__(self,q):self.q=q
        def complete_json(self,**kw):return SimpleNamespace(data=copy.deepcopy(self.q))
    def invoke(q,objective='report home runs',intent=None,inputs=None,tool_override=None):
        chosen=tool_override or tool;chosen.llm=IR(q)
        request=ToolRequest(need_id='a',capability='local_analytics',objective=objective,
            inputs={'intent':{'objective':objective,'season':2024,'population':'REGULAR_SEASON',**(intent or {})},
                    'entity_sets':[{'ids':[player],'names':['audit player'],'role':'BATTER'}],**(inputs or {})})
        return chosen.execute(request)
    base={'season':2024,'population':'REGULAR_SEASON','entity_filters':[{'role':'BATTER','ids':[player]}],
          'aggregates':[{'name':'home_runs','function':'COUNT_IF','condition':{'op':'COMPARE','field':'events','comparator':'=','value':'home_run'}}]}
    r=invoke(base)
    rec.check('artifact_retains_ir','ir' in r.artifact.structured_data,True,'lineage')
    rec.check('artifact_lineage_edges',bool(r.artifact.lineage),True,'lineage')
    rec.check('artifact_snapshot_or_partition',any(k in r.artifact.provenance for k in ['snapshot','snapshot_id','partition','partitions','as_of']),True,'lineage')
    try:r.artifact.model_dump_json();serializable=True
    except Exception:serializable=False
    rec.check('postgres_artifact_json',serializable,True,'artifact_schema')

    # Name-only base quantities launder a fabricated standard rate through normalization.
    q=copy.deepcopy(base);q['aggregates']=[{'name':'hits','function':'COUNT','field':'*'},
        {'name':'at_bats','function':'COUNT','field':'*'}]
    q['derived']=[{'name':'avg','op':'SAFE_DIV','left':{'kind':'aggregate','name':'hits'},'right':{'kind':'aggregate','name':'at_bats'}}]
    r=invoke(q,'report batting average under threshold')
    measured=r.artifact.structured_data['actual_measures']
    rec.check('wrong_base_avg_attestation',any(x['key']=='avg' and x['verified'] for x in measured),False,'measure',result=encode(r),injected_ir=q)
    sem=SemanticInterpretation(raw_query='How much was audit player batting average in 2024?',entities=[EntityMention(mention='audit player',type_hint='player')],analysis_requests=[AnalysisRequest(text='report batting average',objective='report batting average')],constraints=[Constraint(kind='season',value=2024),Constraint(kind='measure',value='AVG')])
    goal=RequirementBuilder().build(sem,anchor_date=dt.date(2026,9,14))
    entity=Artifact(kind='ENTITY_RESOLUTION',need_id='e',status='ACCEPTED',structured_data={'identity':{'audit player':{'level':'CANONICAL','id':player,'name':'audit player'}}},actual_scope=Scope(entities=[str(player)],entity_names=['audit player']))
    projection=Judge().evaluate(goal,[entity,r.artifact],[])
    rec.check('wrong_avg_complete',projection.goal_status.value,'LIMITED','measure',projection=encode(projection))
    # Recomputed values are sorted/limited by SQL before normalization; the original
    # sorted metric can differ from the final values. Inspect this ownership ordering.
    q=copy.deepcopy(q);q['entity_filters']=[];q['group_by']=['batter_id'];q['sort']=[{'name':'avg','direction':'DESC'}];q['limit']=30
    q['aggregates'][1]['function']='COUNT_NON_NULL';q['aggregates'][1]['field']='events'
    q['derived'][0]['op']='SUB'
    r=invoke(q,'rank batting average',inputs={'entity_sets':[]})
    values=[x['avg'] for x in r.artifact.structured_data['rows']]
    rec.check('normalized_ranking_order',values,sorted(values,reverse=True),'measure',result=encode(r))

    # EMPTY observation vs missing partition is not distinguished at the capability.
    missing=Path('/tmp/m211-missing-partition');missing.mkdir(exist_ok=True)
    link=missing/'mlb_statcast_2023.parquet'
    if not link.exists():link.symlink_to(Path(cfg.parquet.archive_path)/'mlb_statcast_2023.parquet')
    mex=AnalyticalExecutor(cfg.postgres,str(missing));mt=LocalAnalyticsTool(mex,None,parquet_archive=str(missing))
    q={'season':2022,'population':'REGULAR_SEASON','aggregates':[{'name':'n','function':'COUNT','field':'*'}]}
    r=invoke(q,'count pitches',intent={'season':2022},inputs={'entity_sets':[]},tool_override=mt)
    rec.check('missing_partition_not_valid_empty',r.outcome.code=='VALID_EMPTY',False,'availability',result=encode(r))
    q=copy.deepcopy(base);q['season']=2026;q['date_range']={'start':'2026-09-15','end':'2026-09-30'}
    r=invoke(q,intent={'season':2026,'date_range':q['date_range']})
    rec.check('missing_recent_not_accepted',r.artifact.status,'PARTIAL','zero_rows',result=encode(r))
    q=copy.deepcopy(base);q['group_by']=['batter_id'];q['qualification']={'basis':'PA','minimum':100000000}
    r=invoke(q,'count home runs')
    rec.check('grouped_empty_typed',r.outcome.code,'VALID_EMPTY','zero_rows',result=encode(r))

    # Canonical temporal scope loses its resolved window at the local request boundary.
    semantic=SemanticInterpretation(raw_query='How many pitches in the last 15 days?',analysis_requests=[AnalysisRequest(text='count pitches in the last 15 days',objective='count pitches in the last 15 days')])
    goal=RequirementBuilder().build(semantic,anchor_date=dt.date(2026,9,14))
    ctx=ExecutionContext(goal=goal,conversation=ConversationState(session_id='audit'),config=cfg)
    engine=Engine.__new__(Engine);engine.config=cfg
    need=PlannerNeed(id='n',objective='count pitches in the last 15 days',capability='local_analytics')
    request=engine._build_request(need,ctx,semantic,[],BindingResult())
    rec.check('resolved_window_reaches_local',request.inputs['intent']['date_range'],goal.resolved_time_scope.get('date_range'),'temporal',request=encode(request),goal=encode(goal))
    rec.check('binder_window_scope',engine._binding_scope(need,goal).date_range is not None,True,'temporal')

    # Enumeration completeness contract has an independent, strict compatibility gate.
    def art(year,source='POSTGRES',completeness='COMPLETE',qual='PA >= 300'):
        return Artifact(kind='DATABASE_RESULT',status='ACCEPTED',structured_data={'rows':[{'batter_id':player,'n':1}]},enumeration=EnumerationContract(population='REGULAR_SEASON',qualification=qual,season=year,source=source,identity_columns=['batter_id'],entity_kind='PLAYER',entity_namespace='MLBAM',completeness=completeness))
    for name,operation,arts,want in [
        ('delta_cross_source','DELTA',[art(2023,'PARQUET'),art(2024)],True),
        ('truncated_rank','RANK',[art(2024,completeness='TRUNCATED')],False),
        ('unknown_multi','UNKNOWN',[art(2023),art(2024)],False),
        ('overlap_rank','RANK',[art(2024),art(2024)],False),
        ('qualification_mix','DELTA',[art(2023),art(2024,qual='PA >= 400')],False)]:
        result=check_compatibility(GlobalOperation(kind=operation,identity='PLAYER',partition_by='SEASON'),arts)
        rec.check(name,result.ok,want,'enumeration_completeness',result=encode(result))
    # Irrelevant additional schema data cannot affect deterministic compilation.
    query=validate_query(AnalyticalQuery.model_validate(base));original=SQLCompiler().compile(query,'POSTGRES').sql
    spec=CATALOG.table('pitch');fields={**spec.fields,'unrelated_audit':FieldSpec('unrelated_audit','unrelated','float','measure',frozenset({'FILTER','AGG'}))}
    cat=SchemaCatalog({'pitch':dataclasses.replace(spec,fields=fields)})
    rec.check('unrelated_schema_invariance',SQLCompiler(cat).compile(validate_query(query,cat),'POSTGRES').sql,original,'metamorphic')
    # Exact scope mismatch and entity-role contradictions are not derived from user trust.
    q=copy.deepcopy(base);q['date_range']={'start':'2024-08-01','end':'2024-08-30'}
    r=invoke(q,intent={'date_range':{'start':'2024-06-01','end':'2024-06-30'}})
    rec.check('requested_dates_preserved',encode(r.artifact.requested_scope.date_range),{'start':'2024-06-01','end':'2024-06-30'},'analytical_scope_lineage',result=encode(r))
    q=copy.deepcopy(base);q['entity_filters'][0]['role']='PITCHER'
    r=invoke(q)
    rec.check('wrong_role_fails_closed',r.outcome.status,'BLOCKED','analytical_scope_lineage',result=encode(r))
    # Package requirement aggregation takes any SATISFIED child, whereas Goal completion
    # requires all mandatory children; verify this scope display defect independently.
    req=UserRequirement(kind=RequirementKind.ANALYSIS,statement='causes and effects')
    goal=Goal(statement='causes and effects',raw_query='causes and effects',requirements=[req])
    p1=AnswerProposition(requirement_id=req.id,statement='cause',coverage='SATISFIED')
    p2=AnswerProposition(requirement_id=req.id,statement='effect',coverage='UNSUPPORTED')
    projection=AnswerProjection(goal_id=goal.id,goal_status='LIMITED',propositions=[p1,p2])
    package=build_answer_package(goal,projection)
    rec.check('package_partial_children',package.requirements[0].coverage,'PARTIAL','answer_projection',package=encode(package))
    rec.save('supplement-checks.json')

if __name__=='__main__':main()
