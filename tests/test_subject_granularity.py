"""N-1: output units survive population binding, receipts, Judge and projection.

Synthetic replayable receipts are not live database/model evidence. Names and values
below are fixture data, never interpretation rules in production.
"""
from types import SimpleNamespace

import pytest

from app.contracts.models import (
    AnalysisRequest, Artifact, Constraint, EntityMention, Export, RelationSemantics,
    Scope, SemanticInterpretation,
)
from app.semantic.goals import RequirementBuilder
from app.verification.judge import Judge
from tests.m212_receipt_fixture import with_receipt
from tests.test_runtime_dataflow_phase5a import _stats_artifact

A, B, C = 660271, 592450, 605141
NAMES = ['大谷翔平', 'Aaron Judge', 'Mookie Betts']
METRICS = ['home_runs', 'hits', 'pa', 'avg', 'obp', 'slg', 'ops']


def goal(*, count=2, grain='PER_SUBJECT', text=None, kind='line', copies=1, names=None, role='BATTER'):
    names = names or NAMES[:count]
    text = text or f"2023年{'和'.join(names)}常规赛打击表现如何？"
    objective = 'overall regular-season batting performance for ' + ' and '.join(names) + ' in 2023'
    if kind == 'home_runs':
        objective = '2023 regular-season home runs for ' + ' and '.join(names)
    return RequirementBuilder().build(SemanticInterpretation(raw_query=text, language='zh',
        entities=[EntityMention(mention=n, type_hint='player') for n in names],
        analysis_requests=[AnalysisRequest(text=objective, objective=objective, user_span=text,
            analytical_role=role, relation_semantics=RelationSemantics(
                subject_refs=names, output_granularity=grain))],
        constraints=[Constraint(kind='season', value=2023), *[
            Constraint(kind='game_population', value='REGULAR_SEASON') for _ in range(copies)]]))


def identity(count=2, *, missing=(), ids=None, names=None):
    ids = ids or [A, B, C][:count]
    resolved = [dict(mention=n, name=n, id=i) for n, i in zip(names or NAMES, ids) if i not in missing]
    return Artifact(id='identity', kind='ENTITY_RESOLUTION', status='ACCEPTED',
        structured_data={'resolved': resolved}, exports=[Export(id='identities', type='PLAYER_ID_SET',
            payload={'ids': [r['id'] for r in resolved], 'namespace': 'MLBAM'})])


def line(i):
    return dict(home_runs={A: 44, B: 37, C: 39}.get(i, 12), hits=100 + i % 100,
                pa=500, avg=.275, obp=.36, slg=.55, ops=.91)


def stats(ids=(A, B), *, grouped=True, rows=None, name='stats', season=2023,
          game_types=('R',), role='BATTER', keys=None, extra_group=(), limit=None):
    field = 'batter_id' if role == 'BATTER' else 'pitcher_id'
    rows = rows if rows is not None else [dict(**({field: i} if grouped else {}), **line(i)) for i in ids]
    if not grouped and len(rows) > 1:
        rows = [{k: sum(row[k] for row in rows) for k in METRICS}]
    a = _stats_artifact(name, season=season, rows=rows)
    a.actual_scope = Scope(entities=list(map(str, ids)), season=season,
        game_population='REGULAR_SEASON', game_types=list(game_types), measures=keys or METRICS)
    with_receipt(a)
    if grouped:
        a.exports[0].type = 'RANKED_ENTITY_SET'  # actual analytics grouped-output carrier
    from app.capabilities.analytics.ir import AnalyticalQuery, validate_query
    from app.capabilities.analytics.compiler import SQLCompiler
    from app.capabilities.analytics.conformance import (
        attest, compare, digest, RequestedAnalyticalContract, SourceAvailability, scope_from_execution,
    )
    receipt = a.structured_data['execution_receipt']
    q = AnalyticalQuery.model_validate(receipt['validated_ir'])
    q.analytical_role = role
    for entity in q.entity_filters:
        entity.role = role
    q.group_by = ([field] if grouped else []) + list(extra_group)
    q.game_types = list(game_types)
    q.population = None
    q.limit = limit
    q = validate_query(q)
    compiled = SQLCompiler().compile(q, 'POSTGRES')
    attestation = attest(q, compiled, availability=SourceAvailability.model_validate(
        receipt['attestation']['availability']), executed=True)
    requested = RequestedAnalyticalContract(analytical_role=role, entities=attestation.entities,
        season=season, game_types=tuple(game_types), measures=tuple(keys or METRICS),
        group_by=tuple(q.group_by), limit=limit)
    receipt.update(validated_ir=q.model_dump(mode='json'), requested=requested.model_dump(mode='json'),
        attestation=attestation.model_dump(mode='json'), attestation_digest=digest(attestation),
        result_digest=digest(rows), conformance=compare(requested, attestation).model_dump(mode='json'))
    a.actual_scope = scope_from_execution(attestation)
    a.structured_data.update(sql=compiled.sql, actual_measures=list(attestation.measures))
    a.structured_data['intent_summary'] = 'overall regular-season batting performance in 2023'
    return a


def evaluate(g, *data, ident=None):
    return Judge().evaluate(g, [ident or identity(len([r for r in g.requirements if r.kind.value == 'ENTITY'])), *data], [])


def subjects(p):
    return [s for s in p.propositions if s.subject_granularity == 'PER_SUBJECT']


def view(g, p, artifacts):
    from app.api.presentation import present
    return present(SimpleNamespace(goal=g, projection=p, artifacts=artifacts,
        semantic=SimpleNamespace(language='zh'), answer='approved answer', outcomes=[]))


def split_subject_goal(*, grain='PER_SUBJECT', measures=None):
    labels = {'home_runs': '本垒打', 'hits': '安打'}
    texts = [f'2023年{name}常规赛{labels[measures[i]] if measures else "打击表现"}如何？'
             for i, name in enumerate(NAMES[:2])]
    requests = []
    for i, (name, text) in enumerate(zip(NAMES, texts)):
        objective = (f'2023 regular-season {measures[i].replace("_", " ")} for {name}' if measures
                     else f'overall regular-season batting performance for {name} in 2023')
        requests.append(AnalysisRequest(text=objective, objective=objective, user_span=text,
            analytical_role='BATTER', relation_semantics=RelationSemantics(
                subject_refs=[name], output_granularity=grain)))
    return RequirementBuilder().build(SemanticInterpretation(raw_query='；'.join(texts), language='zh',
        entities=[EntityMention(mention=name, type_hint='player') for name in NAMES[:2]],
        analysis_requests=requests, constraints=[Constraint(kind='season', value=2023),
            Constraint(kind='game_population', value='REGULAR_SEASON')]))


@pytest.mark.parametrize('grain', ['PER_SUBJECT', 'UNRESOLVED'])
def test_separately_owned_subject_requests_cannot_approve_a_pooled_scalar(grain):
    g = split_subject_goal(grain=grain)
    p = evaluate(g, stats(grouped=False))
    assert p.goal_status.value != 'COMPLETE' and not p.claims
    assert {s.subject for s in subjects(p)} == set(NAMES[:2])


@pytest.mark.parametrize('shared', [True, False])
def test_separately_owned_subject_requests_complete(shared):
    g = split_subject_goal()
    data = [stats()] if shared else [stats((i,), grouped=False, name=str(i)) for i in (A, B)]
    p = evaluate(g, *data)
    assert p.goal_status.value == 'COMPLETE'
    assert len(p.claims) == 2
    assert {tuple(c.scope.entities) for c in p.claims} == {(str(A),), (str(B),)}


def test_separately_owned_subject_requests_retain_only_supported_a():
    g = split_subject_goal()
    p = evaluate(g, stats((A,), grouped=False))
    assert p.goal_status.value == 'LIMITED' and len(p.claims) == 1
    assert p.claims[0].scope.entities == [str(A)]


@pytest.mark.parametrize('missing_b', [True, False])
def test_separately_owned_subject_metrics_do_not_leak_sibling_fields(missing_b):
    g = split_subject_goal(measures=['home_runs', 'hits'])
    rows = [dict(batter_id=A, home_runs=44, hits=151),
            dict(batter_id=B, home_runs=37, hits=None if missing_b else 98)]
    p = evaluate(g, stats(rows=rows, keys=['home_runs', 'hits']))
    assert p.goal_status.value == ('LIMITED' if missing_b else 'COMPLETE')
    # Analysis and inferred Measure Requirements both retain their original IDs.
    assert len(p.claims) == (2 if missing_b else 4)
    proofs = [proof for a in p.artifact_assessments for proof in a.subject_proofs]
    assert next(proof for proof in proofs if proof['subject_ids'] == [str(A)])['fields'] == ['home_runs']
    if not missing_b:
        assert next(proof for proof in proofs if proof['subject_ids'] == [str(B)])['fields'] == ['hits']


def test_shared_measure_keeps_each_independent_subject_owner():
    g = split_subject_goal(measures=['home_runs', 'home_runs'])
    p = evaluate(g, stats(keys=['home_runs']))
    assert p.goal_status.value == 'COMPLETE'
    assert all(a.status.value == 'SATISFIED' for a in p.requirement_assessments)
    measure_ids = {r.id for r in g.requirements if r.kind.value == 'MEASURE'}
    measure_slots = [s for s in subjects(p) if s.requirement_id in measure_ids]
    assert {s.subject for s in measure_slots} == set(NAMES[:2])


def test_subject_proof_cannot_retarget_an_authoritative_owner_to_its_sibling():
    from app.verification.propositions import ensure_propositions
    from app.verification.subjects import assess_subjects
    g = split_subject_goal()
    slots = ensure_propositions(g)
    a = next(s for s in slots if s.subject_granularity == 'PER_SUBJECT' and s.subject == NAMES[0])
    b = next(s for s in slots if s.subject_granularity == 'PER_SUBJECT' and s.subject == NAMES[1])
    wrong = a.model_copy(deep=True, update={'subject': b.subject,
                                          'subject_requirement_ids': b.subject_requirement_ids})
    assert not assess_subjects(g, [identity(), stats()], [wrong], {r.id for r in g.requirements})


def test_missing_role_metadata_cannot_bypass_unresolved_output_granularity():
    g = goal(grain='UNRESOLVED', role=None)
    from app.capabilities.analytics.conformance import contract_for_goal, ExecutionAttestation
    artifact = stats(grouped=False)
    ident = identity()
    p = evaluate(g, artifact, ident=ident)
    assert p.goal_status.value != 'COMPLETE' and not p.claims
    executed = ExecutionAttestation.model_validate(artifact.structured_data['execution_receipt']['attestation'])
    bound = contract_for_goal(g, [ident, artifact], executed)
    assert any('subject output' in issue for issue in bound.issues)


def test_known_output_units_work_without_requiring_role_metadata():
    g = goal(role=None)
    p = evaluate(g, stats())
    assert p.goal_status.value == 'COMPLETE' and len(p.claims) == 2


def test_original_legacy_shape_cannot_approve_pooled_scalar():
    g = goal(grain='UNRESOLVED')
    p = evaluate(g, stats(grouped=False))
    assert p.goal_status.value != 'COMPLETE'
    assert not p.claims
    assert len(subjects(p)) >= 2


@pytest.mark.parametrize('grain', ['UNRESOLVED', 'PER_SUBJECT'])
def test_three_subjects_cannot_be_replaced_by_a_pooled_scalar(grain):
    g = goal(count=3, grain=grain)
    p = evaluate(g, stats((A, B, C), grouped=False))
    assert p.goal_status.value != 'COMPLETE' and not p.claims
    assert len(subjects(p)) == 3


@pytest.mark.parametrize('count', [2, 3])
def test_individual_grouped_lines_complete(count):
    g = goal(count=count)
    p = evaluate(g, stats(ids=[A, B, C][:count]))
    assert p.goal_status.value == 'COMPLETE'
    assert len(subjects(p)) == count
    assert all(s.coverage.value == 'SATISFIED' for s in subjects(p))
    assert {v['entity'] for c in p.claims for v in c.answer_values} == set(NAMES[:count])


def test_separate_single_subject_scalars_complete():
    g = goal()
    p = evaluate(g, stats((A,), grouped=False, name='a'), stats((B,), grouped=False, name='b'))
    assert p.goal_status.value == 'COMPLETE'
    assert {tuple(c.scope.entities) for c in p.claims} == {(str(A),), (str(B),)}


@pytest.mark.parametrize('missing_identity', [False, True])
def test_a_survives_missing_b(missing_identity):
    g = goal()
    a = stats((A,), grouped=False)
    ident = identity(missing=(B,) if missing_identity else ())
    p = evaluate(g, a, ident=ident)
    assert p.goal_status.value == 'LIMITED'
    assert {c.answer_values[0]['entity'] for c in p.claims} == {NAMES[0]}
    assert next(s for s in subjects(p) if s.subject == NAMES[0]).coverage.value == 'SATISFIED'
    assert next(s for s in subjects(p) if s.subject == NAMES[1]).coverage.value != 'SATISFIED'
    assert len(view(g, p, [ident, a]).artifacts) == 1


@pytest.mark.parametrize('mutation', ['pooled', 'missing_identity_column', 'duplicate_identity',
    'out_of_scope_identity', 'wrong_season', 'wrong_role', 'wrong_population',
    'extra_grouping', 'extra_filter', 'wrong_qualification', 'restrictive_limit',
    'rejected', 'export_tamper', 'receipt_tamper'])
def test_wrong_subject_or_scope_never_complete(mutation):
    g = goal()
    if mutation == 'pooled':
        a = stats(grouped=False)
    elif mutation == 'missing_identity_column':
        a = stats(rows=[line(A), line(B)])
    elif mutation == 'duplicate_identity':
        a = stats(rows=[dict(batter_id=A, **line(A)), dict(batter_id=A, **line(A))])
    elif mutation == 'out_of_scope_identity':
        a = stats(rows=[dict(batter_id=A, **line(A)), dict(batter_id=C, **line(C))])
    elif mutation == 'wrong_season':
        a = stats(season=2024)
    elif mutation == 'wrong_role':
        a = stats(role='PITCHER')
    elif mutation == 'wrong_population':
        a = stats(game_types=('F', 'D'))
    elif mutation == 'extra_grouping':
        a = stats(rows=[dict(batter_id=i, p_throws='L', **line(i)) for i in (A, B)], extra_group=('p_throws',))
    elif mutation in {'extra_filter', 'wrong_qualification'}:
        from tests.test_typed_comparison_ga07 import _re_receipt
        a = stats()
        if mutation == 'extra_filter':
            _re_receipt(a, filters=[dict(op='COMPARE', field='p_throws', comparator='=', value='L')])
        else:
            _re_receipt(a, minimum=100)
    elif mutation == 'restrictive_limit':
        a = stats(limit=1)
    else:
        a = stats()
        if mutation == 'rejected':
            a.status = 'REJECTED'
        elif mutation == 'export_tamper':
            a.exports[0].payload['rows'] = [{'home_runs': 99}]
        else:
            a.structured_data['execution_receipt']['attestation']['group_by'] = []
    p = evaluate(g, a)
    assert p.goal_status.value != 'COMPLETE'
    assert not p.claims


def test_missing_b_group_retains_only_a():
    g = goal()
    a = stats(rows=[dict(batter_id=A, **line(A))])
    p = evaluate(g, a)
    assert p.goal_status.value == 'LIMITED'
    assert all(c.scope.entities == [str(A)] for c in p.claims)
    shown = view(g, p, [identity(), a]).artifacts[0].payload.rows
    assert len(shown) == 1 and shown[0]['batter_id'] == A


def test_conflicting_b_never_majority_selects_or_leaks_b_table():
    g = goal()
    a = stats(name='a')
    bad = stats((B,), grouped=False, name='b', rows=[{**line(B), 'home_runs': 999}])
    copies = [bad.model_copy(deep=True, update={'id': f'bad_{i}'}) for i in range(3)]
    p = evaluate(g, a, bad, *copies)
    assert p.goal_status.value == 'LIMITED'
    assert all(c.scope.entities == [str(A)] for c in p.claims)
    shown = view(g, p, [identity(), a, bad, *copies]).artifacts
    assert len(shown) == 1
    assert all(row['batter_id'] == A for row in shown[0].payload.rows)
    assert '999' not in str(shown)


def test_agreeing_repeated_receipts_are_one_witness_per_subject():
    g = goal(copies=3)
    a = stats(name='a')
    duplicate = a.model_copy(deep=True, update={'id': 'duplicate'})
    p = evaluate(g, a, duplicate)
    assert p.goal_status.value == 'COMPLETE'
    assert len(p.claims) == 2
    assert sum(len(a.subject_proofs) for a in p.artifact_assessments) == 2
    assert all(s.coverage.value == 'SATISFIED' for s in p.requirement_assessments)


def test_equivalent_integer_and_float_receipts_are_not_a_source_conflict():
    g = goal()
    a = stats(name='a')
    rows = [dict(batter_id=i, **{k: float(v) for k, v in line(i).items()}) for i in (A, B)]
    equivalent = stats(name='numeric_representation', rows=rows)
    p = evaluate(g, a, equivalent)
    assert p.goal_status.value == 'COMPLETE'
    assert len(p.claims) == 2


def test_one_canonical_subject_with_two_alias_requirements_is_not_two_players():
    aliases = [NAMES[0], 'Shohei Ohtani']
    g = goal(names=aliases)
    p = evaluate(g, stats((A,), grouped=False), ident=identity(ids=[A, A], names=aliases))
    assert p.goal_status.value == 'COMPLETE'
    assert all(c.scope.entities == [str(A)] for c in p.claims)
    assert all(s.coverage.value == 'SATISFIED' for s in subjects(p))


def test_legacy_individual_measure_requirements_inherit_output_units():
    g = goal(kind='home_runs', text='2023年大谷翔平和Aaron Judge常规赛本垒打数是多少？')
    a = stats(keys=['home_runs'], rows=[dict(batter_id=i, home_runs=line(i)['home_runs']) for i in (A, B)])
    p = evaluate(g, a)
    assert p.goal_status.value == 'COMPLETE'
    assert all(a.coverage.value == 'SATISFIED' for a in p.requirement_assessments)


def test_subject_lines_cannot_drop_a_requested_measure_when_contract_is_legacy():
    text = '报告大谷翔平和Aaron Judge在2023常规赛的本垒打数和安打数。'
    g = goal(text=text)
    a = stats(keys=['home_runs'], rows=[dict(batter_id=i, home_runs=line(i)['home_runs']) for i in (A, B)])
    p = evaluate(g, a)
    assert p.goal_status.value != 'COMPLETE'
    assert not p.claims


def test_distinct_subjects_keep_distinct_obligations_and_ids():
    g = goal()
    before = [r.model_dump(mode='json') for r in g.requirements]
    p = evaluate(g, stats())
    assert p.goal_status.value == 'COMPLETE'
    assert len({tuple(s.subject_requirement_ids) for s in subjects(p)}) == 2
    assert before == [r.model_dump(mode='json') for r in g.requirements]
    from app.verification.governed_state import validate_governed_projection, ExpectedObligations
    validate_governed_projection(p, expected=ExpectedObligations.from_goal(g))


def test_explicit_pooling_label_without_operation_cannot_grant_authority():
    g = goal(grain='POOLED')
    assert evaluate(g, stats(grouped=False)).goal_status.value != 'COMPLETE'
    assert evaluate(g, stats()).goal_status.value != 'COMPLETE'


def test_untrusted_need_or_artifact_granularity_cannot_override_requirements():
    from app.contracts.models import PlannerNeed
    g = goal()
    a = stats(grouped=False)
    a.structured_data['subject_granularity'] = 'PER_SUBJECT'
    a.structured_data['approved'] = True
    p = Judge().evaluate(g, [identity(), a], [PlannerNeed(id='pool', capability='local_analytics',
        objective='use pooled values as individual values', requires=[r.id for r in g.requirements])])
    assert p.goal_status.value != 'COMPLETE' and not p.claims


def test_unproven_requirement_cannot_approve_subject_results():
    g = goal()
    req = next(r for r in g.requirements if r.kind.value == 'ANALYSIS')
    req.source = 'inferred'
    req.provenance = None
    p = evaluate(g, stats())
    assert p.goal_status.value != 'COMPLETE' and not p.claims


def test_span_reference_ids_are_grounded_to_current_entity_requirements():
    text = '2023年大谷翔平和Aaron Judge常规赛打击表现如何？'
    g = RequirementBuilder().build(SemanticInterpretation(raw_query=text,
        entities=[EntityMention(mention=n, type_hint='player', ref=f'span-{i}') for i, n in enumerate(NAMES[:2])],
        analysis_requests=[AnalysisRequest(text=text, user_span=text, analytical_role='BATTER',
            relation_semantics=RelationSemantics(output_granularity='PER_SUBJECT', subject_refs=['span-0', 'span-1']))],
        constraints=[Constraint(kind='season', value=2023), Constraint(kind='game_population', value='REGULAR_SEASON')]))
    req = next(r for r in g.requirements if r.kind.value == 'ANALYSIS')
    assert req.relation_semantics.subject_refs == NAMES[:2]
    assert req.relation_semantics.output_granularity == 'PER_SUBJECT'
    assert evaluate(g, stats()).goal_status.value == 'COMPLETE'


def test_unknown_subject_references_cannot_delete_mandatory_subjects():
    g = goal(grain='UNRESOLVED')
    req = next(r for r in g.requirements if r.kind.value == 'ANALYSIS')
    req.relation_semantics.subject_refs = ['unknown-ref']
    p = evaluate(g, stats())
    assert p.goal_status.value != 'COMPLETE'
    assert len(subjects(p)) == 2


def test_subject_scope_mismatch_is_rejected_by_whole_state_validation():
    from app.verification.governed_state import (
        ExpectedObligations, GovernedStateError, governed_limited_fallback, validate_governed_projection,
    )
    g = goal(); p = evaluate(g, stats())
    data = p.model_dump(mode='json')
    data['claims'][0]['scope']['entities'] = [str(A), str(B)]
    candidate = type(p).model_validate(data)
    expected = ExpectedObligations.from_goal(g)
    with pytest.raises(GovernedStateError, match='subject footprint'):
        validate_governed_projection(candidate, expected=expected)
    fallback = governed_limited_fallback(candidate, expected=expected)
    assert fallback.goal_status.value != 'COMPLETE'
    assert len(fallback.claims) == 1


def test_canonical_pack_uses_bound_output_granularity_not_entity_count_or_hint():
    from app.capabilities.analytics.canonical import build_canonical_query
    intent = dict(entities=[dict(role='BATTER', ids=[A, B])], season=2023,
                  objective='overall batting performance', for_comparison=True)
    assert build_canonical_query(intent).group_by == []
    intent['bound_obligations'] = {'group_by': ['batter_id']}
    assert build_canonical_query(intent).group_by == ['batter_id']


def test_known_single_subject_granularity_preserves_native_proposition_paths():
    from app.verification.propositions import ensure_propositions
    g = goal(count=1, grain='PER_SUBJECT')
    assert all(p.subject_granularity is None for p in ensure_propositions(g))
    assert evaluate(g, stats((A,), grouped=False)).goal_status.value == 'COMPLETE'


def test_single_subject_legacy_line_still_completes():
    g = goal(count=1)
    p = evaluate(g, stats((A,), grouped=False), ident=identity(count=1))
    assert p.goal_status.value == 'COMPLETE'


@pytest.mark.parametrize('copies', [1, 2, 3])
def test_duplicate_analysis_requirements_preserve_all_owners(copies):
    g = goal()
    req = next(r for r in g.requirements if r.kind.value == 'ANALYSIS')
    for index in range(1, copies):
        g.requirements.append(req.model_copy(deep=True, update={'id': f'duplicate_req_{index}', 'required': index != 2}))
    p = evaluate(g, stats())
    assert p.goal_status.value == 'COMPLETE'
    assert len(subjects(p)) == 2 * copies
    assert {a.requirement_id for a in p.requirement_assessments} == {r.id for r in g.requirements}
    assert all(a.coverage.value == 'SATISFIED' for a in p.requirement_assessments)


def operation_goal(kind, *, report=False, direction='DESC'):
    from app.contracts.operations import OperationContract
    text = f"2023年大谷翔平和Aaron Judge常规赛本垒打的{kind}"
    operands = [{'scope': dict(measure='home_runs', analytical_role='BATTER',
        subjects=[n], season=2023, game_types=['R'])} for n in NAMES[:2]]
    requests = [AnalysisRequest(text=text, user_span=text, analytical_role='BATTER',
        operation_contract=OperationContract(key='combine', kind=kind, output_field='result',
            operands=operands, parameters={'direction': direction} if kind == 'RANK' else {}))]
    if report:
        requests.insert(0, AnalysisRequest(text=text, user_span=text, analytical_role='BATTER',
            reporting_contract={'cells': [o['scope'] for o in operands]}))
    return RequirementBuilder().build(SemanticInterpretation(raw_query=text, language='zh',
        entities=[EntityMention(mention=n, type_hint='player') for n in NAMES[:2]],
        analysis_requests=requests, constraints=[Constraint(kind='season', value=2023),
            Constraint(kind='game_population', value='REGULAR_SEASON')]))


def computed(g, data):
    from app.capabilities.python_compute import PythonComputeTool
    from app.contracts.models import InputBinding, ToolRequest
    from tests.test_runtime_dataflow_phase5a import _ctx
    ctx = _ctx([identity(), *data]); ctx.goal = g
    request = ToolRequest(id='compute', need_id='combine', capability='python_compute', objective=g.raw_query,
        bindings=[InputBinding(need_id='combine', artifact_id=a.id, export_id=a.exports[0].id,
            export_type='STATISTICAL_RESULT', source_need_id=a.id) for a in data])
    result = PythonComputeTool().execute(request, ctx)
    assert result.artifact is not None, result.outcome
    return result.artifact


@pytest.mark.parametrize('kind,expected', [('SUM', 81), ('MEAN', 40.5), ('MIN', 37), ('MAX', 44)])
@pytest.mark.parametrize('report', [False, True])
def test_explicit_aggregation_works_without_collapsing_individual_reports(kind, expected, report):
    g = operation_goal(kind, report=report)
    data = [stats((i,), grouped=False, name=f'p_{i}', keys=['home_runs'],
                  rows=[{'home_runs': line(i)['home_runs']}]) for i in (A, B)]
    derived = computed(g, data)
    p = evaluate(g, *data, derived)
    assert p.goal_status.value == 'COMPLETE'
    assert derived.structured_data['rows'][0]['result'] == expected
    reported = [v for c in p.claims for v in c.answer_values if v.get('metric') == 'home_runs']
    if report:
        assert {v['entity'] for v in reported} == set(NAMES[:2])
        assert {v['value'] for v in reported} == {44, 37}
    else:
        assert not reported  # internal operands are not invented reporting obligations


@pytest.mark.parametrize('direction', ['ASC', 'DESC'])
def test_explicit_ranking_uses_governed_individual_operands(direction):
    g = operation_goal('RANK', direction=direction)
    data = [stats((i,), grouped=False, name=f'p_{i}', keys=['home_runs'],
                  rows=[{'home_runs': line(i)['home_runs']}]) for i in (A, B)]
    derived = computed(g, data)
    p = evaluate(g, *data, derived)
    assert p.goal_status.value == 'COMPLETE'
    assert derived.structured_data['rows']


def test_typed_numeric_comparison_still_completes_from_individual_roots():
    from tests.test_typed_comparison_ga07 import _case
    g, data, needs = _case(lhs_id=A, rhs_id=B, lhs_year=2023, rhs_year=2023,
                           lhs_value=44, rhs_value=37, key='home_runs')
    p = Judge().evaluate(g, data, needs)
    assert p.goal_status.value == 'COMPLETE'
    assert any(c.comparison_support for c in p.claims)


def test_untyped_comparative_predicate_is_not_proven_by_individual_rows():
    from app.contracts.models import RelationKind
    g = goal(text='2023常规赛，大谷翔平的本垒打数是否高于Aaron Judge？')
    req = next(r for r in g.requirements if r.kind.value == 'ANALYSIS')
    req.relation_semantics.relation_kind = RelationKind.COMPARISON
    req.relation_semantics.relation_kinds = [RelationKind.COMPARISON]
    p = evaluate(g, stats())
    assert p.goal_status.value != 'COMPLETE'
    assert not any(c.comparison_support for c in p.claims)


@pytest.mark.parametrize('wrong_b_measure', [False, True])
def test_subject_and_measure_cells_do_not_collapse(wrong_b_measure):
    text = '报告大谷翔平2023常规赛本垒打数和Aaron Judge2023常规赛安打数。'
    g = RequirementBuilder().build(SemanticInterpretation(raw_query=text, language='zh',
        entities=[EntityMention(mention=n, type_hint='player') for n in NAMES[:2]],
        analysis_requests=[AnalysisRequest(text=text, user_span=text, analytical_role='BATTER',
            reporting_contract={'cells': [dict(measure=key, analytical_role='BATTER',
                subjects=[name], season=2023, game_types=['R'])
                for name, key in zip(NAMES[:2], ['home_runs', 'hits'])]})],
        constraints=[Constraint(kind='season', value=2023),
                     Constraint(kind='game_population', value='REGULAR_SEASON')]))
    a = stats((A,), grouped=False, name='a', keys=['home_runs'], rows=[{'home_runs': 44}])
    key = 'home_runs' if wrong_b_measure else 'hits'
    b = stats((B,), grouped=False, name='b', keys=[key], rows=[{key: 98}])
    p = evaluate(g, a, b)
    assert p.goal_status.value == ('LIMITED' if wrong_b_measure else 'COMPLETE')
    values = [(v['entity'], v['metric'], v['value']) for c in p.claims for v in c.answer_values]
    assert (NAMES[0], 'home_runs', 44) in values
    assert ((NAMES[1], 'hits', 98) in values) != wrong_b_measure


def test_reporting_cell_cannot_pool_two_individuals():
    g = operation_goal('SUM', report=True)
    g.requirements = [r for r in g.requirements if not r.operation_contract]
    req = next(r for r in g.requirements if r.reporting_contract)
    req.reporting_contract = type(req.reporting_contract).model_validate({'cells': [dict(
        measure='home_runs', analytical_role='BATTER', subjects=NAMES[:2], season=2023, game_types=['R'])]})
    p = evaluate(g, stats(grouped=False, keys=['home_runs'], rows=[{'home_runs': 81}]))
    assert p.goal_status.value != 'COMPLETE' and not p.claims


def test_pooled_root_cannot_be_relabelled_as_an_individual_mean_operand():
    from app.verification.governed_operations import OperationAdmission
    from app.contracts.operations import OperandCell
    g = operation_goal('MEAN')
    req = next(r for r in g.requirements if r.operation_contract)
    scope = req.operation_contract.operands[0].scope.model_copy(update={'subjects': NAMES[:2]})
    pooled = stats(grouped=False, keys=['home_runs'], rows=[{'home_runs': 81}])
    from app.verification.artifact_assessment import _identities
    admission = OperationAdmission([identity(), pooled], g, {r.id for r in g.requirements},
        _identities(g, [identity()]))
    cell = OperandCell(artifact_id=pooled.id, export_id=pooled.exports[0].id, row_index=0, field='home_runs')
    with pytest.raises(ValueError, match='separate governed root cells'):
        admission.root(pooled, cell, scope)


def test_subject_dependency_does_not_become_independent_partial_claim():
    from app.verification.propositions import ensure_propositions
    g = goal()
    props = ensure_propositions(g)
    a, b = [p for p in props if p.subject_granularity == 'PER_SUBJECT']
    a.depends_on = [b.id]
    p = evaluate(g, stats((A,), grouped=False))
    assert p.goal_status.value != 'COMPLETE' and not p.claims


def test_projection_keeps_only_approved_subject_columns_not_tool_prose():
    g = goal()
    a = stats()
    a.text_content = 'invented pooled total 9999'
    a.structured_data['rows'][0]['malicious_prose'] = '9999 pooled individual claim'
    from app.capabilities.analytics.conformance import digest
    a.structured_data['execution_receipt']['result_digest'] = digest(a.structured_data['rows'])
    a.exports[0].payload['rows'] = a.structured_data['rows']
    p = evaluate(g, a)
    assert p.goal_status.value == 'COMPLETE'
    assert '9999' not in ' '.join(c.text for c in p.claims)
    shown = view(g, p, [identity(), a])
    assert 'malicious_prose' not in str(shown.artifacts)
    assert '9999' not in str(shown.artifacts)
    assert 'execution_receipt' not in shown.model_dump_json()


@pytest.mark.parametrize('selector,expected', [([NAMES[0]], A), ([NAMES[1]], B)])
def test_scalar_selector_selects_owned_subject_not_whole_bound_export(selector, expected):
    from app.capabilities.analytics.scalar_inputs import scalar_input_contract
    from app.contracts.models import ToolRequest
    from tests.test_runtime_dataflow_phase5a import _ctx
    g = operation_goal('SUM', report=True)
    ctx = _ctx([identity()]); ctx.goal = g
    req = ToolRequest(id='select', need_id='input', capability='local_analytics', objective='selector only',
        inputs={'subjects': selector, 'analytical_requirement_ids': [r.id for r in g.requirements]})
    bound, intent, selected = scalar_input_contract(req, ctx, dict(entities=[dict(ids=[A, B])]))
    assert bound.entities[0].ids == (expected,)
    assert intent['entities'][0]['ids'] == [expected]


@pytest.mark.parametrize('selector', [[NAMES[2]], [NAMES[0], NAMES[1]], [], ['unknown'], 'Aaron Judge'])
def test_scalar_selector_cannot_create_unrequested_or_pooled_scope(selector):
    from app.capabilities.analytics.scalar_inputs import scalar_input_contract
    from app.contracts.models import ToolRequest
    from tests.test_runtime_dataflow_phase5a import _ctx
    g = operation_goal('SUM', report=True)
    ctx = _ctx([identity(count=3)]); ctx.goal = g
    req = ToolRequest(id='select', need_id='input', capability='local_analytics', objective='selector only',
        inputs={'subjects': selector, 'analytical_requirement_ids': [r.id for r in g.requirements]})
    with pytest.raises(ValueError):
        scalar_input_contract(req, ctx, dict(entities=[dict(ids=[A, B, C])]))


def test_output_contract_tamper_rejected_and_fallback_conserves_source_slots():
    from app.verification.governed_state import (
        ExpectedObligations, GovernedStateError, governed_limited_fallback, validate_governed_projection,
    )
    g = goal()
    p = evaluate(g, stats())
    expected = ExpectedObligations.from_goal(g)
    data = p.model_dump(mode='json')
    data['propositions'][-1]['subject_requirement_ids'] = []
    candidate = type(p).model_validate(data)
    with pytest.raises(GovernedStateError):
        validate_governed_projection(candidate, expected=expected)
    recovered = governed_limited_fallback(candidate, expected=expected)
    assert recovered.goal_status.value != 'COMPLETE'
    assert len(subjects(recovered)) == 2
