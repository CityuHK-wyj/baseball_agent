"""Stage 4 authority roots and interpreted evidence intent, not user-language cue rules."""
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.capabilities.analytics.authority import resolve_authority
from app.capabilities.analytics.conformance import independent_analytical_partitions
from app.contracts.models import (
    AnswerProposition, AssertionRequirement, AuthorityNeed, EvidenceIntent, Goal,
    RequirementAuthority, SemanticInterpretation, UserRequirement,
)
from app.knowledge.store import KnowledgeItem, KnowledgeStore
from app.semantic.goals import RequirementBuilder
from app.semantic.interpreter import SemanticInterpreter
from app.semantic.provenance import provenance_valid
from app.verification.evidence_semantics import derive_assertion_expectation, derive_authority_expectation
from app.verification.judge import Judge
from app.verification.propositions import build_propositions
from tests.test_partial_retention_m212r4 import scenario
from tests.test_web_evidence_semantics_wsa01_02 import _prose_proposition


class InterpreterReply:
    configured = True

    def __init__(self, data):
        self.data = data
        self.payload = None

    def complete_json(self, **kwargs):
        self.payload = json.loads(kwargs['user'])
        return SimpleNamespace(data=deepcopy(self.data))


def interpreted(raw, normalized, *, quote=None, intent=None):
    entry = dict(text=normalized, objective=normalized, user_span=quote or raw)
    if intent is not None:
        entry['evidence_intent'] = intent
    llm = InterpreterReply({'understanding': normalized, 'evidence_requests': [entry]})
    semantic = SemanticInterpreter(llm).interpret(raw)
    goal = RequirementBuilder().build(semantic)
    return semantic, goal


def test_paraphrased_meaning_does_not_have_to_be_a_literal_user_quote():
    raw = 'Is he on the injured list now?'
    normalized = 'current injury status'
    semantic, goal = interpreted(raw, normalized)
    request = next(r for r in goal.requirements if r.kind.value == 'EVIDENCE')
    assert normalized not in raw
    assert semantic.evidence_requests[0].objective == normalized
    assert request.provenance.user_span == raw
    assert provenance_valid(request.model_dump(mode='json'), bound_message=raw)
    assert request.provenance.is_governed_frozen
    with pytest.raises(TypeError):
        request.provenance.bound_message = 'a different user turn'


@pytest.mark.parametrize('broken', ['span', 'message', 'digest', 'assistant'])
def test_ancestry_tampering_cannot_define_requested_truth(broken):
    _, goal = interpreted('Is he on the injured list now?', 'current injury status')
    payloads = [r.model_dump(mode='json') for r in goal.requirements]
    req = next(r for r in payloads if r['kind'] == 'EVIDENCE')
    if broken == 'span':
        req['provenance']['user_span'] = 'from a previous assistant answer'
    elif broken == 'message':
        req['provenance']['bound_message'] = 'different turn'
    elif broken == 'digest':
        req['provenance']['message_digest'] = 'not the message digest'
    else:
        req['provenance']['origin'] = 'ASSISTANT_CONTEXT'
    approved, unproven, _ = resolve_authority(payloads, bound_user_message=goal.raw_query)
    assert req['id'] not in {r['id'] for r in approved}
    assert req['id'] in {r['id'] for r in unproven}


@pytest.mark.parametrize('source', ['user', 'clarification', 'planner', 'tool'])
def test_source_label_alone_does_not_grant_judge_authority(source):
    goal, artifact, needs = scenario('Explain the unknown context', False)
    # Exercise the real runtime Goal, not the old synthetic rooted Goal subclass.
    payload = goal.model_dump()
    for req in payload['requirements']:
        req['source'] = source
        req['provenance'] = None
    if source in {'planner', 'tool'}:
        assert not resolve_authority(payload['requirements'], bound_user_message=goal.raw_query)[0]
        with pytest.raises(ValueError):
            Goal.model_validate(payload)
        return
    unbound = Goal.model_validate(payload)
    projection = Judge().evaluate(unbound, [artifact], needs)
    assert not projection.claims
    assert projection.goal_status.value != 'COMPLETE'
    assert all(a.authority_status == RequirementAuthority.UNPROVEN for a in projection.requirement_assessments)


def test_partition_keeps_actual_user_message_as_the_authority_root():
    goal, _, _ = scenario('Explain the unknown context', False)
    partitions = independent_analytical_partitions(goal)
    assert partitions
    for local in partitions:
        assert local.raw_query == goal.raw_query
        assert local.analytical_partition_clause
        for req in local.requirements:
            assert provenance_valid(req.model_dump(mode='json'), bound_message=goal.raw_query)


def test_context_entity_identity_is_not_assistant_factual_authority():
    from app.contracts.models import AnalysisRequest, EntityReference, IdentityLevel
    semantic = SemanticInterpretation(
        raw_query='What about him in 2024?',
        analysis_requests=[AnalysisRequest(text='batting performance', user_span='What about him in 2024?')],
        entity_references=[EntityReference(surface='him', type_hint='player',
            canonical_name='Mookie Betts', antecedent='Mookie Betts', carried_from_goal='prior',
            status=IdentityLevel.RESOLVED)],
    )
    goal = RequirementBuilder().build(semantic)
    entity = next(r for r in goal.requirements if r.kind.value == 'ENTITY')
    assert entity.provenance.origin == 'DERIVED_CONTEXT'
    assert entity.provenance.context_antecedent == 'Mookie Betts'
    assert provenance_valid(entity.model_dump(mode='json'), bound_message=semantic.raw_query)
    forged = entity.model_dump(mode='json')
    forged.update(kind='EVIDENCE', statement='Mookie Betts is injured')
    assert not provenance_valid(forged, bound_message=semantic.raw_query)


@pytest.mark.parametrize('rejected_role', [
    'NOT fan reaction', 'not reporting or rumors', 'not a prediction',
    'fan reaction is not what I asked for',
])
def test_rejected_role_mentions_do_not_loosen_expectations(rejected_role):
    prop = _prose_proposition('Current injury status; ' + rejected_role,
                             'Current injury status; ' + rejected_role)
    assert derive_authority_expectation(prop).need == AuthorityNeed.OFFICIAL_STATE
    assert derive_assertion_expectation(prop).minimum_strength == AssertionRequirement.ASSERTED


@pytest.mark.parametrize('mode,authority,strength', [
    ('CONFIRMED_STATE', AuthorityNeed.OFFICIAL_STATE, AssertionRequirement.ASSERTED),
    ('REPORTED_ACCOUNT', AuthorityNeed.REPORTED_ACCOUNT, AssertionRequirement.SPECULATIVE_ALLOWED),
    ('FIRSTHAND_ACCOUNT', AuthorityNeed.FIRSTHAND_ACCOUNT, AssertionRequirement.SPECULATIVE_ALLOWED),
    ('PREDICTION', AuthorityNeed.OFFICIAL_STATE, AssertionRequirement.SPECULATIVE_ALLOWED),
])
def test_typed_intent_survives_request_requirement_proposition_boundary(mode, authority, strength):
    intent = {'mode': mode, 'information_class': 'INJURY'}
    _, goal = interpreted('Tell me about his injury', 'injury information', intent=intent)
    props = build_propositions(goal)
    prop = next(p for p in props if p.kind.value in {'FACT', 'EVENT'})
    assert prop.evidence_intent == EvidenceIntent(**intent)
    assert prop.authority_expectation.need == authority
    assert prop.assertion_expectation.minimum_strength == strength


@pytest.mark.parametrize('intent', [{'mode': 'invented'}, {'mode': True}, {'information_class': 'unknown'}])
def test_malformed_intent_falls_back_to_strict_fact_policy(intent):
    _, goal = interpreted('Current injury status', 'Current injury status', intent=intent)
    prop = next(p for p in build_propositions(goal) if p.kind.value in {'FACT', 'EVENT'})
    assert prop.assertion_expectation.minimum_strength == AssertionRequirement.ASSERTED
    assert prop.authority_expectation.need == AuthorityNeed.OFFICIAL_STATE


def test_interpreter_receives_bounded_stable_semantics_not_cached_mutable_facts():
    store = KnowledgeStore()
    store.items.insert(0, KnowledgeItem(id='mutable_injury', type='INJURY', title='injury status',
                                      summary='Mookie Betts is injured', status='ACTIVE'))
    llm = InterpreterReply({'understanding': 'Current injury status'})
    SemanticInterpreter(llm, knowledge=store).interpret('Current injury status')
    context = llm.payload['stable_semantic_knowledge']
    assert len(context) <= 8
    assert any(item['id'] == 'semantic_evidence_intents' for item in context)
    assert 'mutable_injury' not in json.dumps(context)
    assert all(next(i for i in store.items if i.id == item['id']).knowledge_class == 'SEMANTIC'
               for item in context)


@pytest.mark.parametrize('request_kind', ['analysis_requests', 'evidence_requests', 'user_assertions'])
def test_assistant_ancestry_is_not_rewritten_by_a_valid_current_user_quote(request_kind):
    raw = 'What about next season?'
    copied = 'Player X declined because of an injury'
    llm = InterpreterReply({request_kind: [dict(text=copied, objective=copied, user_span=raw,
                                               meaning_origin='ASSISTANT_CONTEXT')]})
    semantic = SemanticInterpreter(llm).interpret(raw)
    assert not getattr(semantic, request_kind)
    goal = RequirementBuilder().build(semantic)
    assert copied not in json.dumps([r.model_dump(mode='json') for r in goal.requirements])


def test_contextual_user_intent_binds_backend_antecedent_not_model_assistant_text():
    from app.contracts.models import AnalysisRequest
    prior = RequirementBuilder().build(SemanticInterpretation(raw_query='Report his batting performance in 2025'))
    semantic = SemanticInterpretation(raw_query='And in 2024?', analysis_requests=[AnalysisRequest(
        text='batting performance in 2024', user_span='And in 2024?', meaning_origin='DERIVED_CONTEXT')])
    goal = RequirementBuilder().build(semantic, parent_goal=prior, turn_type='FOLLOW_UP')
    req = next(r for r in goal.requirements if r.kind.value == 'ANALYSIS')
    assert req.provenance.origin == 'DERIVED_CONTEXT'
    assert req.provenance.context_antecedent == prior.raw_query
    assert provenance_valid(req.model_dump(mode='json'), bound_message=semantic.raw_query)
    without_parent = RequirementBuilder().build(semantic)
    req = next(r for r in without_parent.requirements if r.kind.value == 'ANALYSIS')
    assert not provenance_valid(req.model_dump(mode='json'), bound_message=semantic.raw_query)


def test_unproved_historical_requirement_is_not_exported_as_user_intent():
    from app.runtime.conversation import ConversationManager
    from app.contracts.models import ConversationState
    manager = ConversationManager()
    goal = RequirementBuilder().build(SemanticInterpretation(raw_query='Tell me about Player X'))
    goal.requirements.append(UserRequirement(kind='ANALYSIS', statement='Player X is injured',
                                            value={'objective': 'Player X is injured'}))
    state = ConversationState(session_id='ancestry', goals=[goal], active_goal_id=goal.id)
    context = manager._context(state, goal)
    assert 'Player X is injured' not in json.dumps(context['active_goal']['requirements'])
    assert context['active_goal']['raw_query'] == goal.raw_query


@pytest.mark.parametrize('role,accepted', [('user', True), ('assistant', False)])
def test_elliptical_followup_quote_can_resolve_only_against_prior_user_ancestry(role, accepted):
    raw = 'And in 2024?'
    prior = 'Report his batting performance in 2025'
    llm = InterpreterReply({'turn_type': 'FOLLOW_UP', 'analysis_requests': [
        dict(text='batting performance', user_span='batting performance',
             objective='overall batting performance in 2024')]})
    semantic = SemanticInterpreter(llm).interpret(raw, conversation_context={
        'recent_messages': [{'role': role, 'text': prior}]})
    assert bool(semantic.analysis_requests) == accepted
    if accepted:
        request = semantic.analysis_requests[0]
        assert request.user_span == raw
        assert request.meaning_origin == 'DERIVED_CONTEXT'
        parent = RequirementBuilder().build(SemanticInterpretation(raw_query=prior))
        goal = RequirementBuilder().build(semantic, parent_goal=parent, turn_type='FOLLOW_UP')
        req = next(r for r in goal.requirements if r.kind.value == 'ANALYSIS')
        assert req.provenance.context_antecedent == prior
        assert provenance_valid(req.model_dump(mode='json'), bound_message=raw)


@pytest.mark.parametrize('typed_role,objective', [(True, 'offensive season summary'),
                                                  (False, 'overall batting performance')])
def test_elliptical_quote_does_not_replace_normalized_execution_intent(typed_role, objective):
    from app.contracts.models import AnalysisRequest, Constraint
    from app.capabilities.analytics.conformance import requested_contract
    prior = RequirementBuilder().build(SemanticInterpretation(raw_query='Report his batting performance in 2025'))
    raw = '那2024赛季呢？'
    semantic = SemanticInterpretation(raw_query=raw,
        analysis_requests=[AnalysisRequest(text=objective, objective=objective, user_span=raw,
            meaning_origin='DERIVED_CONTEXT', analytical_role='BATTER' if typed_role else None)],
        constraints=[Constraint(kind='season', value=2024)])
    goal = RequirementBuilder().build(semantic, parent_goal=prior, turn_type='FOLLOW_UP')
    req = next(r for r in goal.requirements if r.kind.value == 'ANALYSIS')
    assert req.bound_source_clause == raw
    contract = requested_contract({}, dict(bound_user_intent=raw,
        analytical_requirements=[r.model_dump(mode='json') for r in goal.requirements]), [])
    assert not contract.issues
    assert contract.analytical_role == 'BATTER'
    assert contract.season == 2024
    # No scope-default relaxation: this user did not authorize a competition restriction.
    assert contract.game_types is None
    from app.capabilities.analytics.conformance import bind_obligations
    intent, issues = bind_obligations({}, dict(bound_user_intent=raw,
        analytical_requirements=[r.model_dump(mode='json') for r in goal.requirements]))
    assert not issues and intent['objective'] == objective


@pytest.mark.parametrize('broken', ['missing_delta', 'target', 'answer', 'candidate'])
def test_clarification_requires_its_bound_backend_delta(broken):
    from app.contracts.models import RequirementProvenance
    from app.semantic.provenance import message_digest
    raw = 'Report his numbers'
    provenance = RequirementProvenance(
        origin='VALIDATED_CLARIFICATION', message_id='goal', bound_message=raw,
        message_digest=message_digest(raw), user_span=raw,
        clarification_target_digest='backend-target', clarification_answer='pitching')
    req = UserRequirement(id='clarified', kind='CONSTRAINT', source='clarification',
                          statement='pitching role', value={'analytical_role': 'PITCHER'}, provenance=provenance)
    delta = dict(candidate_id='clarified', target_digest='backend-target', user_answer='pitching')
    assert provenance_valid(req.model_dump(mode='json'), bound_message=raw, clarification_deltas=[delta])
    if broken == 'target': delta['target_digest'] = 'foreign-target'
    if broken == 'answer': delta['user_answer'] = 'batting'
    if broken == 'candidate': delta['candidate_id'] = 'foreign-requirement'
    assert not provenance_valid(req.model_dump(mode='json'), bound_message=raw,
                                clarification_deltas=[] if broken == 'missing_delta' else [delta])
