"""Independent Stage A semantic attacker; no unit-test/audit case generators.

Outputs operational evidence, not provider inputs or numerical Artifact oracles.
"""
import argparse
import copy
import hashlib
import itertools
import json
import random
import sys
from pathlib import Path


def run(seed):
    from app.capabilities.analytics.authority import resolve_authority, GOVERNED_DERIVATIONS
    from app.capabilities.analytics.conformance import constraint_coverage, requested_contract
    rng=random.Random(seed)
    attacks=[]
    def add(family,input,contained,observed):
        attacks.append(dict(family=family,input=input,contained=contained,observed=observed))
    parents=[dict(id='root',kind='MEASURE',value={'key':'doubles'},source='user'),
        dict(id='root',kind='ANALYSIS',value={'objective':'Report batter doubles in 2022'},source='user'),
        dict(id='root',kind='SEASON',value=2022,source='user'),
        dict(id='root',kind='CONSTRAINT',value='against righties',source='user')]
    payloads=[{'filters':[dict(op='COMPARE',field=f,comparator='=',value=v)]} for f,v in
              [('inning',rng.randint(4,12)),('strikes',rng.choice([0,1,2])),('balls',rng.randint(0,3)),('stand','L')]]
    payloads += [{'group_by':['pitch_type']},{'qualification':{'basis':'BBE','minimum':rng.randint(31,231)}},
                 {'limit':rng.randint(2,13)},{'analytical_role':'PITCHER'},
                 {'date_range':{'start':'2021-06-03','end':'2021-07-19'}}]
    for parent,derivation,payload in itertools.product(parents,sorted(GOVERNED_DERIVATIONS),payloads):
        child=dict(id='derived',kind='CONSTRAINT',source='inferred',value=payload,
            parent_requirement_ids=['root'],derivation_kind=derivation,derivation_proof='This is certainly a semantic proof.')
        good,bad,issues=resolve_authority([parent,child])
        add('authority',dict(parent=parent,child=child),'derived' not in {r['id'] for r in good},issues)
    defaults=['full_eligible_row_population','regular_season_competition_default','scalar_result_default','no_qualification_default','no_semantic_top_n_default']
    for name,payload in itertools.product(defaults,payloads):
        child=dict(id='derived',kind='CONSTRAINT',source='inferred',value=payload,
            derivation_kind='domain_default',derivation_proof=name)
        good,bad,issues=resolve_authority([child])
        add('default',child,not good,issues)
    # Independently authored modifier combinations; unknown restrictions must remain
    # blocking even if three other modifiers happen to match.
    forms=[('against southpaws',('p_throws','L')),('with 1 strike',('strikes',1)),
           ('when balls are 2',('balls',2)),('第9局',('inning',9)),('for R batters',('stand','R'))]
    unknown=['inside a sealed roof','on neutral fields','when ahead by 4','after ceremonial introductions']
    for _ in range(64):
        chosen=rng.sample(forms,rng.randint(2,4));rng.shuffle(chosen)
        text=rng.choice([' while ',' when ',' with ',' AND ','；',' during ']).join(x[0] for x in chosen)
        c=constraint_coverage(text,'BATTER','pa')
        missing=[p for _,p in chosen if not any((f.get('field'),f.get('value'))==p for f in c['obligations'].get('filters',[]))]
        add('consumption',text,not missing or c['state']!='COMPLETE',dict(missing=missing,coverage=c))
        extra=rng.choice(unknown)
        c=constraint_coverage(text+' '+extra,'BATTER','pa')
        add('residue',text+' '+extra,c['state']!='COMPLETE' and bool(c['unresolved'] or c['unsupported']),c)
    for text in ['for L hitters','for R pitchers','仅L打者','L打者','第9局','两好球','when balls are 0']:
        c=constraint_coverage(text,'BATTER','pa')
        add('short_cjk',text,bool(c['obligations'].get('filters')) or c['state']!='COMPLETE',c)
    for text in ['Report 2022年 batter doubles','报告2022年MLB打者二垒安打','Report 2022 batter doubles']:
        reqs=[dict(id='a',kind='ANALYSIS',source='user',value={'objective':'Report batter doubles'}),
              dict(id='s',kind='SEASON',source='user',value=2021)]
        c=requested_contract({},dict(analytical_requirements=reqs,bound_user_intent=text),[])
        add('temporal',text,bool(c.issues),c.model_dump(mode='json'))
    for text in ['top 5 batters by doubles inside a sealed roof','rank top 5 batters by doubles with 1 strike under a sealed roof',
                 'top 5 batters by doubles; tie break by ascending MLBAM ID on national television',
                 'against southpaws OR with 1 strike','not against righties']:
        c=constraint_coverage(text,'BATTER','doubles')
        add('ranking_logic',text,c['state']!='COMPLETE',c)
    from app.contracts.models import Goal,UserRequirement
    goal=Goal(statement='attack',raw_query='Rank top 11 by walks',requirements=[UserRequirement(id='original',kind='ANALYSIS',statement='Rank top 11 by walks',value={'objective':'Rank top 11 by walks'})])
    if hasattr(goal,'analytical_resolution_state'):
        def bind(g):return requested_contract({},dict(analytical_requirements=[r.model_dump(mode='json') for r in g.requirements],bound_user_intent=g.raw_query,_resolution_state=g.analytical_resolution_state),[])
        first=bind(goal);before=copy.deepcopy(goal.analytical_resolution_state['attempts'])
        for _ in range(3):
            goal=Goal.model_validate_json(goal.model_dump_json());bind(goal)
        add('history','three serialized replans',bool(before) and before==goal.analytical_resolution_state['attempts'] and goal.analytical_resolution_state.get('duplicates_suppressed',0)>0,copy.deepcopy(goal.analytical_resolution_state))
        goal.requirements.append(UserRequirement(id='new',kind='CONSTRAINT',statement='select batters',source='clarification',value={'analytical_role':'BATTER'}))
        later=bind(goal)
        add('new_information','authoritative batter clarification',not later.issues and any(a['retry_reason']=='new authoritative context' for a in goal.analytical_resolution_state['attempts']),copy.deepcopy(goal.analytical_resolution_state))
    else:
        add('history','three serialized replans',False,'lifecycle ledger absent')
        add('new_information','authoritative clarification',False,'persistent ledger absent')
    return dict(seed=seed,total=len(attacks),failures=sum(not a['contained'] for a in attacks),
                families={family:dict(total=sum(a['family']==family for a in attacks),failures=sum(a['family']==family and not a['contained'] for a in attacks)) for family in {a['family'] for a in attacks}},attacks=attacks)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('output');p.add_argument('--seed',type=int,default=419331);p.add_argument('--production-root');a=p.parse_args()
    sys.path.insert(0,a.production_root or str(Path(__file__).resolve().parents[1]))
    result=run(a.seed);Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='attacks'}))
    if result['failures']:sys.exit(1)
