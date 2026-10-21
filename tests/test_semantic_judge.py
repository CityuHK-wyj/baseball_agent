from tests.m212_receipt_fixture import with_receipt
from tools.requirement_ancestry_fixture import Goal
from app.contracts.models import (
    Artifact,
    Export,
    GoalStatus,
    PlannerNeed,
    RequirementKind,
    RequirementStatus,
    Scope,
    UserRequirement,
)
from app.semantic.goals import RequirementBuilder
from app.semantic.interpreter import SemanticInterpreter
from app.verification.judge import Judge


def test_fallback_interpreter_preserves_year_and_population():
    interpreter = SemanticInterpreter(llm=None)
    message = interpreter.interpret("大谷翔平2025年季后赛表现如何？")
    assert message.raw_query == "大谷翔平2025年季后赛表现如何？"
    assert any(c.kind == "season" and c.value == 2025 for c in message.constraints)
    assert "POSTSEASON" in message.population_hints


def test_requirement_builder_separates_user_requirements():
    interpreter = SemanticInterpreter(llm=None)
    message = interpreter.interpret("大谷翔平2025年季后赛打击表现如何？")
    goal = RequirementBuilder().build(message)
    kinds = {r.kind for r in goal.requirements}
    assert RequirementKind.SEASON in kinds
    assert RequirementKind.ANALYSIS in kinds


def _entity_artifact(mention="大谷翔平", player_id=660271, name="Shohei Ohtani") -> Artifact:
    return Artifact(
        id="art_entity",
        kind="ENTITY_RESOLUTION",
        structured_data={"resolved": [{"mention": mention, "id": player_id, "name": name, "role": "BATTER"}]},
        exports=[Export(id="exp_entities", type="PLAYER_ID_SET", payload={"ids": [player_id], "names": [name], "role": "BATTER"})],
        actual_scope=Scope(entities=[str(player_id)], entity_names=[name]),
        need_id="n1",
    )


def _stats_artifact(season=2025, population="POSTSEASON") -> Artifact:
    return with_receipt(Artifact(
        id="art_stats",
        kind="DATABASE_RESULT",
        structured_data={"rows": [{"home_runs": 10}], "row_count": 1},
        text_content="home_runs\n10",
        actual_scope=Scope(entities=["660271"], season=season, game_population=population, source="POSTGRES"),
        need_id="n2",
    ))


def _goal() -> Goal:
    return Goal(
        statement="postseason performance",
        raw_query="大谷翔平2025年季后赛表现如何？",
        requirements=[
            UserRequirement(id="r1", kind=RequirementKind.ENTITY, statement="identify", value={"mention": "大谷翔平", "role":"BATTER"}),
            UserRequirement(id="r2", kind=RequirementKind.SEASON, statement="2025", value=2025),
            UserRequirement(id="r3", kind=RequirementKind.GAME_POPULATION, statement="postseason", value="POSTSEASON"),
            UserRequirement(id="r4", kind=RequirementKind.ANALYSIS, statement="overall performance"),
        ],
    )


def _needs() -> list[PlannerNeed]:
    return [
        PlannerNeed(id="n1", objective="resolve", capability="entity_resolution", requires=["r1"]),
        PlannerNeed(id="n2", objective="stats", capability="local_analytics", depends_on=["n1"], requires=["r2", "r3", "r4"]),
    ]


def test_complete_when_all_core_requirements_supported():
    projection = Judge().evaluate(_goal(), [_entity_artifact(), _stats_artifact()], _needs())
    assert projection.goal_status == GoalStatus.COMPLETE
    assert projection.claims


def test_limited_when_population_mismatch():
    # A regular-season artifact must not satisfy a postseason requirement.
    projection = Judge().evaluate(_goal(), [_entity_artifact(), _stats_artifact(population="REGULAR_SEASON")], _needs())
    assert projection.goal_status == GoalStatus.LIMITED
    assert any("POPULATION" in limitation or "population" in limitation.lower() or "postseason" in limitation.lower() for limitation in projection.limitations)
    # M2.12: a different population cannot be admitted as this requested fact.
    assert not projection.claims


def test_failed_without_evidence():
    projection = Judge().evaluate(_goal(), [], _needs())
    assert projection.goal_status == GoalStatus.FAILED
    assert projection.limitations


def test_planner_optional_failure_does_not_erase_supported_stats():
    needs = _needs() + [PlannerNeed(id="n3", objective="news", capability="web_research", requires=[])]
    projection = Judge().evaluate(_goal(), [_entity_artifact(), _stats_artifact()], needs)
    assert projection.goal_status == GoalStatus.COMPLETE


# ------------------------------------------------------------ measure verification


def _measure_goal(key, *, derivable=True, label=None):
    return Goal(
        statement="measure question",
        raw_query="大谷翔平2025赛季本垒打是多少？",
        requirements=[
            UserRequirement(id="r1", kind=RequirementKind.ENTITY, statement="identify", value={"mention": "大谷翔平", "role":"BATTER"}),
            UserRequirement(id="r2", kind=RequirementKind.SEASON, statement="2025", value=2025),
            UserRequirement(id="r9", kind=RequirementKind.MEASURE, statement="measure",
                            value={"key": key, "label": label or key, "derivable": derivable}),
        ],
    )


def _declaring_stats_artifact(*, keys, season=2025, population=None) -> Artifact:
    return with_receipt(Artifact(
        id="art_measure",
        kind="DATABASE_RESULT",
        structured_data={
            "rows": [{"home_runs": 10}],
            "row_count": 1,
            "measure_keys": list(keys),
            "actual_measures": [{"column": k, "key": k, "verified": True} for k in keys],
        },
        text_content="home_runs\n10",
        actual_scope=Scope(entities=["660271"], season=season, game_population=population,
                           measures=list(keys), source="POSTGRES"),
        need_id="n2",
    ))


def test_measure_satisfied_when_result_declares_it():
    needs = _needs()
    projection = Judge().evaluate(
        _measure_goal("home_runs"),
        [_entity_artifact(), _declaring_stats_artifact(keys=["home_runs"])],
        needs,
    )
    assert projection.goal_status == GoalStatus.COMPLETE
    assert any(ref.source == "artifact" for claim in projection.claims for ref in claim.support_refs)


def test_measure_key_mismatch_blocks_database_claim():
    needs = _needs()
    projection = Judge().evaluate(
        _measure_goal("avg"),
        [_entity_artifact(), _declaring_stats_artifact(keys=["home_runs"])],
        needs,
    )
    assert projection.goal_status == GoalStatus.LIMITED
    assert not any(ref.source == "artifact" for claim in projection.claims for ref in claim.support_refs)


def test_unsupported_measure_is_limited_and_not_replaced():
    needs = _needs()
    projection = Judge().evaluate(
        _measure_goal("rbi", derivable=False),
        [_entity_artifact(), _declaring_stats_artifact(keys=["hits", "home_runs"])],
        needs,
    )
    assert projection.goal_status == GoalStatus.LIMITED
    assert any("UNSUPPORTED_MEASURE" in limitation or "MEASURE" in limitation for limitation in projection.limitations)
    assert not any(ref.source == "artifact" for claim in projection.claims for ref in claim.support_refs)


def test_unresolved_entity_blocks_database_claim():
    """A league-wide aggregate must not become a claim when the entity is unresolved."""
    needs = [PlannerNeed(id="n1", objective="resolve", capability="entity_resolution", requires=["r1"]),
             PlannerNeed(id="n2", objective="stats", capability="local_analytics", depends_on=["n1"], requires=["r2"])]
    projection = Judge().evaluate(_measure_goal("home_runs"), [_declaring_stats_artifact(keys=["home_runs"])], needs)
    # With no canonical identity, M2.12 admits no numerical or scope fact.
    assert projection.goal_status == GoalStatus.FAILED
    assert not any(ref.source == "artifact" for claim in projection.claims for ref in claim.support_refs)
