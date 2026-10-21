"""User reporting slots are not internal computation operands (synthetic receipts)."""
import pytest
from app.contracts.models import AnalysisRequest, Constraint, SemanticInterpretation
from app.semantic.goals import RequirementBuilder
from app.verification.judge import Judge
from tests.governed_operation_fixture import compute, roots, project

SCOPES = [{'measure': 'home_runs', 'season': y} for y in (2023, 2024)]


def test_reporting_grounding_normalizes_governed_measure_symbol_encoding():
    from app.semantic.reporting import ground_reporting_contract
    contract = ground_reporting_contract({'cells': [
        {'measure': 'Runs_Batted_In', 'season': 2025},
        {'measure': 'home runs', 'season': 2023},
    ]})
    assert [c.measure for c in contract.cells] == ['rbi', 'home_runs']


def test_reporting_grounding_rejects_measure_outside_governed_vocabulary():
    from app.semantic.reporting import ground_reporting_contract
    with pytest.raises(ValueError, match='governed Measure vocabulary'):
        ground_reporting_contract({'cells': [{'measure': 'clutch_factor', 'season': 2023}]})


def test_operation_grounding_normalizes_operand_measure_symbol_encoding():
    from app.semantic.operations import ground_operation_contract
    contract = ground_operation_contract({'key': 'diff', 'kind': 'SUBTRACT', 'operands': [
        {'scope': {'measure': 'Home Runs', 'season': 2023}},
        {'scope': {'measure': 'home_runs', 'season': 2024}}],
        'output_field': 'difference'})
    assert [o.scope.measure for o in contract.operands] == ['home_runs', 'home_runs']


def reporting_goal(*, report=True, operation=True):
    clauses = []
    requests = []
    if report:
        text = 'Report home runs for 2023 and 2024.'
        clauses.append(text)
        requests.append(AnalysisRequest(text=text, user_span=text, objective=text,
            reporting_contract={'cells': SCOPES},
            evidence_intent={'mode': 'CONFIRMED_STATE', 'information_class': 'PERFORMANCE'}))
    if operation:
        text = 'Subtract 2024 home runs from 2023 home runs.'
        clauses.append(text)
        requests.append(AnalysisRequest(text=text, user_span=text, objective=text,
            operation_contract={'key': 'difference', 'kind': 'SUBTRACT', 'output_field': 'difference',
                'operands': [{'scope': s} for s in SCOPES]},
            evidence_intent={'mode': 'CONFIRMED_STATE', 'information_class': 'PERFORMANCE'}))
    text = ' '.join(clauses)
    return RequirementBuilder().build(SemanticInterpretation(raw_query=text, understanding=text,
        analysis_requests=requests,
        constraints=[Constraint(kind='season', value=y, explicit=True) for y in (2023, 2024)]))


def report_props(projection):
    return [p for p in projection.propositions if getattr(p, 'reporting_index', None) is not None]


def reported_values(projection):
    return [v['value'] for c in projection.claims for v in c.answer_values]


def test_operands_only_are_not_extra_user_reporting_requirements():
    goal = reporting_goal(report=False)
    assert not [r for r in goal.requirements if r.kind.value == 'MEASURE']
    goal, derived, sources = compute(goal)
    p = project(goal, derived, sources)
    assert p.goal_status.value == 'COMPLETE'
    assert not report_props(p)
    assert any('subtract result: 20' in c.text for c in p.claims)


def test_raw_values_and_difference_each_have_separate_approved_slots():
    goal, derived, sources = compute(reporting_goal())
    p = project(goal, derived, sources)
    assert len(report_props(p)) == 2
    assert all(p.coverage.value == 'SATISFIED' for p in report_props(p))
    assert p.goal_status.value == 'COMPLETE'
    assert sorted(reported_values(p)) == [19, 39]
    assert any('subtract result: 20' in c.text for c in p.claims)
    for prop in report_props(p):
        assert prop.supporting_claim_ids
        assert derived.id not in prop.supporting_artifact_ids


def test_standalone_reporting_needs_no_derived_artifact():
    p = Judge().evaluate(reporting_goal(operation=False), roots(), [])
    assert p.goal_status.value == 'COMPLETE'
    assert sorted(reported_values(p)) == [19, 39]


def test_missing_second_source_preserves_first_raw_fact_without_overcomplete():
    p = Judge().evaluate(reporting_goal(), roots()[:1], [])
    assert p.goal_status.value == 'LIMITED'
    assert len(report_props(p)) == 2
    assert [s.coverage.value for s in report_props(p)] == ['SATISFIED', 'UNSUPPORTED']
    assert reported_values(p) == [39]


def test_computation_failure_does_not_erase_independent_raw_reporting():
    p = Judge().evaluate(reporting_goal(), roots(), [])
    assert p.goal_status.value == 'LIMITED'
    assert sorted(reported_values(p)) == [19, 39]
    assert not any('subtract result' in c.text for c in p.claims)


@pytest.mark.parametrize('attack', ['rejected', 'rows', 'export', 'wrong_season', 'wrong_measure'])
def test_unapproved_or_inconsistent_root_cannot_fill_reporting_slot(attack):
    source = roots()
    right = source[1]
    if attack == 'rejected':
        right.status = 'REJECTED'
    elif attack == 'rows':
        right.structured_data['rows'][0]['home_runs'] = 73129
    elif attack == 'export':
        right.exports[0].payload['rows'] = [{'home_runs': 73129}]
    elif attack == 'wrong_season':
        g = reporting_goal()
        owner = next(r for r in g.requirements if getattr(r, 'reporting_contract', None))
        raw = owner.reporting_contract.model_dump()
        raw['cells'][1]['season'] = 2022
        owner.reporting_contract = type(owner.reporting_contract).model_validate(raw)
    else:
        g = reporting_goal()
        owner = next(r for r in g.requirements if getattr(r, 'reporting_contract', None))
        raw = owner.reporting_contract.model_dump()
        raw['cells'][1]['measure'] = 'exit_velocity'
        owner.reporting_contract = type(owner.reporting_contract).model_validate(raw)
    p = Judge().evaluate(g if attack in {'wrong_season', 'wrong_measure'} else reporting_goal(), source, [])
    assert p.goal_status.value != 'COMPLETE'
    assert reported_values(p) == [39]


def test_fabricated_derived_output_does_not_poison_verified_raw_observations():
    g, derived, source = compute(reporting_goal())
    derived.structured_data['rows'] = [{'difference': 73129}]
    for export in derived.exports:
        export.payload['rows'] = [{'difference': 73129}]
    p = project(g, derived, source)
    assert p.goal_status.value == 'LIMITED'
    assert sorted(reported_values(p)) == [19, 39]
    assert all('73129' not in claim.text for claim in p.claims)
    assert next(a for a in p.artifact_assessments if a.artifact_id == derived.id).status == 'REJECTED'


def test_unavailable_independent_web_account_does_not_erase_local_reports():
    from app.contracts.models import EvidenceRequest
    report = 'Report home runs for 2023 and 2024.'
    account = 'Find the attributed account of the recovery.'
    g = RequirementBuilder().build(SemanticInterpretation(raw_query=report + ' ' + account,
        analysis_requests=[AnalysisRequest(text=report, user_span=report, reporting_contract={'cells': SCOPES})],
        evidence_requests=[EvidenceRequest(text=account, user_span=account,
            evidence_intent={'mode': 'REPORTED_ACCOUNT', 'information_class': 'INJURY'})],
        constraints=[Constraint(kind='season', value=y, explicit=True) for y in (2023, 2024)]))
    p = Judge().evaluate(g, roots(), [])
    assert p.goal_status.value == 'LIMITED'
    assert sorted(reported_values(p)) == [19, 39]
    assert all(p.coverage.value == 'SATISFIED' for p in report_props(p))
    assert any(a.coverage.value == 'UNSUPPORTED' for a in p.requirement_assessments)


def test_approved_operation_cannot_automatically_satisfy_an_unproved_report_slot():
    from app.contracts.reporting import ReportingContract
    g = reporting_goal()
    owner = next(r for r in g.requirements if r.reporting_contract)
    raw = owner.reporting_contract.model_dump()
    raw['cells'][1]['measure'] = 'exit_velocity'
    owner.reporting_contract = ReportingContract.model_validate(raw)
    g, derived, source = compute(g)
    p = project(g, derived, source)
    assert p.goal_status.value == 'LIMITED'
    assert reported_values(p) == [39]
    assert any('subtract result: 20' in c.text for c in p.claims)
    assert report_props(p)[1].coverage.value == 'UNSUPPORTED'


@pytest.mark.parametrize('facet,value', [
    ('analytical_role', 'PITCHER'), ('subjects', ['unresolved subject']),
    ('game_types', ['S']), ('qualification', {'basis': 'PA', 'minimum': 500}),
    ('date_range', {'start': '2022-01-01', 'end': '2022-12-31'}),
])
def test_all_reporting_scope_facets_are_independently_checked(facet, value):
    from app.contracts.reporting import ReportingContract
    g = reporting_goal(operation=False)
    owner = next(r for r in g.requirements if r.reporting_contract)
    raw = owner.reporting_contract.model_dump()
    raw['cells'][1][facet] = value
    owner.reporting_contract = ReportingContract.model_validate(raw)
    p = Judge().evaluate(g, roots(), [])
    assert p.goal_status.value == 'LIMITED'
    assert reported_values(p) == [39]


@pytest.mark.parametrize('restriction', ['against lefties with two strikes', 'with at least 500 PA', 'split by pitch type'])
def test_omitted_known_source_restrictions_do_not_become_unrestricted_reports(restriction):
    text = f'Report 2024 home runs {restriction}.'
    g = RequirementBuilder().build(SemanticInterpretation(raw_query=text,
        analysis_requests=[AnalysisRequest(text=text, user_span=text,
            reporting_contract={'cells': [SCOPES[1]]})],
        constraints=[Constraint(kind='season', value=2024, explicit=True)]))
    p = Judge().evaluate(g, roots()[1:], [])
    assert p.goal_status.value != 'COMPLETE'
    assert not reported_values(p)


@pytest.mark.parametrize('facet,value', [('analytical_role', 'PITCHER'), ('game_types', ['S']), ('measure', 'exit_velocity')])
def test_scope_only_receipt_cannot_launder_a_rejected_report_into_public_table(facet, value):
    from types import SimpleNamespace
    from app.api.presentation import present
    text = 'Report 2024 home runs.'
    scope = {**SCOPES[1], facet: value}
    g = RequirementBuilder().build(SemanticInterpretation(raw_query=text,
        analysis_requests=[AnalysisRequest(text=text, user_span=text, reporting_contract={'cells': [scope]})],
        constraints=[Constraint(kind='season', value=2024)]))
    source = roots()[1:]
    p = Judge().evaluate(g, source, [])
    assert p.goal_status.value == 'LIMITED'
    assert not p.claims
    view = present(SimpleNamespace(goal=g, projection=p, artifacts=source,
        semantic=SimpleNamespace(language='en'), answer='not established', outcomes=[]))
    assert not view.artifacts


def test_source_manifest_conserves_each_reporting_slot():
    from app.verification.governed_state import ExpectedObligations, validate_governed_projection, GovernedStateError
    from app.verification.propositions import ensure_propositions
    g = reporting_goal(operation=False)
    ensure_propositions(g)
    expected = ExpectedObligations.from_goal(g)
    p = Judge().evaluate(g, roots(), [])
    raw = p.model_dump()
    missing = report_props(p)[1].id
    raw['propositions'] = [p for p in raw['propositions'] if p['id'] != missing]
    from app.contracts.models import AnswerProjection
    with pytest.raises(GovernedStateError):
        validate_governed_projection(AnswerProjection.model_validate(raw), expected=expected)


def test_reporting_presentation_does_not_bless_sibling_cells_or_tool_prose():
    from types import SimpleNamespace
    from app.api.presentation import present
    source = roots()
    source[0].text_content = 'Unverified narrative: result 73129'
    source[0].structured_data['intent_summary'] = 'Unverified result 73129'
    g = reporting_goal(operation=False)
    projection = Judge().evaluate(g, source, [])
    view = present(SimpleNamespace(goal=g, projection=projection, artifacts=source,
        semantic=SimpleNamespace(language='en'), answer='verified observations', outcomes=[]))
    assert len(view.artifacts) == 2
    assert all([c.id for c in table.payload.columns] == ['home_runs'] for table in view.artifacts)
    assert '73129' not in view.model_dump_json()
    assert 'hits' not in view.model_dump_json()
    assert all('73129' not in c.text for c in projection.claims)


def test_conflicting_scalar_sources_are_not_selected_by_collection_order():
    source = roots()
    duplicate = source[0].model_copy(deep=True)
    duplicate.id = 'conflicting-same-scope'
    duplicate.structured_data['rows'][0]['home_runs'] = 38
    for export in duplicate.exports:
        export.payload['rows'] = duplicate.structured_data['rows']
    from tests.m212_receipt_fixture import with_receipt
    with_receipt(duplicate)
    for ordered in ([*source, duplicate], [duplicate, *source]):
        p = Judge().evaluate(reporting_goal(operation=False), ordered, [])
        assert p.goal_status.value != 'COMPLETE'
        assert 39 not in reported_values(p)
