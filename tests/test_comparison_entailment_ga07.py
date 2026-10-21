"""GA-07: receipt conformance is not comparative entailment.

These fixtures exercise the real Judge/receipt/claim path. The current Proposition has no
validated operand contract; even a row with two numbers is not sufficient evidence of
which operands the user requested. Web proposition-specific verification remains separate.
"""
from __future__ import annotations

import pytest

from app.contracts.models import (AnswerProposition, Artifact, CoverageState, GoalStatus,
                                  PropositionKind, RequirementKind, Scope, UserRequirement)
from tools.requirement_ancestry_fixture import root_goal
from app.verification.judge import Judge
from tests.m212_receipt_fixture import with_receipt
from tests.test_semantic_judge import _entity_artifact, _goal, _needs, _stats_artifact


def comparison_goal(statement="2025 home runs > 2024 home runs"):
    goal = _goal()
    goal.propositions = [AnswerProposition(
        id="compare", goal_id=goal.id, requirement_id="r4", kind=PropositionKind.COMPARISON,
        statement=statement, subject="Shohei Ohtani", required_information=statement,
    )]
    return goal


def checked(goal, artifacts):
    root_goal(goal)
    return Judge().evaluate(goal, [_entity_artifact(), *artifacts], _needs())


def assert_unproven(result):
    proposition = result.propositions[0]
    assert proposition.coverage != CoverageState.SATISFIED
    assert not proposition.supporting_claim_ids
    assert result.goal_status != GoalStatus.COMPLETE
    assert not result.claims, "a comparison-only table must not escape through partial presentation"


@pytest.mark.parametrize("wording", [
    "2025 home runs > 2024 home runs", "2024 to 2025 home run delta",
    "Did home runs rise between 2024 and 2025?",
])
def test_missing_operand_does_not_complete_or_expose_table(wording):
    assert_unproven(checked(comparison_goal(wording), [_stats_artifact()]))


def test_avg_2024_gt_avg_2023_missing_operand():
    goal = comparison_goal("AVG(Ohtani, 2024) > AVG(Ohtani, 2023)")
    goal.requirements[1].value = 2024  # the locally admitted 2024 operand
    goal.requirements[3].statement = "report batter batting average"
    goal.requirements[3].value = {"objective": "report batter batting average"}
    goal.raw_query = "report batter batting average in 2024"
    root_goal(goal, replace=True)
    a = with_receipt(Artifact(kind="DATABASE_RESULT", id="avg_2024", need_id="n2",
                              structured_data={"rows": [{"avg": .31}], "row_count": 1},
                              actual_scope=Scope(entities=["660271"], season=2024,
                                                 game_population="POSTSEASON", measures=["avg"])))
    from app.capabilities.analytics.conformance import receipt_requirement_ids, receipt_usable
    assert receipt_usable(a)
    assert "r4" in receipt_requirement_ids(a, goal, [_entity_artifact(), a])
    assert_unproven(checked(goal, [a]))


def test_two_correct_values_still_require_governed_relation_and_operand_contract():
    # Same measure, two scopes; current Goal has no typed comparison operand pair.
    # The extra season cannot be admitted as proof of a relation by row-count or prose.
    other = _stats_artifact(season=2024)
    other.id = "other_season"
    assert_unproven(checked(comparison_goal(), [_stats_artifact(), other]))


@pytest.mark.parametrize("companion", [
    "wrong_measure", "wrong_season", "wrong_population", "wrong_entity", "unrelated",
])
def test_extra_artifact_cannot_supply_a_missing_or_incompatible_operand(companion):
    scope = Scope(entities=["999001"] if companion == "wrong_entity" else ["660271"],
                  season=2023 if companion == "wrong_season" else 2024,
                  game_population="REGULAR_SEASON" if companion == "wrong_population" else "POSTSEASON",
                  measures=["obp" if companion == "wrong_measure" else
                            "avg" if companion == "unrelated" else "home_runs"])
    # A complete, independently receipt-validated result is still not a comparison proof.
    a = with_receipt(Artifact(kind="DATABASE_RESULT", id="companion", need_id="n2",
                              structured_data={"rows": [{scope.measures[0]: 1}], "row_count": 1},
                              actual_scope=scope))
    assert_unproven(checked(comparison_goal(), [_stats_artifact(), a]))


def test_one_operand_missing_requested_population_filter():
    from app.capabilities.analytics.compiler import SQLCompiler
    from app.capabilities.analytics.conformance import (
        ExecutionAttestation, RequestedAnalyticalContract, attest, compare, digest,
        receipt_usable, scope_from_execution,
    )
    from app.capabilities.analytics.ir import AnalyticalQuery, Condition, validate_query

    left = _stats_artifact(season=2024)
    receipt = left.structured_data["execution_receipt"]
    query = AnalyticalQuery.model_validate(receipt["validated_ir"])
    query.filters = [Condition(op="COMPARE", field="p_throws", comparator="=", value="L")]
    query = validate_query(query)
    compiled = SQLCompiler().compile(query, "POSTGRES")
    previous = ExecutionAttestation.model_validate(receipt["attestation"])
    attestation = attest(query, compiled, availability=previous.availability, executed=True)
    requested = RequestedAnalyticalContract.model_validate(receipt["requested"]).model_copy(
        update={"filters": tuple(c.model_dump(mode="json") for c in query.filters)})
    left.structured_data["sql"] = compiled.sql
    left.structured_data["actual_measures"] = list(attestation.measures)
    left.actual_scope = scope_from_execution(attestation)
    receipt.update(validated_ir=query.model_dump(mode="json"),
                   requested=requested.model_dump(mode="json"),
                   attestation=attestation.model_dump(mode="json"),
                   attestation_digest=digest(attestation),
                   conformance=compare(requested, attestation).model_dump(mode="json"))
    assert receipt_usable(left)
    right = _stats_artifact(season=2025)
    assert receipt_usable(right)
    assert not ExecutionAttestation.model_validate(right.structured_data["execution_receipt"]["attestation"]).filters
    assert_unproven(checked(comparison_goal("2025 vs 2024 home runs against lefties"), [left, right]))


def test_one_row_with_two_columns_is_not_rejected_by_row_count_but_by_missing_proof():
    a = _stats_artifact()
    a.structured_data["rows"] = [{"home_runs": 10, "previous": 9, "delta": 1}]
    from app.capabilities.analytics.conformance import digest
    a.structured_data["execution_receipt"]["result_digest"] = digest(a.structured_data["rows"])
    assert_unproven(checked(comparison_goal(), [a]))


def test_unrelated_but_goal_admitted_database_result_cannot_prove_comparison():
    # The conformant 2025 home-run table does not entail an OPS entity comparison.
    a = _stats_artifact()
    from app.capabilities.analytics.conformance import receipt_usable
    assert receipt_usable(a)
    assert_unproven(checked(comparison_goal("OPS(Ohtani) > OPS(Betts)"), [a]))


def test_unrelated_nonempty_derived_result_cannot_be_comparison_proof():
    a = Artifact(id="computed",kind="DERIVED_MEASURE",need_id="n2",status="ACCEPTED",
                 actual_scope=Scope(season=2025),
                 structured_data={"rows":[{"delta": 5}],"row_count":1},text_content="delta 5")
    assert_unproven(checked(comparison_goal(), [a]))


def test_independent_value_and_grounded_result_still_admitted():
    goal = _goal()
    result = checked(goal, [_stats_artifact()])
    assert result.goal_status == GoalStatus.COMPLETE
    assert result.claims
    assert any(p.kind == PropositionKind.GROUNDED and p.coverage == CoverageState.SATISFIED
               for p in result.propositions)
    goal.requirements.append(UserRequirement(
        id="measure", kind=RequirementKind.MEASURE, statement="home runs",
        value={"key": "home_runs", "label": "home runs"}))
    goal.propositions = []
    value_result = checked(goal, [_stats_artifact()])
    assert value_result.goal_status == GoalStatus.COMPLETE
    assert value_result.claims
    assert any(p.kind == PropositionKind.VALUE and p.coverage == CoverageState.SATISFIED
               for p in value_result.propositions)


def test_requirement_local_value_claim_does_not_certify_sibling_comparison():
    goal = comparison_goal()
    goal.requirements.append(UserRequirement(
        id="measure", kind=RequirementKind.MEASURE, statement="home runs",
        value={"key": "home_runs", "label": "home runs"}))
    goal.propositions.append(AnswerProposition(
        id="value", goal_id=goal.id, requirement_id="measure", kind=PropositionKind.VALUE,
        statement="2025 home runs", required_information="home runs"))
    result = checked(goal, [_stats_artifact()])
    assert result.propositions[0].coverage != CoverageState.SATISFIED
    assert result.propositions[1].coverage == CoverageState.SATISFIED
    assert result.claims and all("compare" not in c.proposition_ids for c in result.claims)
    assert any("value" in c.proposition_ids for c in result.claims)
    assert result.goal_status == GoalStatus.LIMITED


def test_value_observation_survives_unproven_comparison_on_same_requirement():
    goal = comparison_goal()
    goal.propositions.append(AnswerProposition(
        id="value_observation", goal_id=goal.id, requirement_id="r4",
        kind=PropositionKind.GROUNDED, statement="2025 home runs",
        required_information="2025 home runs"))
    result = checked(goal, [_stats_artifact()])
    assert result.propositions[0].coverage != CoverageState.SATISFIED
    assert result.propositions[1].coverage == CoverageState.SATISFIED
    assert result.claims and all("compare" not in c.proposition_ids for c in result.claims)
    assert result.goal_status == GoalStatus.LIMITED
