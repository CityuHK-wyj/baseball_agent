"""GA-04: unproved attribution requires backend-confirmed, Requirement-local user authority."""
import dataclasses
from copy import deepcopy

import pytest

from app.capabilities.analytics.conformance import requested_contract
from app.config import get_config
from app.contracts.models import (AnswerProjection, AnalysisRequest, Constraint, EntityMention,
                                  GoalStatus, RequirementKind, SemanticInterpretation, UserRequirement)
from app.runtime.engine import Engine
from app.semantic.clarification import apply_semantic_delta, make_semantic
from app.semantic.goals import RequirementBuilder


def state(span="facing portsiders in a windstorm", attribution="facing portsiders"):
    text = f"Ohtani home runs {span} in 2023"
    restriction = {"dimension": "opposing_pitcher_hand", "value": "left", "source_text": span}
    if attribution is not None:
        restriction["attributed_span"] = attribution
    candidate = {"analytical_role": "BATTER", "restrictions": [restriction], "source_text": span}
    semantic = SemanticInterpretation(raw_query=text, understanding=text,
                               analysis_requests=[AnalysisRequest(text=text, objective=text)],
                               entities=[EntityMention(mention="Ohtani", type_hint="player")],
                               constraints=[Constraint(kind="season", value=2023),
                                            Constraint(kind="analytical", value=candidate)])
    goal = RequirementBuilder().build(semantic)
    pending = Engine._clarification(None, goal, [], [], [], semantic)
    assert pending is not None and pending.candidate_id and pending.requirement_id
    return goal, pending


def contract(goal):
    inputs = dict(analytical_requirements=[r.model_dump(mode="json") for r in goal.requirements if r.required],
                  bound_user_intent=goal.raw_query,
                  _semantic_deltas=[d.model_dump(mode="json") for d in goal.semantic_deltas],
                  _resolution_state=goal.analytical_resolution_state)
    return requested_contract({}, inputs, [])


@pytest.mark.parametrize("span", [
    "facing portsiders", "portsiders windstorm", "moon-siders squall",
    "in a windstorm while facing portsiders", "facing portsiders, but during a windstorm",
    "迎战银臂投手在旋流里",
])
def test_compact_and_reordered_novel_wording_is_not_semantic_proof(span):
    goal, pending = state(span, span)
    before = contract(goal)
    assert before.issues and not goal.semantic_deltas
    assert {f["field"] for f in before.filters} == {"p_throws"}
    assert pending.option_actions[:3] == ["DISCHARGE_RESIDUE", "PRESERVE_RESIDUE", "REPLACE_INTERPRETATION"]
    assert pending.option_actions[3:] == ["REPLACE_INTERPRETATION"]
    assert any(c["state"] != "COMPLETE" for c in before.clause_coverage)


@pytest.mark.parametrize('span', [
    'moon-siders squall in the seventh inning',
    'in the seventh inning moon-siders squall',
    'moon-siders squall with two strikes',
])
def test_one_residual_region_cannot_launder_unknown_extra_via_finite_overlap(span):
    goal, pending = state(span, span)
    before = contract(goal)
    assert before.issues and {f['field'] for f in before.filters} >= {'p_throws'}
    assert span in pending.source_span
    assert all(d.action != 'DISCHARGE_RESIDUE' for d in goal.semantic_deltas)


def test_confirmed_opaque_region_does_not_erase_independently_finite_inning():
    goal, pending = state('in the seventh inning comet-arms haze',
                          'in the seventh inning comet-arms haze')
    assert 'seventh inning' not in pending.confirmed_residue
    assert {f['field'] for f in contract(goal).filters} == {'inning', 'p_throws'}
    for index, expected_hand in ((0, 'L'), (3, 'R')):
        updated = apply_semantic_delta(goal, pending, pending.option_actions[index],
                                       pending.options[index])
        after = contract(updated)
        assert not after.issues
        assert {f['field']: f['value'] for f in after.filters} == {
            'inning': 7, 'p_throws': expected_hand}


def test_broad_cited_clause_preserves_modifier_outside_narrow_item_citation():
    span = 'facing portsiders under an unusual squall'
    text = f'Ohtani home runs {span} in 2023'
    candidate = {'analytical_role': 'BATTER', 'restrictions': [
        {'dimension': 'opposing_pitcher_hand', 'value': 'left',
         'source_text': 'facing portsiders', 'attributed_span': 'facing portsiders'}],
        'source_text': span}
    semantic = SemanticInterpretation(raw_query=text, understanding=text,
                               analysis_requests=[AnalysisRequest(text=text, objective=text)],
                               entities=[EntityMention(mention='Ohtani', type_hint='player')],
                               constraints=[Constraint(kind='season', value=2023),
                                            Constraint(kind='analytical', value=candidate)])
    goal = RequirementBuilder().build(semantic)
    req = next(r for r in goal.requirements if r.derivation_kind == 'governed_semantic_candidate')
    assert req.value['source_spans'] == [text]
    assert req.value['attributions'][0]['attributed_span'] == 'facing portsiders'
    pending = Engine._clarification(None, goal, [], [], [], semantic)
    assert pending and 'unusual squall' in pending.question
    assert contract(goal).issues
    assert not contract(apply_semantic_delta(goal, pending, pending.option_actions[0],
                                             pending.options[0])).issues


def test_legacy_narrow_citation_does_not_hide_unattributed_extra_modifier():
    span = 'facing portsiders under an unusual squall'
    text = f'Ohtani home runs {span} in 2023'
    candidate = {'analytical_role': 'BATTER', 'restrictions': [
        {'dimension': 'opposing_pitcher_hand', 'value': 'left',
         'source_text': 'facing portsiders'}], 'source_text': 'facing portsiders'}
    semantic = SemanticInterpretation(raw_query=text, understanding=text,
                               analysis_requests=[AnalysisRequest(text=text, objective=text)],
                               entities=[EntityMention(mention='Ohtani', type_hint='player')],
                               constraints=[Constraint(kind='season', value=2023),
                                            Constraint(kind='analytical', value=candidate)])
    goal = RequirementBuilder().build(semantic)
    pending = Engine._clarification(None, goal, [], [], [], semantic)
    assert pending and 'squall' in pending.question and 'Ohtani home runs' not in pending.question
    assert contract(goal).issues
    assert not contract(apply_semantic_delta(goal, pending, pending.option_actions[0],
                                             pending.options[0])).issues


def test_multiple_citations_are_confirmed_once_each_not_as_one_blanket_span():
    text = 'Ohtani home runs comet-arms haze and the seventh breeze in 2023'
    candidate = {'analytical_role': 'BATTER', 'restrictions': [
        {'dimension': 'opposing_pitcher_hand', 'value': 'left',
         'source_text': 'comet-arms haze', 'attributed_span': 'comet-arms haze'},
        {'dimension': 'inning', 'value': 7,
         'source_text': 'the seventh breeze', 'attributed_span': 'the seventh breeze'}],
        'source_text': text}
    semantic = SemanticInterpretation(raw_query=text, understanding=text,
                               analysis_requests=[AnalysisRequest(text=text, objective=text)],
                               entities=[EntityMention(mention='Ohtani', type_hint='player')],
                               constraints=[Constraint(kind='season', value=2023),
                                            Constraint(kind='analytical', value=candidate)])
    goal = RequirementBuilder().build(semantic)
    first = Engine._clarification(None, goal, [], [], [], semantic)
    assert first and first.source_span == 'comet-arms haze'
    partially_confirmed = apply_semantic_delta(goal, first, first.option_actions[0], first.options[0])
    assert contract(partially_confirmed).issues
    second = Engine._clarification(None, partially_confirmed, [], [], [], make_semantic(partially_confirmed, 'yes'))
    assert second and second.source_span == 'the seventh breeze'
    completed = apply_semantic_delta(partially_confirmed, second, second.option_actions[0], second.options[0])
    assert not contract(completed).issues
    assert Engine._clarification(None, completed, [], [], [], make_semantic(completed, 'yes')) is None


def test_user_only_choice_converges_without_reinterpreting_grounded_requirements():
    goal, pending = state()
    before = contract(goal)
    old_ids = [(r.id, r.kind, deepcopy(r.value)) for r in goal.requirements]
    updated = apply_semantic_delta(goal, pending, pending.option_actions[0], pending.options[0])
    after = contract(updated)
    assert before.issues and not after.issues, after.issues
    assert {f["field"] for f in after.filters} == {"p_throws"}
    assert old_ids == [(r.id, r.kind, r.value) for r in updated.requirements]
    assert updated.raw_query == goal.raw_query and updated.id != goal.id
    assert updated.semantic_deltas[0].source_goal_id == goal.id
    assert Engine._clarification(None, updated, [], [], [], make_semantic(updated, "answered")) is None
    assert any(c["requirement_id"] == "source_review" and c["state"] == "COMPLETE"
               for c in after.clause_coverage)
    assert updated.analytical_resolution_state['current'] != goal.analytical_resolution_state['current']
    assert updated.analytical_resolution_state['attempts'][:len(goal.analytical_resolution_state['attempts'])] == goal.analytical_resolution_state['attempts']


def test_discharge_removes_finite_extra_from_confirmed_source_region():
    goal, pending = state('facing portsiders with two strikes', 'facing portsiders')
    before = contract(goal)
    assert 'strikes' in {f['field'] for f in before.filters}
    updated = apply_semantic_delta(goal, pending, pending.option_actions[0], pending.options[0])
    after = contract(updated)
    assert not after.issues
    assert {f['field'] for f in after.filters} == {'p_throws'}
    assert goal.raw_query == updated.raw_query  # original intent is retained for audit


def test_yes_preserves_unrepresented_condition_without_loop():
    goal, pending = state()
    updated = apply_semantic_delta(goal, pending, pending.option_actions[1], pending.options[1])
    after = contract(updated)
    assert after.issues and {f["field"] for f in after.filters} == {"p_throws"}
    assert any(entry['state'] == 'TEMPORARILY_UNRESOLVED' for entry in after.unresolved_obligations)
    assert updated.semantic_deltas[0].action == "PRESERVE_RESIDUE"
    assert Engine._clarification(None, updated, [], [], [], make_semantic(updated, "answered")) is None


def test_correction_revokes_old_candidate_and_preserves_other_bindings():
    goal, pending = state()
    old_candidate = next(r for r in goal.requirements if r.id == pending.candidate_id)
    unrelated = {r.id: deepcopy(r) for r in goal.requirements if r.id != pending.candidate_id}
    updated = apply_semantic_delta(goal, pending, pending.option_actions[2], pending.options[2])
    assert old_candidate.id not in {r.id for r in updated.requirements}
    assert all(updated_req == unrelated[updated_req.id] for updated_req in updated.requirements)
    after = contract(updated)
    assert after.issues and not any(p["field"] == "p_throws" for p in after.filters)
    assert Engine._clarification(None, updated, [], [], [], make_semantic(updated, "answered")) is None


def test_discharge_invalidates_competing_candidate_but_preserves_independent_requirement():
    goal, pending = state('meeting moon-siders under an unusual squall', 'meeting moon-siders')
    original = next(r for r in goal.requirements if r.id == pending.candidate_id)
    competing = original.model_copy(deep=True)
    competing.id = 'competing-candidate'
    competing.value['filters'] = [{'op': 'COMPARE', 'field': 'strikes', 'comparator': '=', 'value': 2}]
    goal.requirements.append(competing)
    sibling = UserRequirement(id='independent', kind=RequirementKind.EVIDENCE,
                              statement='Find an unrelated report', value={'objective': 'Find an unrelated report'})
    goal.requirements.append(sibling)
    # State changed after the pending question: old digest must be stale.
    with pytest.raises(ValueError, match='stale'):
        apply_semantic_delta(goal, pending, pending.option_actions[0], pending.options[0])
    from app.semantic.clarification import target_digest
    pending = pending.model_copy(update={'target_digest': target_digest(goal, pending.requirement_id,
                                                                       pending.candidate_id, pending.source_span)})
    updated = apply_semantic_delta(goal, pending, pending.option_actions[0], pending.options[0])
    assert competing.id not in {r.id for r in updated.requirements}
    assert next(r for r in updated.requirements if r.id == sibling.id) == sibling
    assert next(r for r in updated.requirements if r.id == original.id).value == original.value
    # A previously consumed answer versions the target even if a caller leaves
    # the goal ID unchanged: no contradictory duplicate semantic deltas.
    with pytest.raises(ValueError, match='stale'):
        apply_semantic_delta(updated, pending, pending.option_actions[1], pending.options[1])


def test_backend_enumerated_correction_replaces_candidate_and_converges():
    goal, pending = state('comet-arms haze', 'comet-arms haze')
    assert len(pending.options) == 4
    assert pending.option_actions[3] == 'REPLACE_INTERPRETATION'
    assert pending.option_replacements[3]['filters'][0]['value'] == 'R'
    corrected = apply_semantic_delta(goal, pending, pending.option_actions[3], pending.options[3])
    assert len(corrected.semantic_deltas) == 2
    assert [d.action for d in corrected.semantic_deltas] == [
        'REPLACE_INTERPRETATION', 'DISCHARGE_RESIDUE']
    original = next(r for r in goal.requirements if r.id == pending.candidate_id)
    assert original.id not in {r.id for r in corrected.requirements}
    replacement = next(r for r in corrected.requirements if r.supersedes == original.id)
    assert replacement.source == 'clarification'
    assert replacement.parent_requirement_ids == original.parent_requirement_ids
    assert {f['field']: f['value'] for f in contract(corrected).filters} == {'p_throws': 'R'}
    assert not contract(corrected).issues
    assert Engine._clarification(None, corrected, [], [], [], make_semantic(corrected, 'yes')) is None
    for old_req in goal.requirements:
        if old_req.id != original.id:
            assert next(r for r in corrected.requirements if r.id == old_req.id).value == old_req.value
    tampered = pending.model_copy(deep=True)
    tampered.option_replacements[3]['filters'][0]['value'] = 'X'
    with pytest.raises(ValueError, match='invalid clarification replacement'):
        apply_semantic_delta(goal, tampered, tampered.option_actions[3], tampered.options[3])


def test_stale_or_forged_target_is_rejected_without_mutation():
    goal, pending = state()
    snapshot = goal.model_dump(mode='json')
    with pytest.raises(ValueError, match='stale'):
        apply_semantic_delta(goal, pending.model_copy(update={'source_span': 'unrelated'}),
                             pending.option_actions[0], 'answer')
    with pytest.raises(ValueError, match='stale'):
        apply_semantic_delta(goal, pending.model_copy(update={'goal_id': 'old-goal'}),
                             pending.option_actions[0], 'answer')
    with pytest.raises(ValueError, match='unrecognised'):
        apply_semantic_delta(goal, pending, 'INVENT_FILTER', 'answer')
    assert goal.model_dump(mode='json') == snapshot


def test_free_text_needs_one_backend_confirmation_and_does_not_self_apply(tmp_path):
    base = get_config()
    engine = Engine(dataclasses.replace(base, runtime_dir=tmp_path,
                                       postgres=dataclasses.replace(base.postgres, port=59999),
                                       llm=dataclasses.replace(base.llm, api_key="")))
    initial, _ = state()
    engine.conversation.commit('delta-test', user_text=initial.raw_query, goal=initial,
                               answer='pending', projection=AnswerProjection(
                                   goal_id=initial.id, goal_status=GoalStatus.WAITING_FOR_USER,
                                   clarification=Engine._clarification(None, initial, [], [], [],
                                                                       make_semantic(initial, ''))))
    engine._run_plan = lambda *args, **kwargs: ([], [], [], [], [])
    engine.interpreter.propose_clarification_action = lambda *_: 'DISCHARGE_RESIDUE'
    interim = engine.ask('delta-test', "Yeah, that other condition isn't relevant")
    assert not interim.goal.semantic_deltas
    assert interim.projection.clarification is not None
    assert interim.projection.clarification.confirmation_requested
    assert 'DISCHARGE_RESIDUE' in interim.projection.clarification.question
    assert contract(interim.goal).issues
    engine.conversation.sessions.pop('delta-test')  # reload persisted pending target
    assert engine.conversation.get('delta-test').pending_clarification.target_digest
    confirmed = engine.ask('delta-test', interim.projection.clarification.options[0])
    assert confirmed.goal.semantic_deltas[0].action == 'DISCHARGE_RESIDUE'
    assert not contract(confirmed.goal).issues
    assert confirmed.projection.clarification is None
    assert confirmed.goal.parent_goal_id == interim.goal.id
    assert contract(confirmed.goal).issues == ()


@pytest.mark.parametrize('choice,action', [(1, 'PRESERVE_RESIDUE'), (2, 'REPLACE_INTERPRETATION'),
                                           (3, 'REPLACE_INTERPRETATION')])
def test_backend_option_resume_reuses_prior_goal_state(tmp_path, choice, action):
    base = get_config()
    engine = Engine(dataclasses.replace(base, runtime_dir=tmp_path,
                                       postgres=dataclasses.replace(base.postgres, port=59999),
                                       llm=dataclasses.replace(base.llm, api_key='')))
    initial, pending = state()
    engine.conversation.commit('option-test', user_text=initial.raw_query, goal=initial,
                               answer='pending', projection=AnswerProjection(
                                   goal_id=initial.id, goal_status=GoalStatus.WAITING_FOR_USER,
                                   clarification=pending))
    engine._run_plan = lambda *args, **kwargs: ([], [], [], [], [])
    result = engine.ask('option-test', pending.options[choice])
    assert result.goal.semantic_deltas[0].action == action
    assert result.projection.clarification is None
    assert result.goal.parent_goal_id == initial.id
    assert result.goal.raw_query == initial.raw_query
    assert result.projection.goal_status != GoalStatus.COMPLETE
    after = contract(result.goal)
    assert bool(after.issues) == (choice != 3)
    if choice == 1:
        assert {p['field'] for p in after.filters} == {'p_throws'}
    elif choice == 2:
        assert not any(p['field'] == 'p_throws' for p in after.filters)
    else:
        assert {p['field']: p['value'] for p in after.filters} == {'p_throws': 'R'}
        assert not after.issues


def test_api_rejects_stale_target_even_with_current_goal_id(tmp_path):
    from fastapi.testclient import TestClient
    from app.api.main import create_app
    from tests.test_frontend_api import EngineFixture

    fake = EngineFixture(tmp_path)
    with TestClient(create_app(fake)) as client:
        cid = client.post('/api/conversations').json()['conversation_id']
        goal, pending = state()
        fake.conversation.commit(cid, user_text=goal.raw_query, goal=goal, answer='pending',
                                 projection=AnswerProjection(goal_id=goal.id,
                                                             goal_status=GoalStatus.WAITING_FOR_USER,
                                                             clarification=pending))
        # In-place change leaves the public goal ID unchanged; the target digest
        # still rejects a response instead of applying it to the changed meaning.
        next(r for r in goal.requirements if r.id == pending.candidate_id).value['filters'][0]['value'] = 'R'
        result = client.post(f'/api/conversations/{cid}/clarifications/clar:{goal.id}/responses',
                             json={'selected_option_ids': ['choice:0']})
        assert result.status_code == 409
        assert result.json()['error']['code'] == 'STALE_CLARIFICATION'
        assert fake.inputs == []


def test_second_unconfirmed_free_text_exits_without_duplicate_clarification(tmp_path):
    base = get_config()
    engine = Engine(dataclasses.replace(base, runtime_dir=tmp_path,
                                       postgres=dataclasses.replace(base.postgres, port=59999),
                                       llm=dataclasses.replace(base.llm, api_key='')))
    initial, pending = state('moon-siders squall', 'moon-siders squall')
    engine.conversation.commit('bounded-test', user_text=initial.raw_query, goal=initial,
                               answer='pending', projection=AnswerProjection(
                                   goal_id=initial.id, goal_status=GoalStatus.WAITING_FOR_USER,
                                   clarification=pending))
    engine._run_plan = lambda *args, **kwargs: ([], [], [], [], [])
    first = engine.ask('bounded-test', 'unsure about the phrasing')
    assert first.projection.clarification is not None
    second = engine.ask('bounded-test', 'still unsure')
    assert second.projection.clarification is None
    assert contract(second.goal).issues
    assert not second.goal.semantic_deltas
