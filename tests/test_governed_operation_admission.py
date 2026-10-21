"""Adversarial production admission and reverse-provenance controls."""
from copy import deepcopy
import pytest
from app.contracts.models import AnalysisRequest, SemanticInterpretation, RelationSemantics, RelationKind
from app.contracts.operations import OperationContract
from app.semantic.goals import RequirementBuilder
from app.verification.governed_operations import OperationAdmission
from tests.governed_operation_fixture import compute, goal, project, roots


@pytest.mark.parametrize('kind,parameters,field,value',[
    ('SUBTRACT',{},'difference',20),('SUM',{},'total',58),('RATIO',{},'ratio',39/19),
    ('MEAN',{},'average',29),('AGGREGATE',{'reducer':'SUM'},'aggregate',58),
    ('RANK',{'direction':'DESC'},'ranking',[
        {'operand':0,'value':39,'rank':1},{'operand':1,'value':19,'rank':2}]),
])
def test_positive_production_trace(kind,parameters,field,value):
    g,artifact,source = compute(goal(kind,parameters=parameters,field=field))
    assert artifact.structured_data['rows'] == [{field:value}]
    projection = project(g,artifact,source)
    assert projection.goal_status.value == 'COMPLETE'
    assert projection.claims
    assessment = next(a for a in projection.artifact_assessments if a.artifact_id == artifact.id)
    proof = assessment.derived_proofs[0]
    assert proof['contract'] == next(r.operation_contract for r in g.requirements if r.operation_contract).model_dump()
    assert [o['value'] for o in proof['operands']] == [39,19]
    assert [o['scope']['season'] for o in proof['operands']] == [2023,2024]
    assert all(o['attestation_digest'] and o['result_digest'] for o in proof['operands'])
    assert proof['replay_digest']
    with pytest.raises(TypeError):
        proof['value'] = 73129


@pytest.mark.parametrize('attack',[
    'cancel','offset','reverse','sum','one_operand','sibling','wrong_measure','wrong_season',
    'missing','wrong_export','wrong_row','rejected','wrong_entity','wrong_population',
    'wrong_qualification','changed_export','unapproved_kind','wrong_owner','wrong_output','cycle',
])
def test_all_laundering_paths_rejected(attack):
    g,artifact,source = compute()
    data = artifact.structured_data
    op = data['governed_operations'][0]
    if attack in {'cancel','offset','sum','one_operand'}:
        data['rows'] = [{'difference':{'cancel':73129,'offset':406,'sum':58,'one_operand':0}[attack]}]
    elif attack == 'reverse':
        op['operands'].reverse()
        data['rows'] = [{'difference':-20}]
    elif attack == 'sibling':
        data['rows'][0]['career_titles'] = 73129
    elif attack == 'wrong_measure':
        op['operands'][0]['field'] = 'avg'
    elif attack == 'wrong_season':
        op['operands'][0] = deepcopy(op['operands'][1])
    elif attack == 'missing':
        op['operands'][0]['artifact_id'] = 'missing'
    elif attack == 'wrong_export':
        op['operands'][0]['export_id'] = source[1].exports[0].id
    elif attack == 'wrong_row':
        op['operands'][0]['row_index'] = 1
    elif attack == 'rejected':
        source[0].status = 'REJECTED'
    elif attack == 'wrong_entity':
        source[0].actual_scope.entities = ['660271']
        from tests.m212_receipt_fixture import with_receipt
        with_receipt(source[0])
    elif attack in {'wrong_population','wrong_qualification'}:
        # Rewrite the requested contract only, not the factual receipt.
        owner = next(r for r in g.requirements if r.operation_contract)
        raw = owner.operation_contract.model_dump()
        raw['operands'][0]['scope']['game_types' if attack == 'wrong_population' else 'qualification'] = (
            ['S'] if attack == 'wrong_population' else {'basis':'PA','minimum':500})
        owner.operation_contract = OperationContract.model_validate(raw)
    elif attack == 'changed_export':
        source[0].exports[0].payload['rows'] = [{'home_runs':73129}]
    elif attack == 'unapproved_kind':
        source[0].kind = 'KNOWLEDGE_ITEM'
    elif attack == 'wrong_owner':
        op['requirement_id'] = 'planner_invented_requirement'
    elif attack == 'wrong_output':
        op['output_field'] = 'medical_clearance'
    elif attack == 'cycle':
        op['operands'][0]['artifact_id'] = artifact.id
        op['operands'][0]['export_id'] = artifact.exports[0].id
        op['operands'][0]['field'] = 'difference'
    for export in artifact.exports:
        export.payload['rows'] = deepcopy(data['rows'])
    projection = project(g,artifact,source)
    assert not projection.claims
    assert projection.goal_status.value != 'COMPLETE'
    assessment = next(a for a in projection.artifact_assessments if a.artifact_id == artifact.id)
    assert assessment.status == 'REJECTED'


def parent_chain(*, rejected=False, cycle=False):
    first = goal()
    parent_owner = next(r for r in first.requirements if r.operation_contract)
    text = first.raw_query + ' Then sum the approved difference.'
    child_contract = OperationContract(key='child',kind='SUM',output_field='total',operands=[
        {'parent_key':'requested','parent_output':'difference'}])
    g = RequirementBuilder().build(SemanticInterpretation(raw_query=text,understanding=text,language='en',analysis_requests=[
        AnalysisRequest(text=first.raw_query,user_span=first.raw_query,operation_contract=parent_owner.operation_contract),
        AnalysisRequest(text='Then sum the approved difference.',user_span='Then sum the approved difference.',operation_contract=child_contract)]))
    parent_owner, child_owner = [r for r in g.requirements if r.operation_contract]
    _,parent,source = compute(g)
    # Auto proposal can execute the root operation only; child lacks its parent yet.
    from app.contracts.models import Artifact, Export
    from app.contracts.operations import GovernedOperation
    op = GovernedOperation(requirement_id=child_owner.id,kind='SUM',output_field='total',operands=[
        {'artifact_id':parent.id,'export_id':parent.exports[0].id,'row_index':0,'field':'difference'}])
    child = Artifact(id='child',kind='DERIVED_MEASURE',status='ACCEPTED',need_id='n2',
        structured_data={'rows':[{'total':20}],'row_count':1,'input_artifact_ids':[parent.id],
                         'governed_operations':[op.model_dump()]},
        exports=[Export(id='exp_child',type='DERIVED_MEASURE',payload={'rows':[{'total':20}]})])
    if rejected:
        parent.structured_data['rows'] = [{'difference':73129}]
        parent.exports[0].payload['rows'] = [{'difference':73129}]
        child.structured_data['rows'] = [{'total':73129}]
        child.exports[0].payload['rows'] = [{'total':73129}]
    if cycle:
        raw = parent_owner.operation_contract.model_dump()
        raw['operands'] = [{'parent_key':'child','parent_output':'total'}]*2
        parent_owner.operation_contract = OperationContract.model_validate(raw)
        parent.structured_data['governed_operations'][0]['operands'] = [
            {'artifact_id':child.id,'export_id':'exp_child','row_index':0,'field':'total'}]*2
        parent.structured_data['input_artifact_ids'] = [child.id]
    return g,parent,child,source


@pytest.mark.parametrize('reverse',[False,True])
def test_recursive_approved_ancestry_independent_of_collection_order(reverse):
    g,parent,child,source = parent_chain()
    artifacts = [*source,parent,child]
    admission = OperationAdmission(list(reversed(artifacts)) if reverse else artifacts,g,{r.id for r in g.requirements},{})
    result = admission.assess(child.id)
    assert result.status == 'ELIGIBLE', result.reasons
    link = result.derived_proofs[0]['operands'][0]['parent']
    assert link['artifact_id'] == parent.id
    assert link['replay_digest'] == admission.assess(parent.id).derived_proofs[0]['replay_digest']
    assert [r['value'] for r in result.derived_proofs[0]['roots']] == [39,19]


@pytest.mark.parametrize('attack',['rejected_parent','cycle','missing'])
def test_recursive_ancestry_fails_closed(attack):
    g,parent,child,source = parent_chain(rejected=attack=='rejected_parent',cycle=attack=='cycle')
    admission = OperationAdmission([*source,child,*([parent] if attack != 'missing' else [])],g,{r.id for r in g.requirements},{})
    result = admission.assess(child.id)
    assert result.status == 'REJECTED'


def test_named_subject_binds_through_context_identity_not_numeric_bindings():
    from app.contracts.models import EntityMention, Artifact, ConversationState, ToolRequest
    from app.runtime.context import ExecutionContext
    from app.capabilities.python_compute import PythonComputeTool
    from tests.test_post_audit_derived_dependency import _bindings
    from tests.m212_receipt_fixture import with_receipt
    contract = next(r.operation_contract for r in goal().requirements if r.operation_contract).model_dump()
    for operand in contract['operands']:
        operand['scope']['subjects'] = ['Mookie Betts']
    text = 'Calculate Mookie Betts 2023 home runs minus 2024 home runs.'
    g = RequirementBuilder().build(SemanticInterpretation(raw_query=text,language='en',
        entities=[EntityMention(mention='Mookie Betts',type_hint='player')],
        analysis_requests=[AnalysisRequest(text=text,user_span=text,operation_contract=OperationContract.model_validate(contract))]))
    source = roots()
    for artifact in source:
        artifact.actual_scope.entities=['605141']
        with_receipt(artifact)
    identity = Artifact(kind='ENTITY_RESOLUTION',status='ACCEPTED',structured_data={
        'resolved':[{'mention':'Mookie Betts','id':605141,'name':'Mookie Betts'}]})
    ctx = ExecutionContext(goal=g,conversation=ConversationState(session_id='named'),artifacts=[*source,identity])
    result = PythonComputeTool().execute(ToolRequest(need_id='n2',capability='python_compute',objective=text,
        bindings=_bindings(),inputs={'operation':{'kind':'DELTA','identity':'PLAYER','partition_by':'SEASON'}}),ctx)
    assert result.artifact is not None,result.outcome
    p = project(g,result.artifact,[*source,identity])
    assert any('subtract result: 20' in c.text for c in p.claims)


def test_scalar_receipt_cannot_substitute_for_its_requested_operation(monkeypatch):
    from app.verification.judge import Judge
    g = goal()
    source = roots()
    monkeypatch.setattr('app.verification.judge.receipt_requirement_ids',
                        lambda artifact,goal,artifacts:{r.id for r in goal.requirements})
    projection = Judge().evaluate(g,source,[])
    assert not projection.claims
    assert projection.goal_status.value != 'COMPLETE'


def test_untrusted_tool_text_never_becomes_claim_text():
    g,artifact,source = compute()
    artifact.text_content = 'career_titles 73129; medically cleared'
    artifact.structured_data['objective'] = 'UNANIMOUS_HALL_OF_FAME_SELECTION'
    projection = project(g,artifact,source)
    assert projection.claims
    assert all('73129' not in c.text and 'HALL_OF_FAME' not in c.text for c in projection.claims)
