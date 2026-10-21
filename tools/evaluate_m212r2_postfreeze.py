#!/usr/bin/env python3
"""Independent frozen review of authority, coverage and finite equivalence.

Written after implementation freeze. Every attack remains in the evidence,
including discoveries outside the development vocabulary. Production files are
never changed. Expectations are fixed by independently authored Requirements.
"""
import copy
import datetime as dt
import json
import random
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.capabilities.analytics.compiler import SQLCompiler
from app.capabilities.analytics.conformance import requested_contract,receipt_usable,digest
from app.capabilities.analytics.executor import PostgresExecutor
from app.capabilities.analytics.tool import LocalAnalyticsTool
from app.config import get_config
from app.contracts.models import ToolRequest
from app.verification.judge import Judge
from tools.evaluate_m212r import mirror,FixedIR
from tools.evaluate_m212r2 import OUT,freeze_check,goal
from tools.m212r2_cases import case,reference
from tools.m212_evaluation_support import encode,equal


def main():
    frozen=freeze_check();seeds=json.loads((OUT/'UNSEEN_SEEDS.json').read_text())
    pg=PostgresExecutor(get_config().postgres);records=[]
    directory=Path('/tmp/m212r2-independent');directory.mkdir(exist_ok=True)
    def record(name,seed,ok,**details):
        records.append(dict(name=name,seed=seed,ok=bool(ok),**encode(details)))
    for seed in seeds['independent_omitted_dimension_review']:
        rng=random.Random(seed);c=case(seed);g=goal(c)
        catalog,executor,_=mirror(c,directory,pg)
        def run(raw,inputs=None,current=None):
            tool=LocalAnalyticsTool(executor,FixedIR(raw));tool.catalog=catalog;tool.compiler=SQLCompiler(catalog)
            request=ToolRequest(need_id='independent',capability='local_analytics',objective='Proposed execution',inputs=inputs or c['inputs'])
            result=tool.execute(request)
            judged=Judge().evaluate(copy.deepcopy(current or g),[result.artifact] if result.artifact else [],[])
            return request,result,judged
        request,control,judged=run(c['raw'])
        wanted=requested_contract(LocalAnalyticsTool(None,None)._intent(request),request.inputs,[])
        record('independent_control',seed,control.artifact is not None and judged.goal_status.value=='COMPLETE',result=control,projection=judged)
        # Full contract surface, including authority/proof metadata and defaults.
        end=(dt.date.fromisoformat(c['raw']['date_range']['end'])-dt.timedelta(days=2)).isoformat()
        group=['stand'] if c['raw']['group_by']!=['stand'] else ['inning']
        role='PITCHER' if c['raw']['analytical_role']=='BATTER' else 'BATTER'
        filt=[*c['raw']['filters'],dict(op='COMPARE',field='strikes',comparator='>=',value=1)]
        proposals=[
            ('relation',{'relation':'game'},{'table':'game'}),
            ('analytical_role',{'analytical_role':role,'entity_role':role},{'analytical_role':role}),
            ('entities',{'entity_sets':[{'role':c['raw']['analytical_role'],'ids':[c['rows'][0][c['identity']]]}]},{'entity_filters':[{'role':c['raw']['analytical_role'],'ids':[c['rows'][0][c['identity']]]}]}),
            ('require_entities',{'require_entities':True},None),
            ('season',{'season':c['raw']['season']+1},{'season':c['raw']['season']+1,'date_range':None}),
            ('date_range',{'date_range':{**c['raw']['date_range'],'end':end}},{'date_range':{**c['raw']['date_range'],'end':end}}),
            ('game_types',{'population':'SPRING_TRAINING','game_types':['S']},{'population':'SPRING_TRAINING'}),
            ('qualification',{'qualification':{'basis':'RESULTS','minimum':rng.randint(20,40)}},{'qualification':{'basis':'RESULTS','minimum':35}}),
            ('measures',{'requested_measures':[{'key':'pitches'}]},{'aggregates':[{'name':'pitches','function':'COUNT','field':'*'}],'sort':[]}),
            ('filters',{'filters':filt},{'filters':filt}),
            ('group_by',{'group_by':group},{'group_by':group,'sort':[],'limit':None}),
            ('ordering',{'ordering':[{'metric':'pa','direction':'ASC'}]},{'sort':[{'name':'sample','direction':'ASC'}]} if c['raw']['limit'] else None),
            ('tie_break_ordering',{'tie_break_ordering':[{'field':'inning','direction':'DESC'}]},None),
            ('limit',{'limit':(c['raw']['limit'] or 0)+1},{'limit':(c['raw']['limit'] or 0)+1} if c['raw']['group_by'] else None),
            ('comparison_population',{'for_comparison':True,'comparison_population':True},None),
            ('requirement_ids',{'requirement_ids':['planner_replacement']},None),
            ('issues',{'obligation_issues':['Planner withholding hint']},None),
            ('obligation_provenance',{'obligation_provenance':{'filters':[{'origin':'user','status':'BOUND','requirement_id':'forged'}]}},None),
            ('clause_coverage',{'clause_coverage':[{'state':'COMPLETE','unresolved':[]}]},None),
        ]
        for dimension,metadata,ir_patch in proposals:
            ins=copy.deepcopy(c['inputs']);ins['intent'].update(metadata);ins['constraints'].update(metadata)
            # Exercise the open top-level inputs as well as nested metadata.
            for key in ['entity_sets','requested_measures','for_comparison','requirement_ids','obligation_provenance','clause_coverage']:
                if key in metadata:ins[key]=metadata[key]
            raw={**copy.deepcopy(c['raw']),**(ir_patch or {})}
            request,result,projection=run(raw,ins)
            rebuilt=requested_contract(LocalAnalyticsTool(None,None)._intent(request),request.inputs,[])
            adversarial=ir_patch is not None
            record('contract_field_'+dimension,seed,rebuilt==wanted and (projection.goal_status.value!='COMPLETE' if adversarial else result.artifact is not None),
                   kind='adversarial' if adversarial else 'positive',incorrect_COMPLETE=adversarial and projection.goal_status.value=='COMPLETE',
                   request=request,current_goal=g,ir=raw,contract=rebuilt,result=result,projection=projection)
        # Lower the producer's normalized mandatory obligation while retaining an
        # independently supplied current Goal. This is a pre-contract attack.
        independent=copy.deepcopy(c);independent['inputs']['analytical_requirements'][-1]['value']['filters'].append(dict(op='IN',field='stand',values=['L']))
        current=goal(independent)
        request,result,projection=run(c['raw'],c['inputs'],current)
        record('weakened_Requirement_copy',seed,result.artifact is not None and projection.goal_status.value!='COMPLETE',kind='adversarial',incorrect_COMPLETE=projection.goal_status.value=='COMPLETE',current_goal=current,request=request,result=result,projection=projection)
        # Partial parser outputs cannot assert completeness by dropping a ledger
        # row, independently of clause position and supported clause type.
        import app.capabilities.analytics.conformance as module
        original=module.constraint_coverage
        comp=copy.deepcopy(c)
        clauses=[f'minimum {rng.randint(5,17)} PITCHES',"pitch_type IN ('CU', 'FC')",'group by stand']
        rng.shuffle(clauses);text=' AND '.join(clauses)
        comp['inputs']['analytical_requirements'][-1]['value']=text
        comp['inputs']['analytical_requirements'].append(dict(id=f'role_{seed}',kind='CONSTRAINT',value={'analytical_role':c['raw']['analytical_role']},statement='Independent role'))
        for position in range(len(clauses)):
            def dropped(value,*args,**kwargs):
                coverage=copy.deepcopy(original(value,*args,**kwargs))
                if value==text:coverage['recognized']=[r for r in coverage['recognized'] if r.get('clause_index')!=position]
                return coverage
            with patch.object(module,'constraint_coverage',dropped):request,result,projection=run(c['raw'],comp['inputs'],goal(comp))
            record('adapter_clause_drop_'+str(position),seed,result.artifact is None and projection.goal_status.value!='COMPLETE',kind='adversarial',incorrect_COMPLETE=projection.goal_status.value=='COMPLETE',request=request,result=result,projection=projection)
        # User-source residue is independent of the finite supported constraints.
        # These factual modifiers require interpretation or an explicit unproven
        # state. None can safely be classified as presentation/research language.
        clauses=['in day games','on the road','during doubleheaders','against southpaws','during extra innings','from the seventh inning onward']
        rng.shuffle(clauses)
        for clause in clauses:
            ins=copy.deepcopy(c['inputs']);ins['bound_user_intent']=ins['raw_query']+' AND '+clause
            current=goal({**c,'inputs':{**ins,'raw_query':ins['bound_user_intent']}})
            request,result,projection=run(c['raw'],ins,current)
            contract=requested_contract(LocalAnalyticsTool(None,None)._intent(request),request.inputs,[])
            record('source_clause_loss',seed,bool(contract.issues) and result.artifact is None and projection.goal_status.value!='COMPLETE',kind='adversarial',incorrect_COMPLETE=projection.goal_status.value=='COMPLETE',semantic_clause=clause,request=request,current_goal=current,contract=contract,result=result,projection=projection)
        # Unknown compound clauses in the normalized Requirement must also fail
        # closed, rather than being blessed by a successful first extraction.
        for position in range(3):
            ins=copy.deepcopy(comp['inputs']);parts=clauses[:2]+['minimum 7 PITCHES'];rng.shuffle(parts)
            parts.insert(position,'unresolved eligibility rule')
            ins['analytical_requirements'][-2]['value']=' AND '.join(parts)
            request,result,projection=run(c['raw'],ins,goal({**c,'inputs':ins}))
            record('unsupported_compound_'+str(position),seed,result.artifact is None and projection.goal_status.value!='COMPLETE',kind='adversarial',incorrect_COMPLETE=projection.goal_status.value=='COMPLETE',request=request,result=result,projection=projection)
        if control.artifact:
            # Even rehashed execution fields cannot disagree with validated IR.
            for field,value in [('analytical_role',role),('filters',filt),('group_by',group),('season',c['raw']['season']+1),('limit',17),('game_types',['S'])]:
                artifact=copy.deepcopy(control.artifact);receipt=artifact.structured_data['execution_receipt'];receipt['attestation'][field]=value
                receipt['attestation_digest']=digest(receipt['attestation'])
                projection=Judge().evaluate(copy.deepcopy(g),[artifact],[])
                record('receipt_field_'+field,seed,not receipt_usable(artifact) and projection.goal_status.value!='COMPLETE',kind='adversarial',incorrect_COMPLETE=projection.goal_status.value=='COMPLETE',artifact=artifact,projection=projection)
        print('independent',seed,flush=True)
    # Independent equivalent encodings: new predicate pairs and exact windows,
    # not merely replaying the development OR/IN members.
    for seed in seeds['independent_equivalence']:
        c=case(seed);rng=random.Random(seed)
        predicates=[dict(op='IN',field='balls',values=rng.sample([0,1,2,3],2)),dict(op='IN',field='stand',values=['L','R'])]
        c['raw']['filters']=predicates;c['inputs']['analytical_requirements'][-1]['value']['filters']=copy.deepcopy(predicates)
        catalog,executor,_=mirror(c,directory,pg);expected=reference(c);g=goal(c)
        variants=[]
        raw=copy.deepcopy(c['raw']);raw['filters']=list(reversed(raw['filters']));variants.append(('conjunction_order',raw))
        raw=copy.deepcopy(c['raw']);raw['filters']=[dict(p,values=list(reversed(p['values']))) for p in predicates];variants.append(('IN_member_order',raw))
        raw=copy.deepcopy(c['raw']);raw['filters']=[dict(op='OR',conditions=[dict(op='COMPARE',field=p['field'],comparator='=',value=float(v) if isinstance(v,int) else v) for v in reversed(p['values'])]) for p in predicates];variants.append(('finite_OR_numeric',raw))
        raw=copy.deepcopy(c['raw']);raw['season']=None;variants.append(('exact_window',raw))
        raw=copy.deepcopy(c['raw']);raw['season']=None;raw['filters'].append(dict(op='COMPARE',field='season',comparator='=',value=float(c['raw']['season'])));variants.append(('governed_integral_scope',raw))
        raw=copy.deepcopy(c['raw']);raw['date_range']={k:dt.date.fromisoformat(v) for k,v in raw['date_range'].items()};variants.append(('date_objects',raw))
        for name,raw in variants:
            tool=LocalAnalyticsTool(executor,FixedIR(raw));tool.catalog=catalog;tool.compiler=SQLCompiler(catalog)
            request=ToolRequest(need_id='equivalence',capability='local_analytics',objective='Equivalent execution',inputs=c['inputs'])
            result=tool.execute(request);projection=Judge().evaluate(copy.deepcopy(g),[result.artifact] if result.artifact else [],[])
            actual=result.artifact.structured_data['rows'] if result.artifact else None
            if actual and expected:actual=[{k:v for k,v in row.items() if k in expected[0]} for row in actual]
            record('independent_equivalent_'+name,seed,result.artifact is not None and projection.goal_status.value=='COMPLETE' and equal(actual,expected),kind='positive',result=result,projection=projection,expected=expected,actual=actual)
    summary={}
    for r in records:
        counts=summary.setdefault(r['name'],dict(passed=0,failed=0,incorrect_COMPLETE=0));counts['passed' if r['ok'] else 'failed']+=1;counts['incorrect_COMPLETE']+=r.get('incorrect_COMPLETE',False)
    payload=dict(frozen_sha=frozen['implementation_sha'],authored_after_freeze=True,seeds=seeds,summary=summary,checks=records)
    (OUT/'holdout-independent-review.json').write_text(json.dumps(payload,indent=2,default=str)+'\n')
    freeze_check();print(json.dumps(summary),flush=True)


if __name__=='__main__':main()
