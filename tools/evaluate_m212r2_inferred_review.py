#!/usr/bin/env python3
"""Post-freeze authority review: inferred is not proof of safe derivation."""
import copy
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.capabilities.analytics.compiler import SQLCompiler
from app.capabilities.analytics.conformance import requested_contract
from app.capabilities.analytics.executor import PostgresExecutor
from app.capabilities.analytics.tool import LocalAnalyticsTool
from app.config import get_config
from app.contracts.models import ToolRequest
from app.verification.judge import Judge
from tools.evaluate_m212r import mirror,FixedIR
from tools.evaluate_m212r2 import OUT,freeze_check,goal
from tools.m212r2_cases import case
from tools.m212_evaluation_support import encode

def main():
    frozen=freeze_check();seeds=json.loads((OUT/'UNSEEN_SEEDS.json').read_text())['independent_omitted_dimension_review'];records=[]
    directory=Path('/tmp/m212r2-inferred');directory.mkdir(exist_ok=True);pg=PostgresExecutor(get_config().postgres)
    for seed in seeds:
        c=case(seed);catalog,executor,_=mirror(c,directory,pg)
        for kind in ['filters','group_by','limit','qualification']:
            if kind=='limit' and not c['raw']['group_by']:continue
            ins=copy.deepcopy(c['inputs']);raw=copy.deepcopy(c['raw'])
            if kind=='filters':value=[dict(op='COMPARE',field='strikes',comparator='=',value=0)];raw['filters']+=value
            elif kind=='group_by':
                field=next(f for f in ['p_throws','stand','inning'] if f not in raw['group_by']);value=[field];raw['group_by']+=value
            elif kind=='limit':value=(raw['limit'] or 0)+1;raw['limit']=value
            else:value=dict(basis='PITCHES',minimum=31);raw['qualification']=value
            addition=dict(id=f'inferred_{kind}_{seed}',kind='CONSTRAINT',value={kind:value},statement='Proposed analytical refinement',source='inferred',required=True,original_intent='Execution-planning inference',references=[])
            ins['analytical_requirements'].append(addition)
            tool=LocalAnalyticsTool(executor,FixedIR(raw));tool.catalog=catalog;tool.compiler=SQLCompiler(catalog)
            request=ToolRequest(need_id='inferred-review',capability='local_analytics',objective='Proposed refinement',inputs=ins)
            bound=requested_contract(tool._intent(request),request.inputs,[]);result=tool.execute(request)
            current=goal({**c,'inputs':ins});projection=Judge().evaluate(copy.deepcopy(current),[result.artifact] if result.artifact else [],[])
            records.append(dict(name='unproven_inferred_'+kind,seed=seed,kind='adversarial',ok=bool(bound.issues) and result.artifact is None and projection.goal_status.value!='COMPLETE',incorrect_COMPLETE=projection.goal_status.value=='COMPLETE',
                                independent_original_requirements=c['requirements'],unproven_addition=addition,request=encode(request),contract=encode(bound),result=encode(result),current_goal=encode(current),projection=encode(projection)))
    (OUT/'holdout-inferred-authority-review.json').write_text(json.dumps(dict(frozen_sha=frozen['implementation_sha'],checks=records),indent=2,default=str)+'\n')
    freeze_check();print('incorrect COMPLETE',sum(r['incorrect_COMPLETE'] for r in records),'of',len(records))

if __name__=='__main__':main()
