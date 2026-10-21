from tools.analytical_requirement_fixture import fixture_inputs
import copy
import datetime as dt
from types import SimpleNamespace
import pytest
from app.capabilities.analytics.compiler import SQLCompiler
from app.capabilities.analytics.conformance import (attest, compare, requested_contract, SourceAvailability, digest, receipt_usable)
from app.capabilities.analytics.ir import AnalyticalQuery, validate_query
from app.capabilities.analytics.measures import resolve_texts, declare_measures
from app.capabilities.analytics.tool import LocalAnalyticsTool
from app.capabilities.analytics.executor import QueryResult
from app.contracts.models import ToolRequest, UserRequirement, RequirementKind, Artifact
from tools.requirement_ancestry_fixture import Goal
from app.verification.judge import Judge
from tools.m212_cases import case, mutations, DEV_SEEDS

def contracts(c,q=None):
    c = {**c, "inputs":fixture_inputs(c["inputs"],replace=True)}
    request=ToolRequest(need_id='test',capability='local_analytics',objective='Report batting average',inputs=c['inputs'])
    tool=LocalAnalyticsTool(None,None)
    intent=tool._intent(request)
    wanted=requested_contract(intent,request.inputs,tool._requested_measures(request,intent))
    query=validate_query(AnalyticalQuery.model_validate(q or c['raw']))
    compiled=SQLCompiler().compile(query,'POSTGRES')
    return wanted,attest(query,compiled)

@pytest.mark.parametrize('seed',DEV_SEEDS)
def test_match_and_canonical_entity_population_encodings(seed):
    c=case(seed);want,execution=contracts(c)
    assert compare(want,execution,check_availability=False).status=='MATCH'
    raw=copy.deepcopy(c['raw']);raw['game_types']=[c['code']];raw['population']=None
    raw['entity_filters'][0]['ids']=[str(x) for x in raw['entity_filters'][0]['ids']]
    assert compare(want,contracts(c,raw)[1],check_availability=False).status=='MATCH'

@pytest.mark.parametrize('seed',DEV_SEEDS)
@pytest.mark.parametrize('mutation',list(mutations(case(21212000))))
def test_single_mutation_invalidates_conformance(seed,mutation):
    c=case(seed);wanted,execution=contracts(c,mutations(c)[mutation])
    assert compare(wanted,execution,check_availability=False).status=='MISMATCH', (seed,mutation)

@pytest.mark.parametrize('seed',DEV_SEEDS)
def test_alias_independence_units_and_base_graph(seed):
    c=case(seed);want,execution=contracts(c)
    raw=copy.deepcopy(c['raw']);raw['derived'][0]['name']='arbitrary_alias';raw['sort'][0]['name']='arbitrary_alias'
    renamed=contracts(c,raw)[1]
    assert compare(want,renamed,check_availability=False).status=='MATCH'
    raw['derived'][0]['op']='PCT'
    assert compare(want,contracts(c,raw)[1],check_availability=False).status=='MISMATCH'
    # Exact governed hard-hit threshold and percent scale only.
    for threshold,scale,expected in [(95,'PCT',True),(96,'PCT',False),(95,'SAFE_DIV',False),(94,'PCT',False)]:
        q=AnalyticalQuery.model_validate(dict(aggregates=[dict(name='a',function='COUNT_IF',condition=dict(op='COMPARE',field='launch_speed',comparator='>=',value=threshold)),dict(name='b',function='COUNT_NON_NULL',field='launch_speed')],derived=[dict(name='hard_hit_pct',op=scale,left=dict(kind='aggregate',name='a'),right=dict(kind='aggregate',name='b'))]))
        assert any(m.key=='hard_hit_pct' and m.verified for m in declare_measures(validate_query(q)))==expected

@pytest.mark.parametrize('seed',DEV_SEEDS)
def test_source_coverage_and_unexecuted_receipt(seed):
    c=case(seed);wanted,a=contracts(c)
    start=dt.date.fromisoformat(c['raw']['date_range']['start']);end=dt.date.fromisoformat(c['raw']['date_range']['end'])
    for offset,status in [(0,'MATCH'),(2,'PARTIAL'),(15,'UNKNOWN')]:
        available=SourceAvailability(source='POSTGRES',relation='pitch',known=True,start=start+dt.timedelta(days=offset),end=end+dt.timedelta(days=16),seasons=(start.year,))
        assert compare(wanted,a.model_copy(update={'availability':available})).status==status

class FixedIR:
    configured=True
    def __init__(self,raw):self.raw=raw
    def complete_json(self,**kw):return SimpleNamespace(data=copy.deepcopy(self.raw))

class Source:
    def __init__(self,c):self.c=c;self.calls=[]
    def run(self,sql,source):
        self.calls.append(sql)
        return QueryResult([], [{'volume':10,'numerator':3,'denominator':9,'velocity':90.,'rate_value':1/3,'__execution_observations':12}],1,source,0,'fake')
    def availability(self,source,relation):
        return SourceAvailability(source=source,relation=relation,known=True,start=dt.date(2015,1,1),end=dt.date(2026,10,1),seasons=tuple(range(2015,2026)))

@pytest.mark.parametrize('mutation',list(mutations(case(21212003))))
def test_drift_cannot_reach_execution_or_complete(mutation):
    c=case(21212003);source=Source(c);tool=LocalAnalyticsTool(source,FixedIR(mutations(c)[mutation]))
    result=tool.execute(ToolRequest(need_id='test',capability='local_analytics',objective='Report batting average',inputs=c['inputs']))
    assert result.outcome.code=='ANALYTICAL_NONCONFORMANCE'
    assert not source.calls and result.artifact is None
    goal=Goal(statement='AVG',raw_query='AVG',requirements=[UserRequirement(kind=RequirementKind.MEASURE,statement='AVG',value={'key':'avg','derivable':True})])
    assert Judge().evaluate(goal,[],[]).goal_status.value!='COMPLETE'

def test_actual_scope_lineage_and_tampering():
    c=case(21212003);source=Source(c);tool=LocalAnalyticsTool(source,FixedIR(c['raw']))
    result=tool.execute(ToolRequest(need_id='test',capability='local_analytics',objective='Report batting average',inputs=c['inputs']))
    artifact=result.artifact
    assert artifact and artifact.status=='ACCEPTED'
    assert receipt_usable(artifact)
    assert not artifact.actual_scope.entity_names
    assert artifact.lineage and len(artifact.structured_data['execution_receipt']['attestation']['ir_digest'])==64
    artifact.structured_data['rows'][0]['rate_value']=1.0
    assert not receipt_usable(artifact)

def test_no_receipt_no_fact():
    goal=Goal(statement='AVG',raw_query='AVG',requirements=[UserRequirement(kind=RequirementKind.MEASURE,statement='AVG',value={'key':'avg','derivable':True})])
    art=Artifact(kind='DATABASE_RESULT',structured_data={'row_count':1,'rows':[{'avg':1}], 'actual_measures':[{'key':'avg','verified':True}]})
    assert Judge().evaluate(goal,[art],[]).goal_status.value!='COMPLETE'

@pytest.mark.parametrize('seed',DEV_SEEDS)
def test_bound_namespace_and_invalid_ids(seed):
    c=case(seed)
    for invalid in [False,7.3,'ambiguous']:
        c2=copy.deepcopy(c);c2['inputs']['entity_sets'][0]['ids']=[invalid]
        wanted,executed=contracts(c2)
        assert compare(wanted,executed,check_availability=False).status=='MISMATCH'
    c2=copy.deepcopy(c);c2['inputs']['entity_sets'][0]['namespace']='OTHER'
    wanted,executed=contracts(c2)
    assert compare(wanted,executed,check_availability=False).status=='MISMATCH'

@pytest.mark.parametrize('seed',DEV_SEEDS)
def test_date_predicate_encoding_and_executed_scope_separation(seed):
    c=case(seed);wanted,original=contracts(c)
    q=copy.deepcopy(c['raw']);dates=q.pop('date_range')
    q['filters']=[dict(op='BETWEEN',field='game_date',low=dates['start'],high=dates['end'])]
    actual=contracts(c,q)[1]
    assert compare(wanted,actual,check_availability=False).status=='MATCH'
    shifted=contracts(c,mutations(c)['time_shift'])[1]
    assert shifted.date_range.start != wanted.date_range.start
    assert original.date_range.start == wanted.date_range.start


def test_normalized_recomputation_cannot_inherit_verified_order():
    from app.capabilities.analytics.tool import canonicalize_metrics
    c=case(21212003);tool=LocalAnalyticsTool(Source(c),FixedIR(c['raw']))
    artifact=tool.execute(ToolRequest(need_id='test',capability='local_analytics',objective='Report batting average',inputs=c['inputs'])).artifact
    a=artifact.structured_data['execution_receipt']['attestation']
    assert a['ordering'][0]['metric']=='avg'
    assert 'event:hits' in a['ordering'][0]['operation']
    artifact.structured_data['rows'][0].update(avg=0.,hits=3,at_bats=6)
    canonicalize_metrics(artifact.structured_data['rows'])
    assert not receipt_usable(artifact)


def test_receipt_partial_cannot_complete_and_valid_receipt_can():
    c=case(21212003)
    c['inputs']['require_entities']=False;c['inputs']['entity_sets']=[];c['raw']['entity_filters']=[]
    c['inputs']=fixture_inputs(c['inputs'],replace=True)
    source=Source(c);tool=LocalAnalyticsTool(source,FixedIR(c['raw']))
    goal=Goal(statement='AVG',raw_query=c['inputs']['raw_query'],requirements=[UserRequirement.model_validate(r) for r in c['inputs']['analytical_requirements']])
    request=ToolRequest(need_id='test',capability='local_analytics',objective='Report batting average',inputs=c['inputs'])
    valid=tool.execute(request).artifact
    assert Judge().evaluate(copy.deepcopy(goal),[valid],[]).goal_status.value=='COMPLETE'
    source.availability=lambda source,relation:SourceAvailability(source=source,relation=relation,known=True,start=dt.date.fromisoformat(c['raw']['date_range']['start'])+dt.timedelta(days=1),end=dt.date.fromisoformat(c['raw']['date_range']['end']))
    partial=tool.execute(request).artifact
    assert partial.status=='PARTIAL' and receipt_usable(partial)
    assert Judge().evaluate(copy.deepcopy(goal),[partial],[]).goal_status.value=='LIMITED'


def test_consumer_role_is_independent_of_identity_export_hint():
    from app.contracts.models import Export, InputBinding
    c=case(21212003);role=c['inputs']['entity_role'];opposite='PITCHER' if role=='BATTER' else 'BATTER'
    request=ToolRequest(need_id='test',capability='local_analytics',objective='Report batting average',inputs={**c['inputs'],'entity_sets':[]},bindings=[InputBinding(need_id='test',export_id='identity',export_type='PLAYER_ID_SET',artifact_id='entity',requirement='PLAYER_ID_SET',source_need_id='n')])
    export=Export(id='identity',type='PLAYER_ID_SET',payload={'ids':c['raw']['entity_filters'][0]['ids'],'role':opposite},contract={'namespace':'MLBAM'})
    ctx=SimpleNamespace(find_export=lambda _:export)
    result=LocalAnalyticsTool(Source(c),FixedIR(c['raw'])).execute(request,ctx)
    assert result.artifact and result.artifact.status=='ACCEPTED'
    assert result.artifact.structured_data['execution_receipt']['attestation']['entities'][0]['role']==role


def test_existing_normalized_nonqualification_constraints_attest_execution():
    from app.capabilities.analytics.conformance import bind_obligations, constraint_obligations
    intent,issues=bind_obligations({'entities':[],'qualification':'top 7 by home runs, tie-break by ascending MLBAM ID'}, fixture_inputs({'entity_role':'BATTER','analytical_requirements':[{'kind':'CONSTRAINT','value':{'analytical_role':'BATTER'}},{'kind':'CONSTRAINT','value':'top 7 by home runs'},{'kind':'CONSTRAINT','value':'tie-break by ascending MLBAM ID'}]}))
    assert not issues and intent['qualification'] is None
    assert intent['execution_constraints']=={'group_by':['batter_id'],'limit':7,'ordering':[{'metric':'home_runs','direction':'DESC'},{'field':'batter_id','direction':'ASC'}]}
    assert constraint_obligations('split by opposing pitcher handedness')=={'group_by':['p_throws']}
    assert constraint_obligations('qualified hitters') is None

@pytest.mark.parametrize('seed',DEV_SEEDS)
def test_postseason_subsets_are_not_equivalent(seed):
    c=case(seed);c['inputs']['intent']['population']='POSTSEASON';c['raw']['population']='POSTSEASON'
    wanted,actual=contracts(c)
    assert compare(wanted,actual,check_availability=False).status=='MATCH'
    c['raw']['population']=None;c['raw']['game_types']=['F','D','L']
    assert compare(wanted,contracts(c)[1],check_availability=False).status=='MISMATCH'
