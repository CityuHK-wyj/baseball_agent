"""A proven global aggregate has one result row; its output ceiling is not a row filter."""
from app.capabilities.analytics.ir import AnalyticalQuery, validate_query
from app.capabilities.analytics.compiler import SQLCompiler
from app.capabilities.analytics.conformance import ExecutionAttestation, RequestedAnalyticalContract, attest, compare, digest, scope_from_execution
from tests.governed_operation_fixture import roots, compute, project


def test_internal_output_ceiling_does_not_restrict_a_proven_scalar_aggregate():
    source=roots()
    for artifact in source:
        receipt=artifact.structured_data['execution_receipt']
        original=ExecutionAttestation.model_validate(receipt['attestation'])
        query=AnalyticalQuery.model_validate(receipt['validated_ir'])
        query.limit=5000
        query=validate_query(query,comparison_plan=True)
        compiled=SQLCompiler().compile(query,'POSTGRES')
        observed=attest(query,compiled,availability=original.availability,executed=True,
                        internal_row_ceiling=5000,returned_groups=1)
        requested=RequestedAnalyticalContract.model_validate(receipt['requested'])
        receipt.update(validated_ir=query.model_dump(mode='json'),attestation=observed.model_dump(mode='json'),
                       attestation_digest=digest(observed),conformance=compare(requested,observed).model_dump(mode='json'))
        artifact.structured_data['sql']=compiled.sql
        artifact.actual_scope=scope_from_execution(observed)
    g,artifact,source=compute(source=source)
    p=project(g,artifact,source)
    assert any('subtract result: 20' in c.text for c in p.claims)
    assessment=next(a for a in p.artifact_assessments if a.artifact_id==artifact.id)
    assert all(root['scalar_shape']['nonrestricting_result_ceiling']==5000
               for proof in assessment.derived_proofs for root in proof['roots'])
