"""Mechanism-level controls for the independent review's F-1/F-2 findings."""
import pytest

from app.contracts.models import AnalysisRequest, Constraint, SemanticInterpretation
from app.semantic.goals import RequirementBuilder
from app.verification.judge import Judge
from tests.governed_operation_fixture import roots
from tests.test_reporting_obligations import reported_values


def reporting_goal(copies=1, *, cells=None, unsupported=False):
    text = 'Report the 2023 home run total in the regular season.'
    if cells and len(cells) > 1:
        text = 'Report the requested 2023 regular-season home runs and hits.'
    if unsupported:
        text += ' Report exit velocity as well.'
    return RequirementBuilder().build(SemanticInterpretation(raw_query=text, language='en',
        analysis_requests=[AnalysisRequest(text=text, user_span=text,
            reporting_contract={'cells': cells or [{'measure': 'home_runs', 'season': 2023, 'game_types': ['R']}]})],
        constraints=[Constraint(kind='season', value=2023, explicit=True), *[
            Constraint(kind='game_population', value='REGULAR_SEASON', explicit=True)
            for _ in range(copies)]]))


def regular_roots():
    from tests.m212_receipt_fixture import with_receipt
    source = roots()
    for a in source:
        a.actual_scope.game_population = 'REGULAR_SEASON'
        with_receipt(a)
    return source


def repeated_root(count):
    source = regular_roots()[0]
    result = [source]
    for i in range(1, count):
        clone = source.model_copy(deep=True)
        clone.id = f'duplicate_{i}'
        clone.exports[0].id = f'duplicate_export_{i}'
        result.append(clone)
    return result


@pytest.mark.parametrize('count', [1, 2, 3])
def test_f1_agreeing_receipts_admit_one_factual_claim(count):
    p = Judge().evaluate(reporting_goal(), repeated_root(count), [])
    assert p.goal_status.value == 'COMPLETE'
    assert reported_values(p) == [39]
    assert len([proof for a in p.artifact_assessments for proof in a.reporting_proofs]) == 1


@pytest.mark.parametrize('count', [1, 2, 3])
def test_f2_duplicate_scope_requirements_conserve_coverage(count):
    g = reporting_goal(count)
    before = [r.model_dump(mode='json') for r in g.requirements]
    p = Judge().evaluate(g, regular_roots()[:1], [])
    assert p.goal_status.value == 'COMPLETE'
    assert all(a.coverage.value == 'SATISFIED' for a in p.requirement_assessments)
    assert [r.model_dump(mode='json') for r in g.requirements] == before
    assert reported_values(p) == [39]
    ids = {r.id for r in g.requirements if r.kind.value == 'GAME_POPULATION'}
    shared = [prop for prop in p.propositions if ids & set(prop.owner_requirement_ids)]
    assert len(shared) == 1
    assert set(shared[0].owner_requirement_ids) == ids


def refresh(a):
    from tests.m212_receipt_fixture import with_receipt
    for export in a.exports:
        export.payload['rows'] = [dict(row) for row in a.structured_data['rows']]
    return with_receipt(a)


def other_provider(a):
    """A second, replayable physical origin, not a receipt-id independence label."""
    from app.capabilities.analytics.compiler import SQLCompiler
    from app.capabilities.analytics.ir import AnalyticalQuery
    from app.capabilities.analytics.conformance import attest, compare, digest, scope_from_execution, RequestedAnalyticalContract
    receipt = a.structured_data['execution_receipt']
    query = AnalyticalQuery.model_validate(receipt['validated_ir'])
    compiled = SQLCompiler().compile(query, 'PARQUET')
    original = receipt['attestation']
    from app.capabilities.analytics.conformance import SourceAvailability
    attestation = attest(query, compiled, availability=SourceAvailability.model_validate(
        {**original['availability'], 'source': 'PARQUET'}), executed=True)
    requested = RequestedAnalyticalContract.model_validate(receipt['requested'])
    receipt.update(attestation=attestation.model_dump(mode='json'), attestation_digest=digest(attestation),
                   conformance=compare(requested, attestation).model_dump(mode='json'))
    a.actual_scope = scope_from_execution(attestation)
    a.structured_data.update(sql=compiled.sql, actual_measures=list(attestation.measures))
    return a


@pytest.mark.parametrize('independent', [False, True])
def test_f1_alternative_origins_do_not_fabricate_corroboration(independent):
    source = repeated_root(2)
    if independent:
        other_provider(source[1])
    from app.capabilities.analytics.conformance import receipt_usable
    assert all(receipt_usable(a) for a in source)
    for ordered in (source, list(reversed(source))):
        p = Judge().evaluate(reporting_goal(), ordered, [])
        assert p.goal_status.value == 'COMPLETE'
        assert reported_values(p) == [39]
        assert len(p.claims) == 1  # alternatives, not two votes or two facts
        assert len(p.claims[0].support_refs) == 1


@pytest.mark.parametrize('values', [[39, 38], [39, 39, 38]])
def test_f1_conflicting_values_never_use_first_or_majority(values):
    source = repeated_root(len(values))
    for a, value in zip(source, values):
        a.structured_data['rows'][0]['home_runs'] = value
        refresh(a)
    for ordered in (source, list(reversed(source))):
        p = Judge().evaluate(reporting_goal(), ordered, [])
        assert p.goal_status.value != 'COMPLETE'
        assert not p.claims


@pytest.mark.parametrize('attack', ['population', 'units', 'player', 'season', 'measure', 'qualification', 'rejected', 'unsupported'])
def test_f1_incompatible_or_unapproved_receipts_cannot_borrow_valid_authority(attack):
    source = repeated_root(2)
    other = source[1]
    if attack == 'population':
        other.actual_scope.game_population = 'POSTSEASON'
        refresh(other)
    elif attack == 'units':
        # Recomputing a digest is not enough: compiler reconstruction rejects it.
        from app.capabilities.analytics.conformance import digest
        receipt = other.structured_data['execution_receipt']
        measure = next(m for m in receipt['attestation']['measures'] if m['key'] == 'home_runs')
        measure['unit'] = 'mph'
        receipt['attestation_digest'] = digest(receipt['attestation'])
    elif attack == 'player':
        other.actual_scope.entities = ['605141']
        refresh(other)
    elif attack == 'season':
        other.actual_scope.season = 2024
        refresh(other)
    elif attack == 'measure':
        other.actual_scope.measures = ['hits']
        other.structured_data['rows'] = [{'hits': 39}]
        refresh(other)
    elif attack == 'qualification':
        from app.contracts.operations import OperandScope
        from app.verification.governed_operations import OperationAdmission
        from app.contracts.operations import OperandCell
        scope = OperandScope(measure='home_runs', season=2023, game_types=['R'],
                           qualification={'basis': 'PA', 'minimum': 500})
        with pytest.raises(ValueError):
            OperationAdmission(source, reporting_goal(), set(), {}).root(other, OperandCell(
                artifact_id=other.id, export_id=other.exports[0].id, row_index=0, field='home_runs'), scope)
        other.status = 'REJECTED'
    elif attack == 'rejected':
        other.status = 'REJECTED'
    else:
        other.kind = 'KNOWLEDGE_ITEM'
    p = Judge().evaluate(reporting_goal(), source, [])
    assert p.goal_status.value == 'COMPLETE'
    assert reported_values(p) == [39]
    assert all(ref.ref_id == source[0].id for c in p.claims for ref in c.support_refs)
    # Removing the real evidence must not let the incompatible lookalike become a Claim.
    assert not Judge().evaluate(reporting_goal(), source[1:], []).claims


def bound_compute(g, source):
    from app.contracts.models import InputBinding, ToolRequest
    from app.capabilities.python_compute import PythonComputeTool
    from tests.test_runtime_dataflow_phase5a import _ctx
    ctx = _ctx(source)
    ctx.goal = g
    request = ToolRequest(need_id='n2', capability='python_compute', objective=g.raw_query,
        bindings=[InputBinding(need_id='n2', requirement='STATISTICAL_RESULT', export_id=a.exports[0].id,
                               artifact_id=a.id, export_type=a.exports[0].type, source_need_id=f'n{i}') for i, a in enumerate(source)],
        inputs={'operation': {'kind': 'DELTA', 'identity': 'PLAYER', 'partition_by': 'SEASON'}})
    return PythonComputeTool().execute(request, ctx)


@pytest.mark.parametrize('copies', [1, 2, 3])
def test_f1_duplicate_bound_operands_avoid_spurious_source_overlap(copies):
    from tests.governed_operation_fixture import goal
    source = roots()
    for i in range(1, copies):
        extra = source[0].model_copy(deep=True)
        extra.id = f'extra_{i}'
        extra.exports[0].id = f'extra_export_{i}'
        source.append(extra)
    g = goal()
    result = bound_compute(g, source)
    assert result.artifact is not None, result.outcome
    p = Judge().evaluate(g, [*source, result.artifact], [])
    assert p.goal_status.value == 'COMPLETE'
    assert any('subtract result: 20' in c.text for c in p.claims)


@pytest.mark.parametrize('reuse', [False, True])
def test_f1_distinct_cells_in_one_artifact_and_explicit_operand_reuse(reuse):
    from tests.governed_operation_fixture import goal
    scope = {'measure': 'home_runs', 'season': 2023}
    source = roots()[:1]
    if not reuse:
        source[0].actual_scope.measures = ['home_runs', 'hits']
        source[0].structured_data['rows'] = [{'home_runs': 39, 'hits': 110}]
        refresh(source[0])
    g = goal(scopes=[{'scope': scope}, {'scope': {**scope, 'measure': 'home_runs' if reuse else 'hits'}}])
    result = bound_compute(g, source)
    assert result.artifact is not None, result.outcome
    p = Judge().evaluate(g, [*source, result.artifact], [])
    assert p.goal_status.value == 'COMPLETE'
    assert result.artifact.structured_data['rows'] == [{'difference': 0 if reuse else -71}]


def test_f1_genuine_untyped_source_overlap_stays_prohibited():
    from tests.test_phase5a_closure import _complete_artifact
    from app.verification.enumeration import check_compatibility
    from app.contracts.models import GlobalOperation
    source = [_complete_artifact(a, season=2024, source='POSTGRES') for a in ('a', 'b')]
    decision = check_compatibility(GlobalOperation(kind='UNION', identity='PLAYER'), source)
    assert not decision.ok and decision.code == 'SOURCE_OVERLAP'


@pytest.mark.parametrize('rejected', [False, True])
def test_f1_duplicate_derived_ancestry_is_replayed_not_independent(rejected):
    from tests.test_governed_operation_admission import parent_chain
    from app.contracts.operations import OperationContract
    from app.verification.governed_operations import OperationAdmission
    g, parent, child, source = parent_chain(rejected=rejected)
    owner = next(r for r in g.requirements if r.operation_contract and r.operation_contract.key == 'child')
    raw = owner.operation_contract.model_dump()
    raw['operands'] *= 2
    owner.operation_contract = OperationContract.model_validate(raw)
    child.structured_data['governed_operations'][0]['operands'] *= 2
    child.structured_data['rows'] = [{'total': 40}]
    child.exports[0].payload['rows'] = [{'total': 40}]
    admission = OperationAdmission([*source, parent, child], g, {r.id for r in g.requirements}, {})
    assessed = admission.assess(child.id)
    assert assessed.status == ('REJECTED' if rejected else 'ELIGIBLE')
    if not rejected:
        assert len(assessed.derived_proofs[0]['operands']) == 2
        assert len(assessed.derived_proofs[0]['roots']) == 2  # not four independent roots


@pytest.mark.parametrize('missing', [False, True])
def test_f2_compound_reporting_and_duplicate_scopes_share_no_claim_shortcut(missing):
    cells = [{'measure': m, 'season': 2023, 'game_types': ['R']} for m in ('home_runs', 'hits')]
    g = reporting_goal(2, cells=cells)
    source = regular_roots()[:1]
    if not missing:
        source[0].actual_scope.measures = ['home_runs', 'hits']
        source[0].structured_data['rows'] = [{'home_runs': 39, 'hits': 110}]
        refresh(source[0])
    p = Judge().evaluate(g, source, [])
    assert p.goal_status.value == ('LIMITED' if missing else 'COMPLETE')
    assert sorted(reported_values(p)) == ([39] if missing else [39, 110])
    owner = next(r for r in g.requirements if r.reporting_contract)
    assessment = next(a for a in p.requirement_assessments if a.requirement_id == owner.id)
    assert assessment.coverage.value == ('PARTIAL' if missing else 'SATISFIED')
    assert len([prop for prop in p.propositions if prop.requirement_id == owner.id]) == 2


@pytest.mark.parametrize('facet,value', [('season', 2024), ('subjects', ['Different player']), ('measure', 'hits')])
def test_f2_similar_obligations_are_not_equivalent(facet, value):
    from app.verification.propositions import ensure_propositions
    base = {'measure': 'home_runs', 'season': 2023, 'game_types': ['R']}
    g = reporting_goal(2, cells=[base, {**base, facet: value}])
    props = [p for p in ensure_propositions(g) if p.reporting_index is not None]
    assert len(props) == 2
    p = Judge().evaluate(g, regular_roots()[:1], [])
    assert p.goal_status.value == 'LIMITED'
    assert reported_values(p) == [39]
    assert [prop.coverage.value for prop in p.propositions if prop.reporting_index is not None] == ['SATISFIED', 'UNSUPPORTED']


def test_f2_overlapping_nonidentical_requirements_remain_local():
    from tests.test_reporting_obligations import reporting_goal as two_seasons
    g = two_seasons(operation=False)
    owner = next(r for r in g.requirements if r.reporting_contract)
    # Duplicate source-bound Requirement, but with one different reporting obligation.
    duplicate = owner.model_copy(deep=True)
    duplicate.id = 'overlapping_owner'
    from app.contracts.reporting import ReportingContract
    raw = duplicate.reporting_contract.model_dump()
    raw['cells'][1]['measure'] = 'exit_velocity'
    duplicate.reporting_contract = ReportingContract.model_validate(raw)
    g.requirements.append(duplicate)
    p = Judge().evaluate(g, roots(), [])
    assert p.goal_status.value == 'LIMITED'
    states = {a.requirement_id: a.coverage.value for a in p.requirement_assessments}
    assert states[owner.id] == 'SATISFIED'
    assert states[duplicate.id] == 'PARTIAL'
    assert sorted(reported_values(p)) == [19, 39, 39]


@pytest.mark.parametrize('count', [2, 3])
def test_f2_package_and_rendering_follow_all_satisfied_assessments(count):
    from app.response.answer_contract import build_answer_package, render_governed_package
    g = reporting_goal(count)
    source = regular_roots()[:1]
    p = Judge().evaluate(g, source, [])
    package = build_answer_package(g, p, source, language='zh')
    text = render_governed_package(package)
    assert not package.unresolved
    assert '未能确认' not in text
    assert '39' in text
    population = [r for r in package.requirements if r.kind == 'GAME_POPULATION']
    assert len(population) == count
    assert len({tuple(r.proposition_ids) for r in population}) == 1


def test_f2_duplicate_provenance_and_optional_identity_are_conserved():
    from app.verification.propositions import ensure_propositions
    g = reporting_goal(2)
    duplicate = [r for r in g.requirements if r.kind.value == 'GAME_POPULATION'][1]
    duplicate.required = False
    provenance = duplicate.provenance.model_dump(mode='json')
    provenance['user_span'] = 'regular season'
    from app.contracts.models import RequirementProvenance
    duplicate.provenance = RequirementProvenance.model_validate(provenance).frozen_snapshot()
    population = [r for r in g.requirements if r.kind.value == 'GAME_POPULATION']
    assert population[0].provenance.user_span != population[1].provenance.user_span
    # Provenance is a retained Requirement property, not an equivalence shortcut.
    before = {r.id: r.model_dump(mode='json') for r in g.requirements}
    props = ensure_propositions(g)
    p = Judge().evaluate(g, regular_roots()[:1], [])
    assert p.goal_status.value == 'COMPLETE'
    assert {r.id: r.model_dump(mode='json') for r in g.requirements} == before
    assert len({r.id for r in g.requirements}) == len(p.requirement_assessments)
    assert all(r.provenance is not None for r in g.requirements)
    shared = next(p for p in props if duplicate.id in p.owner_requirement_ids)
    assert shared.mandatory and not duplicate.required


@pytest.mark.parametrize('attack', ['unsatisfied', 'not_attempted', 'lost_mapping', 'unproved_alias'])
def test_f2_whole_state_rejects_contradictory_complete(attack):
    from app.contracts.models import AnswerProjection
    from app.verification.governed_state import ExpectedObligations, validate_governed_projection, GovernedStateError
    g = reporting_goal(2)
    p = Judge().evaluate(g, regular_roots()[:1], [])
    expected = ExpectedObligations.from_goal(g)
    mutable = AnswerProjection.model_validate(p.model_dump(mode='json'))
    duplicate = [r for r in g.requirements if r.kind.value == 'GAME_POPULATION'][1]
    assessment = next(a for a in mutable.requirement_assessments if a.requirement_id == duplicate.id)
    if attack == 'unsatisfied':
        assessment.status = 'UNSATISFIED'
    elif attack == 'not_attempted':
        assessment.coverage = 'NOT_ATTEMPTED'
    elif attack == 'unproved_alias':
        assessment.authority_status = 'UNPROVEN'
    else:
        next(prop for prop in mutable.propositions if duplicate.id in prop.owner_requirement_ids).requirement_ids = []
    with pytest.raises(GovernedStateError):
        validate_governed_projection(mutable, expected=expected)


def test_f2_fallback_after_downgrade_reaggregates_all_duplicate_owners():
    from app.contracts.models import AnswerProjection
    from app.verification.governed_state import ExpectedObligations, governed_limited_fallback
    g = reporting_goal(2)
    p = Judge().evaluate(g, regular_roots()[:1], [])
    mutable = AnswerProjection.model_validate(p.model_dump(mode='json'))
    duplicate_ids = {r.id for r in g.requirements if r.kind.value == 'GAME_POPULATION'}
    shared = next(prop for prop in mutable.propositions if duplicate_ids & set(prop.owner_requirement_ids))
    from app.contracts.models import CoverageState
    shared.coverage = CoverageState.UNSUPPORTED
    shared.supporting_artifact_ids = []
    limited = governed_limited_fallback(mutable, expected=ExpectedObligations.from_goal(g))
    assert limited.goal_status.value == 'LIMITED'
    assert reported_values(limited) == [39]
    assert all(a.coverage.value == 'UNSUPPORTED' for a in limited.requirement_assessments if a.requirement_id in duplicate_ids)
    assert p.goal_status.value == 'COMPLETE'  # immutable source was not edited


def test_f1_exact_numeric_normalization_does_not_round_conflicts():
    source = repeated_root(2)
    source[1].structured_data['rows'][0]['home_runs'] = 39.0
    refresh(source[1])
    assert reported_values(Judge().evaluate(reporting_goal(), source, [])) == [39]
    source[1].structured_data['rows'][0]['home_runs'] = 39.00000001
    refresh(source[1])
    assert not Judge().evaluate(reporting_goal(), source, []).claims


def test_f1_cross_provider_derived_computation_preserves_physical_ancestry():
    from tests.governed_operation_fixture import goal
    g = goal()
    source = roots()
    other_provider(source[0])
    result = bound_compute(g, source)
    assert result.artifact is not None, result.outcome
    p = Judge().evaluate(g, [*source, result.artifact], [])
    assert p.goal_status.value == 'COMPLETE'
    proof = next(a for a in p.artifact_assessments if a.artifact_id == result.artifact.id).derived_proofs[0]
    assert [o['cell']['artifact_id'] for o in proof['operands']] == [a.id for a in source]
    assert {a.actual_scope.source for a in source} == {'POSTGRES', 'PARQUET'}
    assert proof['value'] == 20


def test_f2_duplicate_supported_scope_and_independent_unproved_obligation():
    from app.contracts.models import EvidenceRequest
    text = 'Report the 2023 regular-season home run total. Find the attributed injury account.'
    g = RequirementBuilder().build(SemanticInterpretation(raw_query=text, language='en',
        analysis_requests=[AnalysisRequest(text='Report the 2023 regular-season home run total.',
            user_span='Report the 2023 regular-season home run total.', reporting_contract={'cells': [
                {'measure': 'home_runs', 'season': 2023, 'game_types': ['R']} ]})],
        evidence_requests=[EvidenceRequest(text='Find the attributed injury account.',
            user_span='Find the attributed injury account.', evidence_intent={
                'mode': 'REPORTED_ACCOUNT', 'information_class': 'INJURY'})],
        constraints=[Constraint(kind='season', value=2023), *[
            Constraint(kind='game_population', value='REGULAR_SEASON') for _ in range(2)]]))
    p = Judge().evaluate(g, regular_roots()[:1], [])
    assert p.goal_status.value == 'LIMITED'
    assert reported_values(p) == [39]
    scopes = {r.id for r in g.requirements if r.kind.value == 'GAME_POPULATION'}
    assert all(a.coverage.value == 'SATISFIED' for a in p.requirement_assessments if a.requirement_id in scopes)
    account = next(r for r in g.requirements if r.kind.value == 'EVIDENCE')
    assert next(a for a in p.requirement_assessments if a.requirement_id == account.id).coverage.value != 'SATISFIED'


def test_existing_partial_root_replay_is_not_reclassified_as_rejected():
    from tests.governed_operation_fixture import goal
    from app.verification.governed_operations import OperationAdmission
    from app.contracts.operations import OperandCell
    source = roots()
    source[0].status = 'PARTIAL'
    g = goal()
    scope = next(r.operation_contract for r in g.requirements if r.operation_contract).operands[0].scope
    proof = OperationAdmission(source, g, {r.id for r in g.requirements}, {}).root(source[0],
        OperandCell(artifact_id=source[0].id, export_id=source[0].exports[0].id, row_index=0, field='home_runs'), scope)
    assert proof['scope']['season'] == 2023


def test_ownerless_proposition_cannot_gain_vacuous_shared_authority():
    from app.contracts.models import AnswerProjection
    from app.verification.governed_state import validate_governed_projection, GovernedStateError
    p = Judge().evaluate(reporting_goal(), regular_roots()[:1], [])
    mutable = AnswerProjection.model_validate(p.model_dump(mode='json'))
    mutable.propositions[0].requirement_id = None
    mutable.propositions[0].requirement_ids = []
    with pytest.raises(GovernedStateError):
        validate_governed_projection(mutable)
