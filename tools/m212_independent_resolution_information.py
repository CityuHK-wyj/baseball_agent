#!/usr/bin/env python3
"""Native unresolved -> understood after explicit, authoritative role information.

No production resolver is replaced. The acceptance input supplies a user
clarification between planning rounds, preserving all original Requirements.
"""
import dataclasses
from pathlib import Path
from tools.m212_independent_acceptance import freeze,save,enc,generated_rows,fixture,fixed_case,CandidateProvider,oracle,same_rows
from app.config import get_config
from app.runtime.engine import Engine
from app.runtime.context import ExecutionContext
from app.observe.trace import Trace
from app.contracts.models import Goal,UserRequirement,PlannerNeed,SemanticInterpretation,AnalysisRequest
from app.capabilities.analytics.tool import LocalAnalyticsTool
from app.capabilities.analytics.compiler import SQLCompiler
from app.capabilities.analytics.conformance import requested_contract
from app.verification.judge import Judge

def main():
    freeze();rows=generated_rows();catalog,ex=fixture(rows)
    c=fixed_case(role='PITCHER',metric='pitches',group=['pitcher_id'],limit=4,order=[('pitches',True),('pitcher_id',False)])
    text='Report top 4 by pitches'
    reqs=[r for r in c['inputs']['analytical_requirements'] if r['id']!='dimensions']
    for r in reqs:
        r['original_intent']=text
        if r['kind']=='ANALYSIS':r['value']={'objective':text};r['statement']=text
    goal=Goal(raw_query=text,statement=text,requirements=[UserRequirement(**r) for r in reqs])
    semantic=SemanticInterpretation(raw_query=text,understanding=text,analysis_requests=[AnalysisRequest(text=text,objective=text)])
    cfg=dataclasses.replace(get_config(),llm=dataclasses.replace(get_config().llm,api_key=''),max_rounds=3,runtime_dir=Path('/tmp/m212-independent-new-information'))
    engine=Engine(cfg);tool=LocalAnalyticsTool(ex,CandidateProvider(c['raw']));tool.catalog=catalog;tool.compiler=SQLCompiler(catalog);engine.registry.register(tool)
    plans=[];contracts=[];clarification_text='I mean pitchers, not batters.'
    def plan(**kw):
        if kw['round_index']==1:
            goal.requirements.append(UserRequirement(id='user_role_clarification',kind='CONSTRAINT',source='clarification',value={'analytical_role':'PITCHER'},statement=clarification_text,original_intent=clarification_text))
        plans.append(dict(round=kw['round_index'],goal=enc(goal),outcomes=enc(kw['outcomes'])))
        return [PlannerNeed(id='information'+str(kw['round_index']),capability='local_analytics',objective=text,requires=[r.id for r in goal.requirements],constraints={'season':2025,'game_population':'REGULAR_SEASON'})],'bounded information control',[]
    engine.planner.plan=plan;original=tool.execute
    def execute(req,ctx=None):
        contracts.append(enc(requested_contract(tool._intent(req),req.inputs,[])));return original(req,ctx)
    tool.execute=execute
    ctx=ExecutionContext(goal=goal,conversation=engine.conversation.get('new_information'),config=cfg,llm=engine.llm,clock=engine.clock,trace=Trace('off'))
    needs,outcomes,arts,rejected,notes=engine._run_plan(ctx,semantic,Trace('off'));judge=Judge().evaluate(goal,arts,needs)
    expected=oracle(rows,c['spec']);actual=[a.structured_data['rows'] for a in arts if a.kind=='DATABASE_RESULT']
    save('lifecycle_new_information.json',dict(original_text=text,clarification_text=clarification_text,initial_requirement_ids=[r['id'] for r in reqs],goal=enc(goal),plans=plans,contracts=contracts,outcomes=enc(outcomes),artifacts=enc(arts),judge=enc(judge),independent_expected=expected,numerical_correct=bool(actual) and all(same_rows(x,expected) for x in actual)))
    freeze();print('native resolution',len(plans),bool(contracts[0]['unresolved_obligations']),judge.goal_status.value, bool(actual) and all(same_rows(x,expected) for x in actual))

if __name__=='__main__':main()
