"""Production tool/Judge fixtures for typed Requirement-local operations."""
from app.contracts.models import AnalysisRequest, SemanticInterpretation, RelationSemantics, RelationKind, ToolRequest
from app.contracts.operations import OperationContract
from app.semantic.goals import RequirementBuilder
from tests.test_post_audit_derived_dependency import _operands, _bindings, _project
from tests.test_runtime_dataflow_phase5a import _ctx
from tests.m212_receipt_fixture import with_receipt
from app.capabilities.python_compute import PythonComputeTool


def roots():
    result = list(_operands())
    for artifact in result:
        artifact.actual_scope.entities = []
        with_receipt(artifact)
    return result


def goal(kind='SUBTRACT', *, scopes=None, parameters=None, field='difference', key='requested'):
    contract = OperationContract(key=key,kind=kind,output_field=field,
        parameters=parameters or {},operands=scopes or [
            {'scope':{'measure':'home_runs','season':2023}},
            {'scope':{'measure':'home_runs','season':2024}}])
    text = f'Calculate the requested {kind} over the specified seasonal cells.'
    return RequirementBuilder().build(SemanticInterpretation(raw_query=text,understanding=text,language='en',
        analysis_requests=[AnalysisRequest(text=text,user_span=text,objective=text,
            operation_contract=contract,relation_semantics=RelationSemantics(
                relation_kind=RelationKind.VALUE,relation_kinds=[RelationKind.VALUE]))]))


def compute(g=None, source=None, *, inputs=None):
    g = g or goal()
    source = source or roots()
    ctx = _ctx(source)
    ctx.goal = g
    request = ToolRequest(id='tr',need_id='n2',capability='python_compute',objective=g.raw_query,
        bindings=_bindings(),inputs={'operation':{'kind':'DELTA','identity':'PLAYER','partition_by':'SEASON'},**(inputs or {})})
    result = PythonComputeTool().execute(request,ctx)
    assert result.artifact is not None, result.outcome
    return g,result.artifact,source


def project(g,artifact,source):
    return _project(g,artifact,source)
