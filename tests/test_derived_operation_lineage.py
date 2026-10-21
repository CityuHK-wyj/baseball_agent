"""F-10 migration: Python remains candidate; typed contracts establish authority.

Legacy syntactic dependency/hash assertions are replaced with operation IR proofs.
Positive scope/measure/cross-artifact controls exercise the production tool + Judge.
"""
import pytest
from app.capabilities.python_compute import PythonComputeTool
from app.contracts.models import PlannerNeed, UserRequirement, RequirementKind
from app.response.answer_contract import build_answer_package
from app.verification.judge import Judge
from tests.test_runtime_dataflow_phase5a import _ctx, _stats_artifact, _compute_request
from tests.test_web_evidence_semantics_wsa01_02 import _injury_current_goal
from tests.governed_operation_fixture import compute, project


def _derived(program, *, global_comparison=False, sources=None):
    sources = sources or [_stats_artifact(completeness='COMPLETE')]
    result = PythonComputeTool().execute(
        _compute_request(sources[0],global_comparison=global_comparison,program=program),_ctx(sources))
    assert result.artifact is not None,result.outcome
    return result.artifact,sources


def _injury_projection(artifact,sources=()):
    goal,rid = _injury_current_goal()
    return Judge().evaluate(goal,[artifact,*sources],[PlannerNeed(
        id='n2',objective='current injury status',capability='python_compute',requires=[rid])])


def test_model_literal_constant_is_not_a_derived_fact():
    artifact,sources = _derived("output=[{'injury_status':'Shohei Ohtani is injured.'}]")
    assessment = next(a for a in _injury_projection(artifact,sources).artifact_assessments if a.artifact_id == artifact.id)
    assert assessment.status == 'REJECTED'
    assert 'governed operation' in ' '.join(assessment.reasons)


def test_legitimate_numeric_derivation_over_operands_is_admitted():
    g,artifact,sources = compute()
    assert project(g,artifact,sources).goal_status.value == 'COMPLETE'
    # Numeric data is not authority for an unrelated medical Requirement.
    assert not _injury_projection(artifact,sources).claims


def test_derived_artifact_is_not_identity_evidence():
    artifact,sources = _derived('output=[{"total":sum(v[0]["home_runs"] for v in inputs.values())+987654321}]')
    g,rid = _injury_current_goal()
    entity = next(r for r in g.requirements if r.kind.value == 'ENTITY')
    p = _injury_projection(artifact,sources)
    assert next(prop for prop in p.propositions if prop.kind.value == 'IDENTITY').coverage.value != 'SATISFIED'


@pytest.mark.parametrize('program',[
    "output=[{'injury_status':'Shohei Ohtani is injured.'}]",
    "output=[{'value':'the player is injured'}]",
])
def test_untraceable_string_output_never_mints_a_claim(program):
    artifact,sources = _derived(program)
    p = _injury_projection(artifact,sources)
    assert not p.claims and p.goal_status.value != 'COMPLETE'


def test_program_hash_mismatch_is_rejected():
    artifact,sources = _derived('output=[{"total":sum(v[0]["home_runs"] for v in inputs.values())}]')
    artifact.structured_data['program_hash'] = 1
    p = _injury_projection(artifact,sources)
    assert next(a for a in p.artifact_assessments if a.artifact_id == artifact.id).status == 'REJECTED'


def test_missing_operand_artifact_is_rejected():
    g,artifact,sources = compute()
    artifact.structured_data['input_artifact_ids'] = ['missing']
    assert not project(g,artifact,sources).claims


def test_derived_assessment_is_frozen_into_the_answer_package():
    g,artifact,sources = compute()
    p = project(g,artifact,sources)
    package = build_answer_package(g,p,[*sources,artifact])
    assessment = next(a for a in package.artifact_assessments if a.artifact_id == artifact.id)
    assert assessment.status == 'ELIGIBLE'
    with pytest.raises(TypeError):
        assessment.status = 'REJECTED'
    artifact.structured_data['rows'].append({'injury_status':'invented'})
    assert next(a for a in package.artifact_assessments if a.artifact_id == artifact.id) == assessment


def test_validated_derivation_pins_operand_scope_and_measure():
    g,artifact,sources = compute()
    judge = Judge()
    projection = judge.evaluate(g,[*sources,artifact],[])
    season = UserRequirement(kind=RequirementKind.SEASON,statement='2023',value=2023)
    assert judge._derived_scope_covers(artifact,season,'season')
    absent = season.model_copy(update={'value':2021})
    assert not judge._derived_scope_covers(artifact,absent,'season')
    assessment = next(a for a in projection.artifact_assessments if a.artifact_id == artifact.id)
    assert assessment.derived_operand_fields == ['home_runs']
    assert [o['scope']['season'] for o in assessment.derived_proofs[0]['operands']] == [2023,2024]


def test_cross_artifact_delta_derivation_is_traceable():
    g,artifact,sources = compute()
    p = project(g,artifact,sources)
    proof = next(a for a in p.artifact_assessments if a.artifact_id == artifact.id).derived_proofs[0]
    assert [o['cell']['artifact_id'] for o in proof['operands']] == [a.id for a in sources]
    assert proof['value'] == 20
    artifact.structured_data['rows'][0]['injury_status'] = 'injured'
    assert not project(g,artifact,sources).claims
