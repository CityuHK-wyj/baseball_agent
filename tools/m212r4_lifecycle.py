"""Actual three-round Engine lifecycle and independent partial-result controls."""
import argparse
import copy
import dataclasses
import datetime as dt
import json
from pathlib import Path
from tools.m212_independent_acceptance import enc,fixture,CandidateProvider


def run(output):
    from app.config import get_config
    from app.runtime.engine import Engine
    from app.runtime.context import ExecutionContext
    from app.observe.trace import Trace
    from app.contracts.models import Goal,UserRequirement,PlannerNeed,SemanticInterpretation
    from app.capabilities.analytics.tool import LocalAnalyticsTool
    from app.capabilities.analytics.compiler import SQLCompiler
    from app.verification.judge import Judge
    from app.verification.propositions import build_propositions
    rows=[dict(game_date=dt.date(2025,4,9),game_type='R',batter_id=5100001+j%2,pitcher_id=5200001,game_pk=90001,
               events=event,p_throws='L',stand='R',inning=7,balls=2,strikes=1) for j,event in enumerate(['walk','walk','intent_walk','strikeout'])]
    catalog,executor=fixture(rows)
    cfg=dataclasses.replace(get_config(),llm=dataclasses.replace(get_config().llm,api_key=''),max_rounds=3,runtime_dir=Path('/tmp/m212r4-lifecycle'))
    reqs=[UserRequirement(id='s',kind='SEASON',statement='2025',value=2025),
          UserRequirement(id='p',kind='GAME_POPULATION',statement='regular season',value='REGULAR_SEASON'),
          UserRequirement(id='m',kind='MEASURE',statement='walks',value={'key':'walks','derivable':True})]
    raw=dict(relation='pitch',analytical_role='BATTER',season=2025,population='REGULAR_SEASON',
        aggregates=[dict(name='walks',function='COUNT_IF',condition=dict(op='IN',field='events',values=['walk','intent_walk']))])
    cases=[]
    for kind in ['persistent','clarified','independent','dependent']:
        engine=Engine(cfg)
        candidate=copy.deepcopy(raw)
        if kind in {'persistent','clarified'}:
            wording='Rank top 11 by walks'
            a=UserRequirement(id='a',kind='ANALYSIS',statement=wording,value={'objective':wording})
            goal=Goal(statement=wording,raw_query=wording,requirements=[*copy.deepcopy(reqs),a])
            candidate.update(group_by=['batter_id'],sort=[dict(name='walks',direction='DESC')],limit=11)
        else:
            wording='Count batter walks in 2025 regular season; Report batter pitches when the score is level'
            a=UserRequirement(id='a',kind='ANALYSIS',statement='Count batter walks',value={'objective':'Count batter walks'})
            b=UserRequirement(id='b',kind='ANALYSIS',statement='Report batter pitches when the score is level',value={'objective':'Report batter pitches when the score is level'})
            mp=UserRequirement(id='mp',kind='MEASURE',statement='pitches',value={'key':'pitches','derivable':True})
            goal=Goal(statement=wording,raw_query=wording,requirements=[*copy.deepcopy(reqs),a,b,mp])
        goal.propositions=build_propositions(goal)
        if kind=='dependent':
            next(p for p in goal.propositions if p.requirement_id=='a').depends_on=[next(p.id for p in goal.propositions if p.requirement_id=='b')]
        tool=LocalAnalyticsTool(executor,CandidateProvider(candidate));tool.catalog=catalog;tool.compiler=SQLCompiler(catalog);engine.registry.register(tool)
        plans=[]
        def plan(**kw):
            i=kw['round_index']
            if kind=='clarified' and i==1:
                # Simulates a real authoritative user clarification arriving between
                # rounds, explicitly NOT authority supplied by production Planner.
                goal.requirements.append(UserRequirement(id='clarification',kind='CONSTRAINT',source='clarification',statement='choose batters',value={'analytical_role':'BATTER'}))
                goal.propositions=build_propositions(goal)
            plans.append(dict(round=i,resolution=copy.deepcopy(goal.analytical_resolution_state),requirements=enc(goal.requirements)))
            if kind in {'independent','dependent'}:
                proposed=[]
                if i==0:proposed.append(PlannerNeed(id='supported',capability='local_analytics',objective='Count batter walks',criticality_hint='core',requires=['a','m','s','p']))
                proposed.append(PlannerNeed(id='pending'+str(i),capability='local_analytics',objective='Report batter pitches when the score is level',criticality_hint='core',requires=['b','mp','s','p']))
            else:
                proposed=[PlannerNeed(id='pending'+str(i),capability='local_analytics',objective=wording,criticality_hint='core',requires=[r.id for r in goal.requirements])]
            return proposed,'bounded operational retry',[]
        engine.planner.plan=plan
        ctx=ExecutionContext(goal=goal,conversation=engine.conversation.get(kind),config=cfg,llm=engine.llm,clock=engine.clock,trace=Trace('off'))
        needs,outcomes,artifacts,rejected,notes=engine._run_plan(ctx,SemanticInterpretation(raw_query=wording,understanding=wording),Trace('off'))
        projection=Judge().evaluate(goal,artifacts,needs)
        result=dict(case=kind,rounds=len(plans),plans=plans,goal=enc(goal),outcomes=enc(outcomes),artifacts=enc(artifacts),projection=enc(projection),
            persistent_entries=len(goal.analytical_resolution_state.get('attempts',[])),suppressed=goal.analytical_resolution_state.get('duplicates_suppressed',0))
        cases.append(result)
        Path(output).parent.mkdir(parents=True,exist_ok=True)
        Path(output).write_text(json.dumps(cases,indent=2,ensure_ascii=False,default=str)+'\n')
        if kind=='persistent':
            assert len(plans)==3 and result['suppressed']>0 and projection.goal_status.value=='LIMITED' and not artifacts
            assert all(a.get('retry_reason')=='initial resolution' for a in goal.analytical_resolution_state['attempts'])
        elif kind=='clarified':
            # The Engine budgets recovery by Need status: the first failed Need
            # remains terminal when this attacker proposes NEW IDs, so a third
            # round is expected even though the second already resolved the Goal.
            assert len(plans)==3 and artifacts and projection.goal_status.value=='COMPLETE'
            assert any(a.get('retry_reason')=='new authoritative context' for a in goal.analytical_resolution_state['attempts'])
            assert artifacts[0].structured_data['rows'][0]['walks']==2  # independent events above, batter 5100001
        elif kind=='independent':
            assert projection.goal_status.value=='LIMITED' and projection.claims
            assert any(a.kind=='DATABASE_RESULT' and a.structured_data['rows']==[{'walks':3}] for a in artifacts)
            assert any(p.requirement_id=='a' and p.coverage.value=='SATISFIED' for p in projection.propositions)
        else:
            assert not projection.claims and projection.goal_status.value!='COMPLETE'
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(cases,indent=2,ensure_ascii=False,default=str)+'\n')
    print([(c['case'],c['rounds'],c['projection']['goal_status'],c['persistent_entries'],c['suppressed']) for c in cases])


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('output');a=p.parse_args();run(a.output)
