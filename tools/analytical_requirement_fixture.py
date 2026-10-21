"""Explicit authoritative inputs for legacy synthetic analytics producers.

For tests/evaluation only. Uses the producer's declared input capability contract,
never candidate IR, execution output, or an Artifact requested receipt. New R2
attack generators must author Requirements independently and do not call this.
"""
import copy
from app.contracts.models import ToolRequest
from app.capabilities.analytics.measures import resolve_texts
from app.capabilities.analytics.canonical import is_general_performance
from tools.requirement_ancestry_fixture import root_user_requirements


def fixture_inputs(inputs, *, replace=False):
    inputs=copy.deepcopy(inputs)
    if inputs.get('analytical_requirements') and not replace:
        raw = inputs.get('bound_user_intent') or inputs.get('raw_query') or 'Calculate bound metrics'
        if not inputs.get('raw_query'): inputs['raw_query'] = raw
        root_user_requirements(inputs['analytical_requirements'], raw)
        return inputs
    intent=inputs.get('intent') or {};constraints=inputs.get('constraints') or {}
    reqs=[]
    def add(kind,value,statement=None):
        reqs.append(dict(id=f'fixture_{len(reqs)}',kind=kind,value=value,statement=statement or f'Bound {kind.lower()}',required=True,source='user'))
    for kind,field in [('SEASON','season'),('TIME','date_range'),('GAME_POPULATION','population')]:
        value=intent.get(field) or constraints.get('game_population' if field == 'population' else field)
        if value is not None: add(kind,value)
    role=intent.get('analytical_role') or inputs.get('entity_role')
    if role: add('CONSTRAINT',{'analytical_role':role})
    for item in inputs.get('entity_sets') or []:
        add('ENTITY',dict(mention='fixture subject',role=role or item.get('role'),canonical_ids=item.get('ids') or [],namespace=item.get('namespace') or 'MLBAM',identity_verified=True))
    if inputs.get('require_entities') and not any(r['kind']=='ENTITY' for r in reqs):
        add('ENTITY',dict(mention='fixture subject',role=role))
    measures=inputs.get('requested_measures') or [dict(key=m.key,derivable=m.derivable) for m in resolve_texts([intent.get('measure') or intent.get('objective') or ''])]
    for item in measures: add('MEASURE',{**item,'derivable':item.get('derivable',True)})
    bound={k:copy.deepcopy(v) for k,v in constraints.items() if k in {'filters','group_by','ordering','tie_break_ordering','limit','game_types'} and v is not None}
    if bound: add('CONSTRAINT',bound)
    if intent.get('qualification') is not None: add('CONSTRAINT',intent['qualification'])
    objective=intent.get('objective') or 'Calculate bound metrics'
    # Existing generated inputs sometimes use broad narrative shorthand while
    # separately declaring the precise shape; that shorthand is execution context.
    analysis_objective=objective if is_general_performance(intent) else 'Calculate bound metrics'
    add('ANALYSIS',{'objective':analysis_objective},analysis_objective)
    inputs['analytical_requirements']=reqs
    inputs['raw_query']='Calculate pitching metrics' if role == 'PITCHER' else 'Calculate batting metrics' if role == 'BATTER' else 'Calculate bound metrics'
    root_user_requirements(reqs, inputs.get('bound_user_intent') or inputs['raw_query'])
    return inputs


def analytical_request(*args,**kwargs):
    if kwargs.get('capability') == 'local_analytics': kwargs['inputs']=fixture_inputs(kwargs.get('inputs') or {})
    return ToolRequest(*args,**kwargs)
