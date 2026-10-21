#!/usr/bin/env python3
"""Preserved supplemental discovery: opaque ANALYSIS clause is not coverage."""
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
    frozen=freeze_check();seeds=json.loads((OUT/'UNSEEN_SEEDS.json').read_text())['independent_omitted_dimension_review']
    pg=PostgresExecutor(get_config().postgres);directory=Path('/tmp/m212r2-compound-review');directory.mkdir(exist_ok=True);records=[]
    for seed in seeds:
        c=case(seed)
        for req in c['inputs']['analytical_requirements']:
            if req['kind']=='ANALYSIS':
                text=req['value']['objective']+' AND during extra innings'
                req['value']['objective']=text;req['statement']=text
        c['inputs']['raw_query']=text;c['inputs']['bound_user_intent']=text
        catalog,executor,_=mirror(c,directory,pg);tool=LocalAnalyticsTool(executor,FixedIR(c['raw']));tool.catalog=catalog;tool.compiler=SQLCompiler(catalog)
        request=ToolRequest(need_id='compound-discovery',capability='local_analytics',objective='Attempt subset calculation',inputs=c['inputs'])
        contract=requested_contract(tool._intent(request),request.inputs,[]);result=tool.execute(request);g=goal(c)
        projection=Judge().evaluate(copy.deepcopy(g),[result.artifact] if result.artifact else [],[])
        records.append(dict(name='mandatory_ANALYSIS_opaque_clause',seed=seed,kind='adversarial',ok=bool(contract.issues) and result.artifact is None and projection.goal_status.value!='COMPLETE',incorrect_COMPLETE=projection.goal_status.value=='COMPLETE',contract=encode(contract),request=encode(request),current_goal=encode(g),result=encode(result),projection=encode(projection)))
    (OUT/'holdout-compound-discovery.json').write_text(json.dumps(dict(frozen_sha=frozen['implementation_sha'],checks=records),indent=2,default=str)+'\n')
    freeze_check();print('incorrect COMPLETE',sum(r['incorrect_COMPLETE'] for r in records),'of',len(records))

if __name__=='__main__':main()
