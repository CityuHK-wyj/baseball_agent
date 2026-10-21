#!/usr/bin/env python3
"""R2 Stage A/frozen evaluation including pre-contract metadata and current Goal.

Generated Requirements and reference rows are fixed before execution. Read-only
PostgreSQL VALUES and identical Parquet files provide independent engine checks.
"""
import argparse
import copy
import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.capabilities.analytics.conformance import attest,compare,requested_contract,receipt_usable,bind_obligations
from app.capabilities.analytics.compiler import SQLCompiler
from app.capabilities.analytics.ir import AnalyticalQuery,validate_query
from app.capabilities.analytics.tool import LocalAnalyticsTool
from app.capabilities.analytics.executor import PostgresExecutor
from app.config import get_config
from app.contracts.models import Goal,UserRequirement,ToolRequest
from app.verification.judge import Judge
from tools.evaluate_m212r import mirror,FixedIR
from tools.m212r2_cases import case,reference,need_attacks,DEV_PROVENANCE,DEV_COMPOUND,DEV_EQUIVALENCE
from tools.m212_evaluation_support import encode,equal
OUT=Path('docs/audit/m212r2')


def freeze_check():
    frozen=json.loads((OUT/'IMPLEMENTATION_FROZEN.json').read_text())
    current={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('app').rglob('*.py')}
    assert current==frozen['production_hashes'],'production changed after freeze'
    return frozen


def goal(c):
    return Goal(statement='Independently bound analytical fact',raw_query=c['inputs']['raw_query'],requirements=[UserRequirement.model_validate(r) for r in c['inputs']['analytical_requirements']])


def main(stage):
    frozen=freeze_check() if stage=='holdout' else None
    if frozen:
        seeds=json.loads((OUT/'UNSEEN_SEEDS.json').read_text())['primary']
    else: seeds=list(DEV_PROVENANCE)
    pg=PostgresExecutor(get_config().postgres)
    probe=pg.run('SELECT 1 AS ok')
    if probe.error:raise RuntimeError(probe.error)
    records=[];path=Path('/tmp/m212r2-'+stage);path.mkdir(exist_ok=True)
    def record(name,seed,ok,**details):records.append(dict(name=name,seed=seed,ok=bool(ok),**encode(details)))
    for seed in seeds:
        c=case(seed);catalog,ex,file=mirror(c,path,pg);compiler=SQLCompiler(catalog);g=goal(c)
        def run(raw,inputs=None,current_goal=None):
            tool=LocalAnalyticsTool(ex,FixedIR(raw));tool.catalog=catalog;tool.compiler=compiler
            request=ToolRequest(need_id='r2',capability='local_analytics',objective='Planner objective',inputs=inputs or c['inputs'])
            result=tool.execute(request);projection=Judge().evaluate(copy.deepcopy(current_goal or g),[result.artifact] if result.artifact else [],[])
            return request,result,projection
        req,control,projection=run(c['raw']);expected=reference(c)
        wanted=requested_contract(LocalAnalyticsTool(None,None)._intent(req),req.inputs,[])
        record('requested_contract_construction',seed,not wanted.issues,contract=wanted)
        record('authoritative_provenance',seed,all(v and all(p['requirement_id'] for p in v) for v in wanted.obligation_provenance.values()),provenance=wanted.obligation_provenance,coverage=wanted.clause_coverage)
        results={}
        for source in ['POSTGRES','PARQUET']:
            q=validate_query(AnalyticalQuery.model_validate(c['raw']));compiled=compiler.compile(q,source);res=ex.run(compiled.sql,source);results[source]=res.rows
            record('reference_'+source,seed,not res.error and equal(res.rows,expected),actual=res.rows,expected=expected,error=res.error,sql=compiled.sql)
        record('engine_parity',seed,equal(results['POSTGRES'],results['PARQUET']))
        actual=control.artifact.structured_data['rows'] if control.artifact else None
        actual=[{k:v for k,v in r.items() if k in expected[0]} for r in actual] if actual and expected else actual
        record('valid_request',seed,control.artifact is not None and equal(actual,expected) and projection.goal_status.value=='COMPLETE',request=req,ir=c['raw'],result=control,projection=projection,expected=expected)
        for attack in need_attacks(c):
            request,result,judged=run(attack['ir'],attack['inputs'])
            rebuilt=requested_contract(LocalAnalyticsTool(None,None)._intent(request),request.inputs,[])
            semantic_attack=attack['name']!='Need_injected_ordering' or c['raw']['limit'] is not None
            record(attack['name'],seed,rebuilt==wanted and (judged.goal_status.value!='COMPLETE' if semantic_attack else result.artifact is not None),
                   kind='adversarial' if semantic_attack else 'positive',incorrect_COMPLETE=semantic_attack and judged.goal_status.value=='COMPLETE',request=request,ir=attack['ir'],contract=rebuilt,result=result,projection=judged)
        # Candidate-only population/operator/measure/scope drift remains a separate layer.
        mutations=[('unexpected_filter',dict(filters=[*c['raw']['filters'],dict(op='COMPARE',field='inning',comparator='>',value=4)])),
                   ('role_swap',dict(analytical_role='PITCHER' if c['raw']['analytical_role']=='BATTER' else 'BATTER')),
                   ('date_drift',dict(date_range={**c['raw']['date_range'],'end':(dt.date.fromisoformat(c['raw']['date_range']['end'])+dt.timedelta(days=1)).isoformat()})),
                   ('population_drift',dict(population='SPRING_TRAINING')),
                   ('Measure_laundering',dict(aggregates=[dict(name=c['column'],function='SUM',field='release_speed'),dict(name='sample',function='COUNT_NON_NULL',field='events')]))]
        if c['raw']['qualification']:mutations.append(('qualification_drop',dict(qualification=None)))
        if c['raw']['filters']:mutations.append(('row_predicate_drop',dict(filters=[])))
        if c['raw']['limit']:mutations.append(('limit_drift',dict(limit=c['raw']['limit']+1)))
        for name,patch in mutations:
            raw={**copy.deepcopy(c['raw']),**patch};request,result,judged=run(raw)
            record('IR_'+name,seed,judged.goal_status.value!='COMPLETE',kind='adversarial',incorrect_COMPLETE=judged.goal_status.value=='COMPLETE',request=request,ir=raw,result=result,projection=judged)
        # The producer has a self-consistent but user-inconsistent Requirement copy.
        # Only the independent current Goal defines the actual user obligation.
        ins=copy.deepcopy(c['inputs']);ins['analytical_requirements'][-1]['value']['filters'].append(dict(op='IN',field='p_throws',values=['L']))
        raw=copy.deepcopy(c['raw']);raw['filters'].append(dict(op='IN',field='p_throws',values=['L']))
        request,result,judged=run(raw,ins)
        record('self_certification',seed,result.artifact is not None and receipt_usable(result.artifact) and judged.goal_status.value!='COMPLETE',kind='adversarial',incorrect_COMPLETE=judged.goal_status.value=='COMPLETE',request=request,current_goal=g,result=result,projection=judged)
        # New compound: independent clauses + full candidate; then each omitted clause.
        comp=copy.deepcopy(c);comp['raw'].update(group_by=['stand'],sort=[dict(name='stand',direction='ASC')],limit=None,qualification=dict(basis='PITCHES',minimum=3),filters=[dict(op='IN',field='pitch_type',values=['FC','CU'])])
        comp['inputs']['analytical_requirements'][-1]['value']=f"minimum 3 PITCHES AND pitch_type IN ('FC', 'CU') AND group by stand"
        comp['inputs']['analytical_requirements'].append(dict(id=f'role_{seed}',kind='CONSTRAINT',value={'analytical_role':comp['raw']['analytical_role']},statement='Bound role'))
        compg=goal(comp);request,result,judged=run(comp['raw'],comp['inputs'],compg)
        record('valid_compound',seed,result.artifact is not None and judged.goal_status.value=='COMPLETE',result=result,projection=judged)
        for name,patch in [('qualification',dict(qualification=None)),('row_population',dict(filters=[])),('grouping',dict(group_by=[],sort=[]))]:
            raw={**copy.deepcopy(comp['raw']),**patch};request,result,judged=run(raw,comp['inputs'],compg)
            record('compound_clause_omission_'+name,seed,judged.goal_status.value!='COMPLETE',kind='adversarial',incorrect_COMPLETE=judged.goal_status.value=='COMPLETE',result=result,projection=judged)
        ins=copy.deepcopy(comp['inputs']);ins['analytical_requirements'][-2]['value']+=' AND unproven population condition'
        request,result,judged=run(comp['raw'],ins,goal({**comp,'inputs':ins}))
        record('partial_parse',seed,result.artifact is None and result.outcome.code=='ANALYTICAL_OBLIGATION_UNPROVEN' and judged.goal_status.value!='COMPLETE',kind='adversarial',incorrect_COMPLETE=judged.goal_status.value=='COMPLETE',result=result,projection=judged)
        # Valid syntactic variants are independent of the safety denominators.
        equivalents=[]
        raw=copy.deepcopy(c['raw']);raw['season']=None
        equivalents.append(('same_effective_window',raw))
        raw=copy.deepcopy(c['raw']);raw['season']=None;raw['filters'].append(dict(op='COMPARE',field='season',comparator='=',value=float(c['raw']['season'])))
        equivalents.append(('integral_numeric_season',raw))
        if c['raw']['filters']:
            raw=copy.deepcopy(c['raw']);p=raw['filters'][0];raw['filters'][0]=dict(op='OR',conditions=[dict(op='COMPARE',field=p['field'],comparator='=',value=v) for v in reversed(p['values'])])
            equivalents.append(('OR_IN',raw))
        for name,raw in equivalents:
            request,result,judged=run(raw)
            record('equivalent_'+name,seed,result.artifact is not None and judged.goal_status.value=='COMPLETE',kind='positive',result=result,projection=judged)
        if c['raw']['limit'] is None:
            ins=copy.deepcopy(c['inputs']);ins['internal_row_ceiling']=5000
            request,result,judged=run(c['raw'],ins)
            record('internal_ceiling',seed,result.artifact is not None and judged.goal_status.value=='COMPLETE',kind='positive',result=result,projection=judged)
        print(stage,seed,projection.goal_status.value,flush=True)
    summary={}
    for r in records:
        entry=summary.setdefault(r['name'],dict(passed=0,failed=0,incorrect_COMPLETE=0));entry['passed' if r['ok'] else 'failed']+=1;entry['incorrect_COMPLETE']+=r.get('incorrect_COMPLETE',False)
    payload=dict(stage=stage,sha=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),frozen_sha=frozen['implementation_sha'] if frozen else None,seeds=seeds,summary=summary,checks=records)
    OUT.mkdir(parents=True,exist_ok=True);(OUT/(stage+'-primary.json')).write_text(json.dumps(payload,indent=2,default=str)+'\n')
    if frozen:freeze_check()
    print(json.dumps(summary),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['development','holdout']);main(parser.parse_args().stage)
