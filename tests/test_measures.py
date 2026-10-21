"""Measure semantics: resolution, structural attestation, and no silent substitution."""

from app.capabilities.analytics.canonical import build_canonical_query
from app.capabilities.analytics.catalog import CATALOG
from app.capabilities.analytics.ir import AnalyticalQuery, validate_query
from app.capabilities.analytics.measures import (
    MEASURES,
    declare_measures,
    declared_keys,
    resolve_text,
    resolve_texts,
)
from app.semantic.goals import RequirementBuilder
from app.semantic.interpreter import SemanticInterpreter
from app.contracts.models import (
    AnalysisRequest,
    Constraint,
    EntityMention,
    RequirementKind,
    SemanticInterpretation,
)


def _validate(payload: dict) -> AnalyticalQuery:
    return validate_query(AnalyticalQuery.model_validate(payload), CATALOG)


def test_non_derivable_measures_are_explicit():
    for key in ("era", "rbi", "stolen_bases", "team_record", "whip", "saves"):
        assert MEASURES[key].derivable is False
    assert MEASURES["avg"].derivable is True


def test_resolver_marks_unsupported_measures():
    assert resolve_text("大谷翔平的ERA是多少")[0].key == "era"
    assert resolve_text("打点(RBI)是多少")[0].derivable is False
    assert resolve_text("盗垒多少次")[0].derivable is False


def test_resolver_prefers_longer_alias():
    # 保送率 (BB%) must win over 保送 (walks); 三振率 over 三振.
    assert [r.key for r in resolve_text("他的保送率和三振率")] == ["bb_pct", "k_pct"]


def test_resolver_ignores_ascii_substrings():
    # English word-boundary matching: "hr" must not fire inside another word.
    assert resolve_texts(["throughout the season"]) == []


def test_canonical_pack_declares_standard_measures():
    intent = {
        "objective": "overall batting performance",
        "entities": [{"role": "BATTER", "ids": [660271], "names": ["Shohei Ohtani"]}],
        "season": 2025,
        "population": "REGULAR_SEASON",
    }
    query = validate_query(build_canonical_query(intent), CATALOG)
    keys = declared_keys([item.to_dict() for item in declare_measures(query, ir_origin="canonical_metric_pack")])
    for expected in {"pa", "hits", "home_runs", "walks", "strikeouts", "at_bats", "tb",
                     "avg", "obp", "slg", "ops", "k_pct", "bb_pct"}:
        assert expected in keys, expected


def test_structural_attestation_ignores_column_names():
    """A correctly-defined metric is recognized even under an arbitrary output name."""
    payload = {
        "relation": "pitch",
        "entity_filters": [{"role": "BATTER", "ids": [660271]}],
        "season": 2025,
        "aggregates": [
            {"name": "h", "function": "COUNT_IF",
             "condition": {"op": "IN", "field": "events", "values": ["single", "double", "triple", "home_run"]}},
            {"name": "ab", "function": "COUNT_IF", "condition": {"op": "AND", "conditions": [
                {"op": "IS_NOT_NULL", "field": "events"},
                {"op": "NOT", "conditions": [{"op": "IN", "field": "events", "values": [
                    "walk", "intent_walk", "hit_by_pitch", "catcher_interf", "sac_fly", "sac_bunt",
                    "sac_fly_double_play", "sac_bunt_double_play", "truncated_pa", "game_advisory"]}]}]}},
        ],
        "derived": [{"name": "arbitrary_label", "op": "SAFE_DIV",
                     "left": {"kind": "aggregate", "name": "h"},
                     "right": {"kind": "aggregate", "name": "ab"}}],
    }
    declared = declare_measures(_validate(payload), ir_origin="llm")
    by_column = {item.column: item for item in declared}
    assert by_column["arbitrary_label"].key == "avg"
    assert by_column["arbitrary_label"].verified is True


def test_mislabeled_column_is_not_attested_as_the_wrong_measure():
    """A column named rbi but computed as hits must never be attested as RBI."""
    payload = {
        "relation": "pitch",
        "entity_filters": [{"role": "BATTER", "ids": [660271]}],
        "season": 2025,
        "aggregates": [
            {"name": "rbi", "function": "COUNT_IF",
             "condition": {"op": "IN", "field": "events", "values": ["single", "double", "triple", "home_run"]}},
        ],
    }
    declared = declare_measures(_validate(payload), ir_origin="llm")
    assert declared[0].key == "hits"
    assert "rbi" not in declared_keys([item.to_dict() for item in declared])


def test_unrecognized_condition_is_not_attested():
    payload = {
        "relation": "pitch",
        "entity_filters": [{"role": "BATTER", "ids": [660271]}],
        "season": 2025,
        "aggregates": [
            {"name": "hits", "function": "COUNT_IF",
             "condition": {"op": "IN", "field": "events", "values": ["single", "double"]}},
        ],
    }
    declared = declare_measures(_validate(payload), ir_origin="llm")
    assert declared[0].key is None
    assert declared[0].verified is False


# ------------------------------------------------------------------ requirements


def _message(raw_query: str, *, measure=None, objective="", with_entity=True) -> SemanticInterpretation:
    return SemanticInterpretation(
        raw_query=raw_query,
        language="zh",
        entities=[EntityMention(mention="大谷翔平", type_hint="player")] if with_entity else [],
        constraints=[Constraint(kind="season", value=2025)] + (
            [Constraint(kind="measure", value=measure)] if measure else []
        ),
        analysis_requests=[AnalysisRequest(text=objective or raw_query, objective=objective or raw_query)],
    )


def test_requirement_builder_emits_measure_requirement():
    goal = RequirementBuilder().build(_message("大谷翔平2025年的ERA是多少？", measure="ERA"))
    measures = [r for r in goal.requirements if r.kind == RequirementKind.MEASURE]
    assert len(measures) == 1
    assert measures[0].value["key"] == "era"
    assert measures[0].value["derivable"] is False


def test_definition_question_is_not_a_measure_request():
    goal = RequirementBuilder().build(_message("什么是OPS？", objective="explain OPS", with_entity=False))
    assert not [r for r in goal.requirements if r.kind == RequirementKind.MEASURE]


def test_fallback_interpreter_plus_builder_keep_general_analysis():
    message = SemanticInterpreter(llm=None).interpret("大谷翔平2025年季后赛打击表现如何？")
    goal = RequirementBuilder().build(message)
    assert not [r for r in goal.requirements if r.kind == RequirementKind.MEASURE]
    assert RequirementKind.ANALYSIS in {r.kind for r in goal.requirements}


def test_non_string_measure_inputs_do_not_crash():
    from app.capabilities.analytics.tool import _coerce_season, _coerce_text

    assert isinstance(resolve_texts([["home_runs"], None, 3]), list)
    assert resolve_text(None) == []
    assert _coerce_text(["home_runs", "pa"]) == "home_runs, pa"
    assert _coerce_text(None) is None
    assert _coerce_season("career")[0] is None and _coerce_season("career")[1]
    assert _coerce_season("2025") == (2025, None)
    assert _coerce_season(2025) == (2025, None)
    assert _coerce_season([2025])[0] is None
