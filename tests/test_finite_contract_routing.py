"""A typed operation must not depend on legacy operation-word recognition."""
import pytest
from app.capabilities.python_compute import PythonComputeTool
from app.contracts.models import ToolRequest
from tests.governed_operation_fixture import goal, roots, project
from tests.test_post_audit_derived_dependency import _bindings
from tests.test_runtime_dataflow_phase5a import _ctx


@pytest.mark.parametrize('kind,params', [('SUBTRACT', {}), ('RATIO', {}), ('SUM', {}),
    ('MEAN', {}), ('RANK', {'direction': 'DESC'}), ('AGGREGATE', {'reducer': 'SUM'})])
def test_authoritative_finite_contract_not_legacy_objective_selects_computation(kind, params):
    g = goal(kind, parameters=params)
    source = roots()
    ctx = _ctx(source)
    ctx.goal = g
    result = PythonComputeTool().execute(ToolRequest(need_id='n2', capability='python_compute',
        objective='Produce the requested governed result.', bindings=_bindings()), ctx)
    assert result.artifact is not None, result.outcome
    assert result.artifact.provenance['sandbox'] == 'trusted-finite-ir'
    assert project(g, result.artifact, source).goal_status.value == 'COMPLETE'
