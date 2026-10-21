"""Requirement-local independent evidence versus modifier/dependency blocking."""
import pytest
from app.capabilities.analytics.conformance import independent_analytical_partitions
from app.contracts.models import (
    Artifact, RequirementCompletion, UserRequirement, Scope, PlannerNeed,
)
from tools.requirement_ancestry_fixture import Goal, root_goal
from app.semantic.goals import is_explanatory_question
from app.verification.judge import Judge
from app.verification.propositions import build_propositions
from tests.m212_receipt_fixture import with_receipt


def _analysis_requirement(req_id, text):
    completion = RequirementCompletion.EXPLANATION if is_explanatory_question(text) else RequirementCompletion.GROUNDED
    return UserRequirement(
        id=req_id, kind='ANALYSIS', statement=text,
        value={'objective': text, 'source_text': text}, completion=completion,
    )


def scenario(b='Report pitcher pitches on artificial turf',reverse=False):
    a=_analysis_requirement('a', 'Report batter walks')
    b=_analysis_requirement('b', b)
    scope=UserRequirement(id='s',kind='SEASON',statement='2025',value=2025)
    measure=UserRequirement(id='m',kind='MEASURE',statement='walks',value={'key':'walks','derivable':True})
    goal=Goal(statement='independent tasks',raw_query='Report batter walks in 2025; '+b.statement,
        requirements=[b,a,scope,measure] if reverse else [a,b,scope,measure])
    goal.propositions=build_propositions(goal)
    artifact=with_receipt(Artifact(id='fact',kind='DATABASE_RESULT',status='ACCEPTED',need_id='na',
        structured_data={'rows':[{'walks':19}],'row_count':1},text_content='walks\n19',actual_scope=Scope(season=2025)))
    needs=[PlannerNeed(id='na',objective='Report batter walks',capability='local_analytics',requires=['a','m','s']),
           PlannerNeed(id='nb',objective=b.statement,capability='local_analytics',requires=['b'])]
    return goal,artifact,needs


@pytest.mark.parametrize('reverse',[False,True])
@pytest.mark.parametrize('b',['Report pitcher pitches on artificial turf','Report pitcher pitches when score is level','Explain the unknown context'])
def test_independent_scalar_fact_survives_unresolved_task(b,reverse):
    goal,artifact,needs=scenario(b,reverse)
    projection=Judge().evaluate(goal,[artifact],needs)
    assert projection.goal_status.value=='LIMITED'
    assert projection.claims
    assert artifact.id in projection.supporting_artifact_ids
    assert next(p for p in projection.propositions if p.requirement_id=='a').coverage.value=='SATISFIED'
    assert next(p for p in projection.propositions if p.requirement_id=='b').coverage.value!='SATISFIED'


def test_dependent_fact_does_not_survive_unresolved_prerequisite():
    goal,artifact,needs=scenario()
    a=next(p for p in goal.propositions if p.requirement_id=='a')
    b=next(p for p in goal.propositions if p.requirement_id=='b')
    a.depends_on=[b.id]
    projection=Judge().evaluate(goal,[artifact],needs)
    assert not projection.claims
    assert artifact.id not in projection.supporting_artifact_ids


def test_modifier_is_not_an_independent_partial_task():
    goal,artifact,needs=scenario('under a closed roof')
    projection=Judge().evaluate(goal,[artifact],needs)
    assert not projection.claims
    assert projection.goal_status.value!='COMPLETE'


def test_two_independent_supported_facts_survive_a_third_unresolved_task():
    goal,artifact,needs=scenario()
    s=UserRequirement(id='second',kind='ANALYSIS',statement='Report batter strikeouts',value={'objective':'Report batter strikeouts'})
    goal.requirements.append(s)
    goal.raw_query='Report batter walks in 2025; Report batter strikeouts in 2025; Report pitches on artificial turf'
    root_goal(goal, replace=True)
    goal.propositions=build_propositions(goal)
    artifact=with_receipt(Artifact(id='both',kind='DATABASE_RESULT',status='ACCEPTED',need_id='na',
        structured_data={'rows':[{'walks':19,'strikeouts':28}],'row_count':1},text_content='walks strikeouts\n19 28',actual_scope=Scope(season=2025)))
    needs[0].requires.append('second')
    projection=Judge().evaluate(goal,[artifact],needs)
    assert projection.claims
    assert projection.goal_status.value=='LIMITED'
    assert all(p.coverage.value=='SATISFIED' for p in projection.propositions if p.requirement_id in {'a','second'})


# ---------------------------------------------------------------------------
# Dependency-aware invalidation (mission §18/§39): an approved Artifact is owned by
# the current semantics of its Requirement. A clarification that changes that
# Requirement's dependency invalidates the Artifact; a clarification that only affects
# an unrelated Requirement leaves it valid.
# ---------------------------------------------------------------------------

from app.contracts.models import RequirementKind  # noqa: E402


def _entity_artifact(artifact_id, mention, player_id):
    return Artifact(
        id=artifact_id, kind='ENTITY_RESOLUTION', status='ACCEPTED', need_id='ne',
        structured_data={'resolved': [{'mention': mention, 'id': player_id, 'name': mention, 'role': 'BATTER'}]},
    )


def _entity_goal(mention='Garcia'):
    analysis = _analysis_requirement('a', 'Report Garcia batting average')
    entity = UserRequirement(id='e', kind='ENTITY', statement=f'Identify {mention}',
                             value={'mention': mention, 'role': 'BATTER'})
    season = UserRequirement(id='s', kind=RequirementKind.SEASON, statement='2025', value=2025)
    goal = Goal(statement='avg', raw_query='Report Garcia batting average in 2025',
                requirements=[analysis, entity, season])
    goal.propositions = build_propositions(goal)
    return goal


def _avg_artifact_for_123():
    return with_receipt(Artifact(id='stats', kind='DATABASE_RESULT', status='ACCEPTED', need_id='na',
                                 structured_data={'rows': [{'avg': 0.3}], 'row_count': 1},
                                 text_content='avg\n0.3', actual_scope=Scope(entities=['123'], season=2025)))


def test_clarification_that_changes_the_entity_invalidates_the_dependent_artifact():
    goal = _entity_goal()
    # The authoritative entity is 123: the Artifact and its receipt conform.
    projection_ok = Judge().evaluate(goal, [_entity_artifact('ent123', 'Garcia', 123), _avg_artifact_for_123()], [])
    assert projection_ok.claims and 'stats' in projection_ok.supporting_artifact_ids
    # A clarification changes the authoritative entity to 456. The same Artifact no longer
    # conforms to the current Requirement and must be invalidated.
    projection_changed = Judge().evaluate(goal, [_entity_artifact('ent456', 'Garcia', 456), _avg_artifact_for_123()], [])
    assert not projection_changed.claims
    assert 'stats' not in projection_changed.supporting_artifact_ids
    assert projection_changed.goal_status.value != 'COMPLETE'


def test_clarification_affecting_an_unrelated_requirement_keeps_a_valid():
    # A's dependency is unchanged; only the unresolved sibling B changes. A stays valid.
    goal = _entity_goal()
    b = _analysis_requirement('b', "Report the team record")
    goal.requirements.append(b)
    goal.raw_query = "Report Garcia batting average in 2025; Report the team record"
    root_goal(goal, replace=True)
    goal.propositions = build_propositions(goal)
    projection = Judge().evaluate(goal, [_entity_artifact('ent123', 'Garcia', 123), _avg_artifact_for_123()], [])
    assert projection.claims
    assert 'stats' in projection.supporting_artifact_ids
    assert projection.goal_status.value == 'LIMITED'
    a_prop = next(p for p in projection.propositions if p.requirement_id == 'a')
    b_prop = next(p for p in projection.propositions if p.requirement_id == 'b')
    assert a_prop.coverage.value == 'SATISFIED'
    assert b_prop.coverage.value != 'SATISFIED'


def test_shared_unresolved_constraint_blocks_both_branches():
    goal,artifact,needs=scenario()
    goal.requirements.append(UserRequirement(id='shared',kind='CONSTRAINT',statement='roof',value='under a closed roof'))
    projection=Judge().evaluate(goal,[artifact],needs)
    assert not projection.claims


# ---------------------------------------------------------------------------
# Segmentation-hint audit: a lexical task verb is never a semantic authority.
# `_is_independent_analysis_task` only decides whether a task may be separated for
# independent Judge evaluation. A task verb must not ground a clause, promote it to
# UNDERSTOOD/COMPLETE, or admit it. These tests freeze that boundary.
# ---------------------------------------------------------------------------

from app.capabilities.analytics.conformance import (  # noqa: E402
    _is_independent_analysis_task, constraint_coverage,
)

# Task-shaped but statistically dependent fragments: pronouns/deixis, omitted measures,
# omitted populations, omitted subjects. Forced GROUNDED so the task verb is the only
# task-like signal; it must not be enough.
DEPENDENT_STATISTICAL_FRAGMENTS = [
    "Show his numbers there",
    "Report the same split",
    "Report two strikes",
    "Report the numbers for that group",
    "计算他那个赛季的表现",
    "统计那个分组的数据",
]

# EXPLANATION-class dependent fragments. They are a distinct obligation class that no
# Database Artifact can satisfy, so they may be separated; but they must stay unresolved
# and must never make the Goal COMPLETE.
DEPENDENT_EXPLANATION_FRAGMENTS = [
    "Explain that",
    "解释一下这个情况",
]


def _grounded_goal_with_sibling(b):
    """A supported statistical task A plus a forced-GROUNDED dependent fragment B."""
    a = UserRequirement(id='a', kind='ANALYSIS', statement='Report batter walks',
                        value={'objective': 'Report batter walks', 'source_text': 'Report batter walks'})
    sibling = UserRequirement(id='b', kind='ANALYSIS', statement=b,
                              value={'objective': b, 'source_text': b},
                              completion=RequirementCompletion.GROUNDED)
    scope = UserRequirement(id='s', kind='SEASON', statement='2025', value=2025)
    measure = UserRequirement(id='m', kind='MEASURE', statement='walks', value={'key': 'walks', 'derivable': True})
    goal = Goal(statement='independent tasks', raw_query='Report batter walks in 2025; ' + b,
                requirements=[a, sibling, scope, measure])
    goal.propositions = build_propositions(goal)
    artifact = with_receipt(Artifact(id='fact', kind='DATABASE_RESULT', status='ACCEPTED', need_id='na',
                                     structured_data={'rows': [{'walks': 19}], 'row_count': 1},
                                     text_content='walks\n19', actual_scope=Scope(season=2025)))
    needs = [PlannerNeed(id='na', objective='Report batter walks', capability='local_analytics', requires=['a', 'm', 's']),
             PlannerNeed(id='nb', objective=b, capability='local_analytics', requires=['b'])]
    return goal, artifact, needs


@pytest.mark.parametrize('fragment', DEPENDENT_STATISTICAL_FRAGMENTS)
def test_task_shape_alone_is_not_an_independent_task(fragment):
    # Grounded (statistical) completion: the task must be rejected unless a governed
    # Measure or subject role actually grounds it. The task verb is irrelevant.
    req = UserRequirement(
        id='x', kind='ANALYSIS', statement=fragment, value={'objective': fragment},
        completion=RequirementCompletion.GROUNDED,
    )
    assert _is_independent_analysis_task(req) is False


@pytest.mark.parametrize('fragment', DEPENDENT_STATISTICAL_FRAGMENTS)
def test_task_shape_does_not_admit_the_dependent_fragment(fragment):
    # Even in a Goal that carries a supported sibling, a dependent task-shaped fragment
    # cannot produce a partition and must not be satisfied by the sibling Artifact. The
    # supported sibling itself may still be retained as a truthful partial result.
    goal, artifact, needs = _grounded_goal_with_sibling(fragment)
    assert independent_analytical_partitions(goal) == []
    projection = Judge().evaluate(goal, [artifact], needs)
    assert projection.goal_status.value != 'COMPLETE'
    dependent = next(p for p in projection.propositions if p.requirement_id == 'b')
    assert dependent.coverage.value != 'SATISFIED'
    # If any claim survives, it is never admitted for the dependent fragment.
    assert all(
        ref.ref_id != artifact.id
        for claim in projection.claims for ref in claim.support_refs
        if dependent.id in claim.proposition_ids
    )


def test_lexically_waived_deictic_fragment_is_not_satisfied_but_sibling_survives():
    # "Show his numbers there" is lexically waived by the residue test, so the full Goal
    # contract may conform. The grounding gate must still stop the deictic fragment from
    # being satisfied by the sibling Artifact, while the genuinely grounded sibling A is
    # retained as a truthful partial result.
    goal, artifact, needs = _grounded_goal_with_sibling("Show his numbers there")
    projection = Judge().evaluate(goal, [artifact], needs)
    assert projection.goal_status.value == 'LIMITED'
    dependent = next(p for p in projection.propositions if p.requirement_id == 'b')
    assert dependent.coverage.value != 'SATISFIED'
    assert any(ref.ref_id == artifact.id for claim in projection.claims for ref in claim.support_refs)


@pytest.mark.parametrize('fragment', DEPENDENT_EXPLANATION_FRAGMENTS)
def test_explanation_fragment_is_separate_but_never_satisfied_by_a_statistic(fragment):
    # An EXPLANATION-class dependent fragment may be separated, but it can never be
    # satisfied by the sibling Database Artifact and the Goal is never COMPLETE.
    goal, artifact, needs = scenario(fragment)
    projection = Judge().evaluate(goal, [artifact], needs)
    assert projection.goal_status.value != 'COMPLETE'
    b = next(p for p in projection.propositions if p.requirement_id == 'b')
    assert b.coverage.value != 'SATISFIED'
    assert not any(
        claim for claim in projection.claims
        if b.id in claim.proposition_ids and claim.epistemic.value == 'SUPPORTED'
        and any(ref.ref_id == artifact.id for ref in claim.support_refs)
    )


def test_explanation_completion_alone_does_not_ground_the_fragment():
    # `_is_independent_analysis_task` returning True for an EXPLANATION is a class
    # signal, not proof of grounding. A bare explanation with no content stays empty.
    req = UserRequirement(
        id='x', kind='ANALYSIS', statement='Explain that', value={'objective': 'Explain that'},
        completion=RequirementCompletion.EXPLANATION,
    )
    assert _is_independent_analysis_task(req) is True
    coverage = constraint_coverage('Explain that')
    assert coverage['state'] != 'COMPLETE'


def test_per_task_entities_split_when_each_task_names_its_own_subject():
    # Two clearly separate subjects, each named in its own task, may be judged
    # independently. A subject that is not owned by exactly one task stays shared, so a
    # single unresolved identity still blocks every branch (see the next test).
    a = UserRequirement(id='a', kind='ANALYSIS', statement='Report Ohtani batting average',
                        value={'objective': 'Report Ohtani batting average', 'source_text': 'Report Ohtani batting average'})
    b = UserRequirement(id='b', kind='ANALYSIS', statement='Report Judge batting average',
                        value={'objective': 'Report Judge batting average', 'source_text': 'Report Judge batting average'})
    ent_a = UserRequirement(id='ea', kind='ENTITY', statement='Ohtani', value={'mention': 'Ohtani'})
    ent_b = UserRequirement(id='eb', kind='ENTITY', statement='Judge', value={'mention': 'Judge'})
    measure = UserRequirement(id='m', kind='MEASURE', statement='avg', value={'key': 'avg', 'derivable': True})
    goal = Goal(statement='two subjects', raw_query='Report Ohtani and Judge batting average',
                requirements=[a, b, ent_a, ent_b, measure])
    goal.propositions = build_propositions(goal)
    partitions = independent_analytical_partitions(goal)
    assert len(partitions) == 2
    for partition in partitions:
        ids = {r.id for r in partition.requirements}
        assert not ({'ea', 'eb'} <= ids), 'the two subjects must not be in the same branch'
        assert 'ea' in ids or 'eb' in ids


def test_unowned_shared_entity_stays_in_every_partition():
    # One subject named in only one task (and an ungrounded second task) is a shared clause.
    a = UserRequirement(id='a', kind='ANALYSIS', statement='Report Ohtani batting average',
                        value={'objective': 'Report Ohtani batting average', 'source_text': 'Report Ohtani batting average'})
    b = UserRequirement(id='b', kind='ANALYSIS', statement='Report Ohtani home runs',
                        value={'objective': 'Report Ohtani home runs', 'source_text': 'Report Ohtani home runs'})
    ent = UserRequirement(id='e', kind='ENTITY', statement='Ohtani', value={'mention': 'Ohtani'})
    measure = UserRequirement(id='m', kind='MEASURE', statement='avg', value={'key': 'avg', 'derivable': True})
    goal = Goal(statement='one subject', raw_query='Report Ohtani batting average and home runs',
                requirements=[a, b, ent, measure])
    goal.propositions = build_propositions(goal)
    partitions = independent_analytical_partitions(goal)
    assert partitions and all(any(r.id == 'e' for r in p.requirements) for p in partitions)


def test_sub_fragment_of_another_task_blocks_partitioning():
    # A compound Requirement that a bad decomposition split into a fragment of itself:
    # structural containment fails closed even though both parts name a subject role.
    outer = UserRequirement(
        id='outer', kind='ANALYSIS',
        statement='Report batter walks with two strikes',
        value={'objective': 'Report batter walks with two strikes', 'source_text': 'Report batter walks with two strikes', 'index': 0},
    )
    inner = UserRequirement(
        id='inner', kind='ANALYSIS',
        statement='Report batter walks',
        value={'objective': 'Report batter walks', 'source_text': 'Report batter walks', 'index': 1},
    )
    goal = Goal(statement='compound', raw_query='Report batter walks with two strikes',
                requirements=[outer, inner])
    goal.propositions = build_propositions(goal)
    assert independent_analytical_partitions(goal) == []


@pytest.mark.parametrize('with_verb,without_verb', [
    ("Report batter walks", "batter walks"),
    ("Show batter walks", "batter walks"),
    ("统计打者的保送", "打者的保送"),
    ("计算他那个赛季的表现", "他那个赛季的表现"),
])
def test_task_verb_does_not_change_grounded_obligations(with_verb, without_verb):
    # Removing a task verb must not change the grounded semantic contract: the residue
    # state and the recognized obligations are identical. This shows no new keyword is
    # needed for equivalent meaning and that the verb is not semantic authority.
    left = constraint_coverage(with_verb, 'BATTER', 'batter walks', accounted_terms=['batter'])
    right = constraint_coverage(without_verb, 'BATTER', 'batter walks', accounted_terms=['batter'])
    assert left['obligations'] == right['obligations']
    assert left['state'] == right['state']
    assert set(left['unsupported']) == set(right['unsupported'])
