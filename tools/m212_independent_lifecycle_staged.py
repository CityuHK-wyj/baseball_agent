#!/usr/bin/env python3
"""Transient first-pass uncertainty injection, confined to acceptance memory.

This is a state-transition control, not evidence of spontaneous live resolution.
Original requirements are unchanged across rounds. Normal production interpretation
is restored after the first round; no production files or persisted semantics change.
"""
import dataclasses
from pathlib import Path
from tools.m212_independent_acceptance import enc,freeze,save,generated_rows,fixture,fixed_case,CandidateProvider
from app.config import get_config
from app.runtime.engine import Engine
from app.runtime.context import ExecutionContext
from app.observe.trace import Trace
from app.contracts.models import Goal,UserRequirement,PlannerNeed,SemanticInterpretation,AnalysisRequest
from app.capabilities.analytics.tool import LocalAnalyticsTool
from app.capabilities.analytics.compiler import SQLCompiler
from app.verification.judge import Judge
import app.capabilities.analytics.conformance as module

def main():
    freeze();catalog,ex=fixture(generated_rows());results=[]
    cfg=dataclasses.replace(get_config(),llm=dataclasses.replace(get_config().llm,api_key=''),max_rounds=3,runtime_dir=Path('/tmp/m212-independent-staged'))
    original=module.constraint_coverage
    for name,phrase in [('A','against lefties'),('B','in night games'),('C','when the score is level')]:
        current_round=[0];c=fixed_case();c['inputs']['bound_user_intent']+=' '+phrase
        c['inputs']['analytical_requirements'].append(dict(id='original_clause',kind='CONSTRAINT',source='user',value=phrase,statement=phrase,original_intent=phrase,required=True))
        if name=='A':c['raw']['filters']=[dict(op='COMPARE',field='p_throws',comparator='=',value='L')]
        engine=Engine(cfg);tool=LocalAnalyticsTool(ex,CandidateProvider(c['raw']));tool.catalog=catalog;tool.compiler=SQLCompiler(catalog);engine.registry.register(tool)
        goal=Goal(raw_query=c['inputs']['bound_user_intent'],statement='Report metrics',requirements=[UserRequirement(**r) for r in c['inputs']['analytical_requirements']])
        semantic=SemanticInterpretation(raw_query=goal.raw_query,understanding=goal.raw_query,analysis_requests=[AnalysisRequest(text=goal.raw_query,objective=goal.raw_query)])
        plans=[];contracts=[]
        def plan(**kw):
            current_round[0]=kw['round_index'];plans.append({k:enc(kw.get(k)) for k in ['goal','round_index','outcomes','feedback']})
            return [PlannerNeed(id='r'+str(current_round[0]),capability='local_analytics',objective='Report batter plate appearances',requires=[r.id for r in goal.requirements],constraints={'season':2025,'game_population':'REGULAR_SEASON'})],'bounded retry',[]
        engine.planner.plan=plan
        def coverage(value,*a,**kw):
            result=original(value,*a,**kw)
            if name in {'A','B'} and current_round[0]==0 and isinstance(value,str) and phrase in value:
                result={**result,'state':'PARTIAL','unsupported':[],'unresolved':[phrase],
                    'clause_states':[dict(clause=value,clause_index=0,state='TEMPORARILY_UNRESOLVED',detail=phrase,attempts=[dict(resolver='acceptance.transient_first_pass',input=phrase,outcome='temporarily_unresolved',learned='')]) ]}
            return result
        original_execute=tool.execute
        def execute(req,ctx=None):
            contracts.append(enc(module.requested_contract(tool._intent(req),req.inputs,[])))
            return original_execute(req,ctx)
        tool.execute=execute
        try:
            module.constraint_coverage=coverage
            ctx=ExecutionContext(goal=goal,conversation=engine.conversation.get('stage'+name),config=cfg,llm=engine.llm,clock=engine.clock,trace=Trace('off'))
            needs,outcomes,arts,rej,notes=engine._run_plan(ctx,semantic,Trace('off'))
            judge=Judge().evaluate(goal,arts,needs)
            results.append(dict(id=name,first_pass_injected=name!='C',original_text=phrase,requirements=enc(goal.requirements),plans=plans,contracts=contracts,outcomes=enc(outcomes),artifacts=enc(arts),judge=enc(judge)))
        finally:module.constraint_coverage=original
    save('lifecycle_staged.json',results);freeze()
    print([(x['id'],len(x['plans']),x['judge']['goal_status']) for x in results])

if __name__=='__main__':main()
