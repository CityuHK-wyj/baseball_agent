#!/usr/bin/env python3
"""Correct the independent evaluator's table/relation oracle without erasure."""
import copy
import gzip
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.capabilities.analytics.compiler import SQLCompiler
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
    frozen=freeze_check();path=OUT/'holdout-independent-review.json';old=json.loads(path.read_text() if path.exists() else gzip.decompress(Path(str(path)+'.gz').read_bytes()))
    pg=PostgresExecutor(get_config().postgres);directory=Path('/tmp/m212r2-relation');directory.mkdir(exist_ok=True)
    checks=[]
    for seed in old['seeds']['independent_omitted_dimension_review']:
        c=case(seed);catalog,executor,_=mirror(c,directory,pg)
        raw={**copy.deepcopy(c['raw']),'relation':'game'}
        tool=LocalAnalyticsTool(executor,FixedIR(raw));tool.catalog=catalog;tool.compiler=SQLCompiler(catalog)
        request=ToolRequest(need_id='relation-review',capability='local_analytics',objective='Unsupported relation',inputs=c['inputs'])
        result=tool.execute(request);projection=Judge().evaluate(goal(c),[result.artifact] if result.artifact else [],[])
        checks.append(dict(name='actual_relation_drift',seed=seed,kind='adversarial',ok=result.artifact is None and projection.goal_status.value!='COMPLETE',incorrect_COMPLETE=projection.goal_status.value=='COMPLETE',request=encode(request),ir=raw,result=encode(result),projection=encode(projection)))
    numerical=[]
    for r in old['checks']:
        if r['name']!='source_clause_loss' or not r['incorrect_COMPLETE']:continue
        c=case(r['seed']);clause=r['semantic_clause']
        predicate={'from the seventh inning onward':dict(op='IN',field='inning',values=list(range(7,13))),
                   'during extra innings':dict(op='IN',field='inning',values=list(range(10,13))),
                   'against southpaws':dict(op='IN',field='p_throws',values=['L'])}.get(clause)
        if predicate:
            c['raw']['filters'].append(predicate);expected=reference(c)
            actual=r['result']['artifact']['structured_data']['rows']
            if actual and expected:actual=[{k:v for k,v in row.items() if k in expected[0]} for row in actual]
            numerical.append(dict(seed=r['seed'],clause=clause,expected=expected,actual=actual,numerically_wrong=not equal(actual,expected)))
    payload=dict(frozen_sha=frozen['implementation_sha'],original_evidence='holdout-independent-review.json',
        adjudication={'contract_field_relation':'32 original probes added the unknown extra key table. AnalyticalQuery ignores it; validated IR and execution remained pitch. These are benign ignored hints, not semantic attacks. Original traces and raw counts are preserved; 32 real relation mutations are added here.',
                      'source_clause_loss':'All 160 COMPLETE cases are genuine mandatory user-source omission failures. Unsupported modifiers must remain unproven, irrespective of numerical availability.'},
        corrected_original_counts={'benign_relation_probes':32,'incorrect_COMPLETE':160},checks=checks,numerical_source_review=numerical)
    (OUT/'holdout-evaluator-adjudication.json').write_text(json.dumps(payload,indent=2,default=str)+'\n')
    freeze_check();print('actual relation',sum(r['ok'] for r in checks),len(checks),'independently wrong values',sum(r['numerically_wrong'] for r in numerical),len(numerical))

if __name__=='__main__':main()
