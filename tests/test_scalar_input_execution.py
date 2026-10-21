"""Typed requested scalar inputs reach Safe IR without reinterpreting tool prose."""
from copy import deepcopy
from datetime import date
import pytest
from app.capabilities.analytics.conformance import SourceAvailability
from app.capabilities.analytics.executor import QueryResult
from app.capabilities.analytics.tool import LocalAnalyticsTool
from app.contracts.models import Constraint, ConversationState, ToolRequest
from app.runtime.context import ExecutionContext
from tests.test_analytical_conformance_m212 import FixedIR
from tests.test_reporting_obligations import reporting_goal
from tests.governed_operation_fixture import roots


class ScalarSource:
    def __init__(self):
        self.calls = []

    def run(self, sql, source):
        self.calls.append((sql, source))
        return QueryResult([], [{'home_runs': 39, 'avg': 0.307, 'hits': 179, '__execution_observations': 1000}], 1, source, 0, 'synthetic')

    def availability(self, source, relation):
        return SourceAvailability(source=source, relation=relation, known=True,
            start=date(2023, 1, 1), end=date(2024, 12, 31), seasons=(2023, 2024))


def execute(*, mutation=None, hint=2023, report=True):
    goal = reporting_goal(report=report)
    ids = [r.id for r in goal.requirements if r.kind.value == 'ANALYSIS'
           or (r.kind.value == 'SEASON' and r.value == 2023)]
    ctx = ExecutionContext(goal=goal, conversation=ConversationState(session_id='typed-input'))
    raw = deepcopy(roots()[0].structured_data['execution_receipt']['validated_ir'])
    if mutation:
        mutation(raw)
    request = ToolRequest(need_id='root', capability='local_analytics', objective='Obtain the requested scalar input.',
        inputs={'intent': {'season': hint, 'measure': 'home_runs'},
                'bound_user_intent': goal.raw_query, 'raw_query': goal.raw_query,
                'analytical_requirement_ids': ids,
                'analytical_requirements': [r.model_dump(mode='json') for r in goal.requirements]})
    source = ScalarSource()
    result = LocalAnalyticsTool(source, FixedIR(raw), parquet_archive='/tmp/synthetic.parquet').execute(request, ctx)
    return result, source


@pytest.mark.parametrize('report', [True, False])
def test_reporting_or_operation_owns_its_scalar_input_not_the_planner_objective(report):
    result, source = execute(report=report)
    assert result.artifact is not None, result.outcome
    assert result.artifact.status == 'ACCEPTED'
    assert len(source.calls) == 1
    assert result.artifact.structured_data['execution_receipt']['requested']['season'] == 2023
    assert result.artifact.provenance.get('scalar_input_contract')


@pytest.mark.parametrize('field,value', [('season', 2024), ('game_types', ['S']), ('group_by', ['season'])])
def test_typed_input_scope_drift_still_never_reaches_sql(field, value):
    result, source = execute(mutation=lambda raw: raw.update({field: value}))
    assert not source.calls
    assert result.artifact is None


def test_unrelated_unsupported_reporting_owner_does_not_hijack_legacy_work():
    from app.capabilities.analytics.scalar_inputs import scalar_input_contract
    from app.contracts.models import AnalysisRequest, SemanticInterpretation
    from app.semantic.goals import RequirementBuilder
    first, second = 'Show 2023 batting performance.', 'Report 2023 RBI.'
    g = RequirementBuilder().build(SemanticInterpretation(raw_query=first + ' ' + second,
        analysis_requests=[AnalysisRequest(text=first, user_span=first, analytical_role='BATTER'),
            AnalysisRequest(text=second, user_span=second, reporting_contract={'cells': [
                {'measure': 'rbi', 'season': 2023}]})], constraints=[Constraint(kind='season', value=2023)]))
    ctx = ExecutionContext(goal=g, conversation=ConversationState(session_id='mixed'))
    req = ToolRequest(need_id='mixed', capability='local_analytics', objective=first, inputs={
        'analytical_requirement_ids': [r.id for r in g.requirements]})
    assert scalar_input_contract(req, ctx, {'season': 2023, 'measure': 'batting performance', 'entities': []}) is None


def test_pure_non_derivable_reporting_need_reports_unsupported_measure():
    from app.contracts.models import AnalysisRequest, SemanticInterpretation
    from app.semantic.goals import RequirementBuilder
    text = 'Report 2025 RBI total.'
    g = RequirementBuilder().build(SemanticInterpretation(raw_query=text,
        analysis_requests=[AnalysisRequest(text=text, user_span=text, reporting_contract={'cells': [
            {'measure': 'rbi', 'season': 2025}]})], constraints=[Constraint(kind='season', value=2025)]))
    ctx = ExecutionContext(goal=g, conversation=ConversationState(session_id='pure-rbi'))
    req = ToolRequest(need_id='pure-rbi', capability='local_analytics', objective=text, inputs={
        'intent': {'season': 2025, 'measure': 'rbi'},
        'analytical_requirement_ids': [r.id for r in g.requirements]})
    result = LocalAnalyticsTool(ScalarSource(), FixedIR({}),
        parquet_archive='/tmp/synthetic.parquet').execute(req, ctx)
    assert result.artifact is None
    assert result.outcome.status == 'BLOCKED'
    assert result.outcome.code == 'UNSUPPORTED_MEASURE'


def test_mixed_legacy_batch_hint_does_not_make_typed_unavailable_output_a_legacy_obligation():
    from app.contracts.models import AnalysisRequest, SemanticInterpretation
    from app.semantic.goals import RequirementBuilder
    from app.capabilities.analytics.canonical import build_canonical_query
    from app.verification.judge import Judge
    first, second = 'Show 2023 batting performance.', 'Report 2023 RBI.'
    g = RequirementBuilder().build(SemanticInterpretation(raw_query=first + ' ' + second,
        analysis_requests=[AnalysisRequest(text=first, user_span=first, analytical_role='BATTER'),
            AnalysisRequest(text=second, user_span=second, reporting_contract={'cells': [
                {'measure': 'rbi', 'season': 2023}]})], constraints=[Constraint(kind='season', value=2023)]))
    ctx = ExecutionContext(goal=g, conversation=ConversationState(session_id='mixed-tool'))
    req = ToolRequest(need_id='mixed', capability='local_analytics', objective='Retrieve both outputs.', inputs={
        'intent': {'season': 2023, 'measure': ['rbi', 'avg', 'home_runs']},
        'raw_query': g.raw_query, 'bound_user_intent': g.raw_query,
        'analytical_requirement_ids': [r.id for r in g.requirements],
        'analytical_requirements': [r.model_dump(mode='json') for r in g.requirements]})
    query = build_canonical_query({'entities': [{'role': 'BATTER', 'ids': [999001]}], 'season': 2023})
    query.entity_filters = []
    source = ScalarSource()
    result = LocalAnalyticsTool(source, FixedIR(query.model_dump(mode='json')),
        parquet_archive='/tmp/synthetic.parquet').execute(req, ctx)
    assert result.artifact is not None, result.outcome
    p = Judge().evaluate(g, [result.artifact], [])
    assert p.goal_status.value == 'LIMITED'
    assert p.claims
    assert next(p for p in p.propositions if p.reporting_index is not None).coverage.value == 'UNSUPPORTED'


@pytest.mark.parametrize('other,complete', [('rbi', False), ('avg', True)])
def test_batched_reporting_work_keeps_every_user_slot_without_blocking_known_values(other, complete):
    from app.contracts.models import AnalysisRequest, SemanticInterpretation
    from app.semantic.goals import RequirementBuilder
    from app.verification.judge import Judge
    text = f'Report 2023 home runs and {other}.'
    g = RequirementBuilder().build(SemanticInterpretation(raw_query=text,
        analysis_requests=[AnalysisRequest(text=text, user_span=text, reporting_contract={'cells': [
            {'measure': 'home_runs', 'season': 2023}, {'measure': other, 'season': 2023}]})],
        constraints=[Constraint(kind='season', value=2023)]))
    ctx = ExecutionContext(goal=g, conversation=ConversationState(session_id='batch'))
    req = ToolRequest(need_id='batch', capability='local_analytics', objective=text, inputs={
        'intent': {'season': 2023, 'measure': ['home_runs', other]},
        'analytical_requirement_ids': [r.id for r in g.requirements]})
    source = ScalarSource()
    from app.capabilities.analytics.canonical import build_canonical_query
    query = build_canonical_query({'entities': [{'role': 'BATTER', 'ids': [999001]}], 'season': 2023})
    query.entity_filters = []  # synthetic whole-population IR, including a verified AVG recipe
    raw = query.model_dump(mode='json')
    raw['game_types'] = next(r.reporting_contract.cells[0].game_types for r in g.requirements if r.reporting_contract) or []
    result = LocalAnalyticsTool(source, FixedIR(raw),
        parquet_archive='/tmp/synthetic.parquet').execute(req, ctx)
    assert result.artifact is not None, result.outcome
    assert len(source.calls) == 1
    p = Judge().evaluate(g, [result.artifact], [])
    assert p.goal_status.value == ('COMPLETE' if complete else 'LIMITED')
    assert len([s for s in p.propositions if s.reporting_index is not None]) == 2
    values = [v['value'] for c in p.claims for v in c.answer_values]
    assert 39 in values
    assert (0.307 in values) == complete


def test_legacy_bound_identity_is_not_promoted_before_binder_validation():
    from app.contracts.models import AnalysisRequest, Artifact, EntityMention, Export, InputBinding, SemanticInterpretation
    from app.semantic.goals import RequirementBuilder
    text = 'Show Shohei Ohtani 2023 batting performance.'
    goal = RequirementBuilder().build(SemanticInterpretation(raw_query=text,
        entities=[EntityMention(mention='Shohei Ohtani', type_hint='player')],
        analysis_requests=[AnalysisRequest(text=text, user_span=text, analytical_role='BATTER')],
        constraints=[Constraint(kind='season', value=2023)]))
    identity = Artifact(kind='ENTITY_RESOLUTION', status='ACCEPTED', exports=[Export(
        id='identity', type='PLAYER_ID_SET', payload={'ids': [660271], 'names': ['Shohei Ohtani']})])
    ctx = ExecutionContext(goal=goal, conversation=ConversationState(session_id='legacy'), artifacts=[identity])
    req = ToolRequest(need_id='stats', capability='local_analytics', objective=text,
        bindings=[InputBinding(need_id='stats', export_id='identity', export_type='PLAYER_ID_SET', artifact_id=identity.id)],
        inputs={'raw_query': text, 'bound_user_intent': text,
            'intent': {'season': 2023}, 'analytical_requirement_ids': [r.id for r in goal.requirements],
            'analytical_requirements': [r.model_dump(mode='json') for r in goal.requirements]})
    source = ScalarSource()
    result = LocalAnalyticsTool(source, None, parquet_archive='/tmp/synthetic.parquet').execute(req, ctx)
    assert result.artifact is not None, result.outcome
    assert source.calls


def test_planner_cannot_choose_an_unrequested_scalar_scope():
    result, source = execute(hint=2022)
    assert not source.calls
    assert result.artifact is None
