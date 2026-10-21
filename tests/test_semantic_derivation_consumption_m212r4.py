"""R4 fresh semantic proof, conservation and lifecycle regressions."""
import copy
import itertools
import json
import random

import pytest

from app.capabilities.analytics.authority import GOVERNED_DERIVATIONS, resolve_authority
from app.capabilities.analytics.conformance import constraint_coverage, requested_contract
from app.capabilities.analytics.measures import MEASURES
from app.contracts.models import UserRequirement, RequirementKind
from tools.requirement_ancestry_fixture import Goal, root_user_requirements


def req(kind,value,**kw):
    return root_user_requirements([dict(id='parent',kind=kind,value=value,statement=str(value),source='user',**kw)], str(value))[0]


def derived(kind,value,derivation,parent='parent',**kw):
    return dict(id='child',kind=kind,value=value,statement=str(value),source='inferred',
        parent_requirement_ids=[parent],derivation_kind=derivation,derivation_proof='claimed proof',**kw)


def admitted(parent,child,**kw):
    return 'child' in {r['id'] for r in resolve_authority([parent,child],**kw)[0]}


@pytest.mark.parametrize('derivation',sorted(GOVERNED_DERIVATIONS))
@pytest.mark.parametrize('value',[
    {'filters':[{'op':'COMPARE','field':'inning','comparator':'=','value':11}]},
    {'analytical_role':'PITCHER'}, {'date_range':{'start':'2020-04-03','end':'2020-05-01'}},
    {'group_by':['stand']}, {'qualification':{'basis':'PA','minimum':187}}, {'limit':8},
])
def test_parent_metadata_does_not_prove_unrelated_child(derivation,value):
    assert not admitted(req('ANALYSIS',{'objective':'Report batter walks in 2025'}),derived('CONSTRAINT',value,derivation))


@pytest.mark.parametrize('kind,value', [('MEASURE',{'key':'walks'}),('ANALYSIS',{'objective':'Report batter walks'})])
def test_measure_or_analysis_cannot_authorize_unasked_inning(kind,value):
    assert not admitted(req(kind,value),derived('CONSTRAINT',{'filters':[{'op':'COMPARE','field':'inning','comparator':'=','value':9}]},'schema_field_binding'))


@pytest.mark.parametrize('name,value',[
    ('full_eligible_row_population',{'filters':[]}),
    ('regular_season_competition_default',{'game_types':['R']}),
    ('scalar_result_default',{'group_by':[]}),
    ('no_qualification_default',{'qualification':None}),
    ('no_semantic_top_n_default',{'limit':None}),
])
def test_defaults_validate_identity_and_exact_payload(name,value):
    c=derived('CONSTRAINT',value,'domain_default');c['derivation_proof']=name;c['parent_requirement_ids']=[]
    assert admitted(req('ANALYSIS',{'objective':'walks'}),c)
    for field,payload in [('filters',[{'op':'COMPARE','field':'balls','comparator':'=','value':3}]),('limit',5),('group_by',['stand'])]:
        attack=copy.deepcopy(c);attack['value'][field]=payload
        assert not admitted(req('ANALYSIS',{'objective':'walks'}),attack)


@pytest.mark.parametrize('derivation,parent,child',[
    ('resolved_temporal_scope',req('SEASON',2023),('TIME',{'start':'2023-01-01','end':'2023-12-31'})),
    ('governed_population_codes',req('GAME_POPULATION','REGULAR_SEASON'),('CONSTRAINT',{'game_types':['R']})),
    ('governed_measure_definition',req('MEASURE',{'key':'walks'}),('MEASURE',{'key':'walks','definition':MEASURES['walks'].definition})),
    ('schema_field_binding',req('CONSTRAINT','with three balls'),('CONSTRAINT',{'filters':[{'op':'COMPARE','field':'balls','comparator':'=','value':3}]})),
    ('synonym_normalization',req('CONSTRAINT','against southpaws'),('CONSTRAINT','against left-handed pitchers')),
    ('interpreter_requirement_binding',req('ANALYSIS',{'objective':'Report batter walks in 2023'}),('SEASON',2023)),
    ('user_requirement_partition',req('CONSTRAINT','strikes = 2 AND inning >= 6'),('CONSTRAINT',{'filters':[{'op':'COMPARE','field':'inning','comparator':'>=','value':6}]})),
])
def test_positive_finite_derivations_and_altered_values(derivation,parent,child):
    c=derived(*child,derivation)
    assert admitted(parent,c), (derivation, resolve_authority([parent,c]))
    attack=copy.deepcopy(c)
    if attack['kind']=='TIME':attack['value']['end']='2023-12-30'
    elif attack['kind']=='SEASON':attack['value']=2024
    elif attack['kind']=='MEASURE':attack['value']['key']='strikeouts'
    elif isinstance(attack['value'],str):attack['value']+=' with a closed roof'
    else:attack['value']['limit']=3
    assert not admitted(parent,attack)


def test_identity_requires_independent_binder_evidence_and_preserves_role():
    parent=req('ENTITY',{'mention':'Verified Person','role':'BATTER'})
    c=derived('ENTITY',{'mention':'Verified Person','role':'BATTER','namespace':'MLBAM','canonical_id':12345},'canonical_entity_identity')
    assert not admitted(parent,c)
    evidence=[dict(mention='Verified Person',ids=[12345],role='BATTER',namespace='MLBAM')]
    assert admitted(parent,c,identity_evidence=evidence)
    for field,value in [('role','PITCHER'),('canonical_id',54321),('mention','Another Person')]:
        attack=copy.deepcopy(c);attack['value'][field]=value
        assert not admitted(parent,attack,identity_evidence=evidence)


@pytest.mark.parametrize('seed',range(41201,41233))
def test_generated_two_to_four_restrictions_are_conserved(seed):
    rng=random.Random(seed)
    forms=[('against lefties','p_throws','L'),('with two strikes','strikes',2),
           ('when balls are 3','balls',3),('第8局','inning',8)]
    rng.shuffle(forms);forms=forms[:rng.randint(2,4)]
    text=rng.choice([' with ',' while ',' when ',' during ',' AND ','；']).join(x[0] for x in forms)
    result=constraint_coverage(text,'BATTER','pa')
    filters=result['obligations'].get('filters',[])
    for _,field,value in forms:
        assert any(p['field']==field and p['value']==value for p in filters) or result['unresolved'],(text,result)
    assert result['state']=='COMPLETE',(text,result)
    assert all(not c['remaining_spans'] for c in result['clause_states'])


@pytest.mark.parametrize('remainder',['under a closed roof','with inning X','with L','while 雨','on 3','when televised nationally'])
def test_partial_synonym_resolution_cannot_understand_unknown_remainder(remainder):
    c=constraint_coverage('against lefties '+remainder,'BATTER','pa')
    assert c['state']!='COMPLETE'
    assert c['unresolved']
    assert c['obligations']['filters'][0]['field']=='p_throws'
    assert any(x['remaining_spans'] for x in c['clause_states'])


@pytest.mark.parametrize('wording',['Report 2025 batter plate appearances','Report 2025年 batter plate appearances','报告2025年打者打席'])
def test_wrong_year_cannot_pass_source_boundary(wording):
    requirements=[req('ANALYSIS',{'objective':'Report batter plate appearances'}),
        dict(id='s',kind='SEASON',value=2024,source='user'),dict(id='m',kind='MEASURE',value={'key':'pa'},source='user')]
    c=requested_contract({},dict(analytical_requirements=requirements,bound_user_intent=wording),[])
    assert c.issues
    assert 'user-source temporal clause has no authoritative bound scope' in c.issues


def test_attempt_history_survives_serialized_goal_and_replans_with_new_information():
    goal=Goal(statement='g',raw_query='Report top 5 by pitches',requirements=[UserRequirement(kind='ANALYSIS',statement='Report top 5 by pitches',value={'objective':'Report top 5 by pitches'})])
    def bind(g):
        return requested_contract({},dict(analytical_requirements=[r.model_dump(mode='json') for r in g.requirements],
            bound_user_intent=g.raw_query,_resolution_state=g.analytical_resolution_state,
            _semantic_deltas=[d.model_dump(mode='json') for d in g.semantic_deltas]),[])
    assert bind(goal).issues
    entries=copy.deepcopy(goal.analytical_resolution_state['attempts'])
    assert entries
    for _ in range(3):
        goal=Goal.model_validate_json(goal.model_dump_json());assert bind(goal).issues
    assert goal.analytical_resolution_state['attempts']==entries
    assert goal.analytical_resolution_state['duplicates_suppressed']>0
    from app.contracts.models import ClarificationDelta, RequirementProvenance
    from app.semantic.provenance import message_digest
    clarified = UserRequirement(kind='CONSTRAINT',statement='pitchers',source='clarification',value={'analytical_role':'PITCHER'})
    # Explicit synthetic backend-confirmed delta, not a source-label authority bypass.
    clarified.provenance = RequirementProvenance(
        origin='VALIDATED_CLARIFICATION', message_id=goal.id, bound_message=goal.raw_query,
        message_digest=message_digest(goal.raw_query), user_span=goal.raw_query,
        clarification_target_digest='fixture-role-target', clarification_answer='pitchers')
    goal.semantic_deltas.append(ClarificationDelta(
        action='REPLACE_INTERPRETATION', requirement_id=goal.requirements[0].id,
        candidate_id=clarified.id, source_span=goal.raw_query, source_goal_id=goal.id,
        user_answer='pitchers', target_digest='fixture-role-target'))
    goal.requirements.append(clarified)
    assert not bind(goal).issues
    assert goal.analytical_resolution_state['attempts'][:len(entries)]==entries
    # Clarification makes ranking resolvable without repeating the old role-less attempt.
    assert any(x['state']=='COMPLETE' for x in goal.analytical_resolution_state['current'].values())
    assert any(a['retry_reason']=='new authoritative context' for a in goal.analytical_resolution_state['attempts'])


def test_governed_measure_operation_graph_is_validated_not_its_label():
    parent=req('MEASURE',{'key':'walks'})
    graph={'aggregates':[{'name':'walks','function':'COUNT_IF','condition':{'op':'IN','field':'events','values':['walk','intent_walk']}}]}
    child=derived('MEASURE',{'key':'walks','operation_graph':graph},'governed_measure_definition')
    assert admitted(parent,child)
    changed=copy.deepcopy(child);changed['value']['operation_graph']['aggregates'][0]['condition']['values']=['walk']
    assert not admitted(parent,changed)
    changed=copy.deepcopy(child);changed['value']['operation_graph']['filters']=[{'op':'COMPARE','field':'inning','comparator':'=','value':5}]
    assert not admitted(parent,changed)


@pytest.mark.parametrize('source',['against lefties OR with two strikes','not against lefties','against lefties except inning 7'])
def test_unconsumed_parent_logic_cannot_prove_a_positive_filter(source):
    parent=req('CONSTRAINT',source)
    child=derived('CONSTRAINT',{'filters':[{'op':'COMPARE','field':'p_throws','comparator':'=','value':'L'}]},'schema_field_binding')
    assert not admitted(parent,child)
    assert constraint_coverage(source,'BATTER','pa')['state']!='COMPLETE'


@pytest.mark.parametrize('source',['rank top 6 batters by walks under a closed roof',
    'top 6 batters by walks for L hitters with two strikes on national television',
    'top 6 batters by walks; tie break by ascending MLBAM ID under a sealed roof'])
def test_ranking_and_tie_matchers_do_not_swallow_unknown_restrictions(source):
    c=constraint_coverage(source,'BATTER','walks')
    assert c['state']!='COMPLETE'
    assert c['unresolved']


def test_source_competition_cannot_be_changed_by_cjk_interpretation():
    requirements=[req('ANALYSIS',{'objective':'Report batter walks'}),dict(id='pop',kind='GAME_POPULATION',value='REGULAR_SEASON',source='user')]
    c=requested_contract({},dict(analytical_requirements=requirements,bound_user_intent='报告打者季后赛保送'),[])
    assert 'user-source competition scope is not fully represented' in c.issues


def test_cjk_left_right_contrast_is_a_split_not_contradictory_conjunction():
    c=constraint_coverage('面对左投和右投的打击率分别是多少','BATTER','avg',analysis=True)
    assert c['state']=='COMPLETE',c
    assert c['obligations']['group_by']==['p_throws']
    assert c['obligations']['filters']==[{'op':'IN','field':'p_throws','values':['L','R']}]
    extended=constraint_coverage('面对左投和右投的打击率分别是多少 with two strikes under a closed roof','BATTER','avg',analysis=True)
    assert extended['state']!='COMPLETE'
    assert any(f.get('field')=='strikes' for f in extended['obligations']['filters'])


@pytest.mark.parametrize('minimum',[173,283,419])
def test_cjk_qualification_plus_extremum_preserves_both(minimum):
    c=constraint_coverage(f'至少有{minimum}个打席的打者中，本垒打最多的是谁','BATTER','home runs',analysis=True)
    assert c['state']=='COMPLETE',c
    assert c['obligations']['qualification']=={'basis':'PA','minimum':minimum}
    assert c['obligations']['group_by']==['batter_id']
    assert c['obligations']['limit']==1


@pytest.mark.parametrize('field',['label','requested_text'])
def test_measure_surface_metadata_cannot_launder_source_residue(field):
    parent=req('MEASURE',{'key':'pa'})
    child=derived('MEASURE',{'key':'pa',field:'under a closed roof'},'governed_measure_definition')
    assert not admitted(parent,child)


def test_identity_verification_and_surface_cannot_be_forged():
    parent=req('ENTITY',{'mention':'Bound Person','canonical_id':189001,'role':'BATTER','identity_verified':False})
    child=derived('ENTITY',{'mention':'Bound Person','canonical_id':189001,'role':'BATTER','identity_verified':True},'canonical_entity_identity')
    assert not admitted(parent,child)
    parent['value']['identity_verified']=True
    assert admitted(parent,child)
    child['value']['canonical_name']='under a closed roof'
    assert not admitted(parent,child)


def test_invalid_interpreted_season_terminates_as_unproved_not_type_error():
    c=requested_contract({},dict(analytical_requirements=[req('SEASON','REGULAR_SEASON')],bound_user_intent='Report 2025年 batter walks'),[])
    assert c.issues
