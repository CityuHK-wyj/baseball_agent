import copy
import random
from types import SimpleNamespace
import pytest
from app.capabilities.analytics.compiler import SQLCompiler
from app.capabilities.analytics.conformance import requested_contract, attest, compare, constraint_coverage, bind_obligations
from app.capabilities.analytics.ir import AnalyticalQuery,validate_query
from app.capabilities.analytics.predicates import canonical_predicate
from app.capabilities.analytics.tool import LocalAnalyticsTool
from app.contracts.models import ToolRequest
from tools.m212r2_cases import case,need_attacks,DEV_PROVENANCE,DEV_COMPOUND,DEV_EQUIVALENCE
from tools.requirement_ancestry_fixture import root_user_requirements, rooted_case

case = rooted_case(case)


def contract(c,inputs=None):
    inputs=inputs or c['inputs'];tool=LocalAnalyticsTool(None,None)
    root_user_requirements(inputs['analytical_requirements'], inputs.get('bound_user_intent') or inputs['raw_query'], replace=True)
    request=ToolRequest(need_id='r2',capability='local_analytics',objective='Planner proposal',inputs=inputs)
    return requested_contract(tool._intent(request),inputs,[])


def executed(raw):
    q=validate_query(AnalyticalQuery.model_validate(raw));return attest(q,SQLCompiler().compile(q,'POSTGRES'))


@pytest.mark.parametrize('seed',DEV_PROVENANCE)
def test_precontract_metadata_never_owns_fact(seed):
    c=case(seed);wanted=contract(c)
    assert not wanted.issues and compare(wanted,executed(c['raw']),check_availability=False).status=='MATCH'
    for attack in need_attacks(c):
        observed=contract(c,attack['inputs'])
        assert observed==wanted, (seed,attack['name'],observed)
        # An ordering proposal can remain a harmless deterministic sort for an
        # unranked scalar/split. It cannot replace a mandatory primary ranking.
        expected='MATCH' if attack['name']=='Need_injected_ordering' and c['raw']['limit'] is None else 'MISMATCH'
        assert compare(observed,executed(attack['ir']),check_availability=False).status==expected
    assert all(v and all(p['requirement_id'] for p in v) for v in wanted.obligation_provenance.values())
    assert all(cov['state']=='COMPLETE' for cov in wanted.clause_coverage)


@pytest.mark.parametrize('seed',DEV_COMPOUND)
def test_compound_coverage_and_each_clause_drop(seed,monkeypatch):
    import app.capabilities.analytics.conformance as module
    c=case(seed);rng=random.Random(seed)
    minimum=rng.randint(6,67);field=rng.choice(['stand','pitch_type','p_throws']);members=['L','R'] if field!='pitch_type' else ['CU','ST']
    clauses=[f'at least {minimum} PITCHES',f'{field} IN ({repr(members[0])}, {repr(members[1])})','group by inning']
    rng.shuffle(clauses);text=' AND '.join(clauses)
    c['inputs']['analytical_requirements'][-1]['value']=text
    coverage=constraint_coverage(text,'BATTER','walks')
    assert coverage['state']=='COMPLETE',coverage
    # Role is still independently required, outside the compound adapter.
    c['inputs']['analytical_requirements'].append(dict(id='role',kind='CONSTRAINT',value={'analytical_role':c['raw']['analytical_role']},statement='role'))
    c['inputs']['raw_query']='Calculate pitching metrics' if c['raw']['analytical_role']=='PITCHER' else 'Calculate batting metrics'
    assert not contract(c).issues
    original=module.constraint_coverage
    for position in range(3):
        def drop(value,*args,**kwargs):
            result=copy.deepcopy(original(value,*args,**kwargs))
            if value==text:result['recognized']=[r for r in result['recognized'] if r.get('clause_index')!=position]
            return result
        with monkeypatch.context() as m:
            m.setattr(module,'constraint_coverage',drop)
            assert contract(c).issues
    for position in range(3):
        changed=clauses[:];changed[position]='unresolved governed condition'
        c['inputs']['analytical_requirements'][-2]['value']=' AND '.join(changed)
        assert contract(c).issues


@pytest.mark.parametrize('seed',DEV_EQUIVALENCE)
def test_finite_equivalence_and_domain(seed):
    rng=random.Random(seed);members=rng.sample(['ST','CU','FC','FF'],rng.randint(2,4))
    in_form=dict(op='IN',field='pitch_type',values=members)
    or_form=dict(op='OR',conditions=[dict(op='COMPARE',field='pitch_type',comparator='=',value=v) for v in reversed(members)])
    assert canonical_predicate(in_form)==canonical_predicate(or_form)
    n=rng.randint(1,9999)
    assert canonical_predicate(dict(op='COMPARE',field='season',value=n,comparator='='))==canonical_predicate(dict(op='COMPARE',field='season',value=float(n),comparator='='))
    for invalid in [True,n+.5,-1,10000]:
        with pytest.raises(ValueError): canonical_predicate(dict(op='COMPARE',field='season',value=invalid,comparator='='))
    null_or=dict(op='OR',conditions=[dict(op='IS_NULL',field='stand'),dict(op='COMPARE',field='stand',value='L',comparator='=')])
    assert canonical_predicate(null_or)[0]=='OR'


@pytest.mark.parametrize('wording',['按投手左右手分组','依投手的惯用手拆分','根据投手左右投分项','按左右投手分开','split by pitcher handedness','group results by p_throws'])
def test_supported_split_concept(wording):
    c=constraint_coverage(wording)
    assert c['state']=='COMPLETE' and c['obligations']['group_by']==['p_throws']


def test_missing_authority_is_typed():
    tool=LocalAnalyticsTool(None,None)
    result=tool.execute(ToolRequest(need_id='r2',capability='local_analytics',objective='Report value',inputs={'intent':{'season':2025},'constraints':{'filters':[]}}))
    assert result.artifact is None and result.outcome.code=='ANALYTICAL_OBLIGATION_UNPROVEN'


def test_adapter_cannot_drop_obligation_while_claiming_full_coverage(monkeypatch):
    import app.capabilities.analytics.conformance as module
    c=case(2128402030);original=module.constraint_coverage
    def polluted(*args,**kwargs):
        result=copy.deepcopy(original(*args,**kwargs))
        if 'group_by' in result['obligations']:result['obligations'].pop('group_by')
        return result
    monkeypatch.setattr(module,'constraint_coverage',polluted)
    assert contract(c).issues


def test_planner_only_authority_is_rejected_and_repeated_user_value_is_safe():
    c=case(2128401030);wanted=contract(c)
    ins=copy.deepcopy(c['inputs']);ins['constraints'].update(filters=c['raw']['filters'],group_by=c['raw']['group_by'],limit=c['raw']['limit'])
    assert contract(c,ins)==wanted
    ins['analytical_requirements'][-1]['source']='planner'
    assert contract(c,ins).issues


def test_generic_population_player_role_is_unproven():
    c=case(2128401031)
    c['inputs']['analytical_requirements']=[dict(id='generic',kind='ANALYSIS',value={'objective':'Report statistics for all players'},statement='Generic role')]
    assert contract(c).issues


@pytest.mark.parametrize('seed',range(2128402040,2128402048))
def test_bound_user_source_cannot_lose_compound_clause_in_normalization(seed):
    c=case(seed);c['inputs']['analytical_requirements'][-1]['value']={'analytical_role':c['raw']['analytical_role']}
    source="Report walks with at least 11 PITCHES AND balls >= 2 AND group by stand"
    c['inputs']['bound_user_intent']=source
    wanted=contract(c)
    assert not wanted.issues
    assert wanted.qualification=={'basis':'PITCHES','minimum':11}
    assert wanted.group_by==('stand',) and wanted.filters[0]['field']=='balls'
    assert any(p['source_clause']==source for p in wanted.obligation_provenance['filters'])


@pytest.mark.parametrize('seed',range(2128402050,2128402058))
def test_omitted_source_temporal_scope_stays_unproven(seed):
    c=case(seed);year=c['raw']['season']
    c['inputs']['bound_user_intent']=f'Report walks as a batter from {year}-05-01 through {year}-05-19 inclusive'
    c['inputs']['analytical_requirements']=[r for r in c['inputs']['analytical_requirements'] if r['kind'] not in {'TIME','SEASON'}]
    assert contract(c).issues


@pytest.mark.parametrize('raw',[dict(season=True),dict(limit=True),dict(qualification={'basis':'PA','minimum':True}),dict(entity_filters=[{'role':'BATTER','ids':[True]}])])
def test_boolean_never_becomes_an_integral_ir_scope(raw):
    with pytest.raises(ValueError):AnalyticalQuery.model_validate(raw)


@pytest.mark.parametrize('seed',range(2128401040,2128401048))
def test_receipt_rehash_cannot_rewrite_executed_population(seed):
    import datetime as dt
    from app.capabilities.analytics.conformance import SourceAvailability, digest, receipt_usable, scope_from_execution, ExecutionAttestation, RequestedAnalyticalContract
    from app.capabilities.analytics.executor import QueryResult
    from tools.evaluate_m212r import FixedIR
    c=case(seed)
    class Source:
        def availability(self,source,relation):return SourceAvailability(source=source,relation=relation,known=True,start=dt.date(2010,1,1),end=dt.date(2030,12,31),seasons=tuple(range(2010,2031)))
        def run(self,sql,source):return QueryResult([],[],0,source,0,'unit')
    artifact=LocalAnalyticsTool(Source(),FixedIR(c['raw'])).execute(ToolRequest(need_id='r2',capability='local_analytics',objective='Bound result',inputs=c['inputs'])).artifact
    assert artifact and receipt_usable(artifact)
    receipt=artifact.structured_data['execution_receipt']
    changed=ExecutionAttestation.model_validate(receipt['attestation']).model_copy(update={'filters':(dict(op='COMPARE',field='balls',comparator='=',value=1),)})
    receipt['attestation']=changed.model_dump(mode='json');receipt['attestation_digest']=digest(changed)
    requested=RequestedAnalyticalContract.model_validate(receipt['requested']).model_copy(update={'filters':changed.filters})
    receipt['requested']=requested.model_dump(mode='json');receipt['conformance']=compare(requested,changed).model_dump(mode='json')
    assert not receipt_usable(artifact)


@pytest.mark.parametrize('seed',range(2128401060,2128401068))
def test_direct_entity_need_metadata_cannot_redefine_bound_identity(seed):
    c=case(seed);role=c['raw']['analytical_role'];ident=3100000+seed%100
    req=dict(id='subject',kind='ENTITY',statement='Bound subject',value={'mention':'Independent subject','role':role,'canonical_id':ident,'identity_verified':True})
    c['inputs']['analytical_requirements'].append(req);c['inputs']['entity_sets']=[dict(role=role,ids=[ident])]
    wanted=contract(c);assert not wanted.issues and wanted.entities[0].ids==(ident,)
    bad=copy.deepcopy(c['inputs']);bad['entity_sets'][0]['ids']=[ident+1]
    assert contract(c,bad).issues
