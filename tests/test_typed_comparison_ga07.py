"""Typed GA-07 comparison entailment: receipts, operand ownership and arithmetic.

No language is parsed in the Judge. These are trusted, explicitly bound Requirement graphs;
open-language input without such a binding continues to fail closed.
"""
from __future__ import annotations

import pytest

from app.capabilities.analytics.conformance import receipt_usable
from app.contracts.models import (
    AnswerProposition, Artifact, ComparativeRelation, ComparisonContract, ComparisonOperand,
    CoverageState, GoalStatus, PlannerNeed, PropositionKind, RequirementKind, Scope,
    UserRequirement,
)
from tools.requirement_ancestry_fixture import Goal, root_goal
from app.verification.judge import Judge
from tests.m212_receipt_fixture import with_receipt


def _case(*, lhs_id=700117, rhs_id=700117, lhs_year=2025, rhs_year=2024,
          lhs_value=.321, rhs_value=.314, key="avg", relation=ComparativeRelation.GT):
    reqs, artifacts, needs = [], [], []
    sides = []
    for side, player_id, season, value in (("lhs", lhs_id, lhs_year, lhs_value),
                                           ("rhs", rhs_id, rhs_year, rhs_value)):
        mention = f"Player {player_id}"
        e = UserRequirement(id=f"e_{side}", kind=RequirementKind.ENTITY,
                            statement=mention, value={"mention": mention, "role": "BATTER"})
        s = UserRequirement(id=f"s_{side}", kind=RequirementKind.SEASON,
                            statement=str(season), value=season)
        m = UserRequirement(id=f"m_{side}", kind=RequirementKind.MEASURE,
                            statement=key, value={"key": key, "label": key})
        clause = f"Report {mention} batter {key} in {season}"
        a = UserRequirement(id=f"a_{side}", kind=RequirementKind.ANALYSIS,
                            statement=clause, value={"objective": clause},
                            original_intent=clause, bound_source_clause=clause)
        reqs.extend((e, s, m, a))
        sides.append(ComparisonOperand(requirement_ids=(e.id, s.id, m.id, a.id),
                                       measure_requirement_id=m.id))
        artifacts.append(Artifact(id=f"identity_{side}", kind="ENTITY_RESOLUTION",
                                  structured_data={"resolved": [{"mention": mention,
                                      "name": mention, "id": player_id, "role": "BATTER"}]}))
        artifact = with_receipt(Artifact(id=f"result_{side}", kind="DATABASE_RESULT",
            status="ACCEPTED", need_id=f"need_{side}",
            structured_data={"rows": [{key: value}], "row_count": 1},
            text_content=f"{season} {key}: {value} ratio",
            actual_scope=Scope(entities=[str(player_id)], season=season, measures=[key])))
        assert receipt_usable(artifact)
        artifacts.append(artifact)
        needs.append(PlannerNeed(id=f"need_{side}", objective=clause,
                                 capability="local_analytics", requires=[a.id, m.id]))
    comp = ComparisonContract(lhs=sides[0], rhs=sides[1], relation=relation)
    reqs.append(UserRequirement(id="comparison", kind=RequirementKind.ANALYSIS,
                                statement="compare the two typed operands",
                                value={"objective": "compare the two typed operands"},
                                comparison=comp))
    goal = Goal(statement="compare", raw_query="; ".join(r.bound_source_clause for r in reqs if r.bound_source_clause),
                requirements=reqs)
    return goal, artifacts, needs


def _re_receipt(artifact, *, filters=None, minimum=None, group_by=None,
                grouped_window=False, narrowed_window=False):
    """Test-only independently consistent IR/attestation/receipt for finite conditions."""
    from app.capabilities.analytics.compiler import SQLCompiler
    from app.capabilities.analytics.conformance import (
        ExecutionAttestation, RequestedAnalyticalContract, attest, compare, digest,
        scope_from_execution,
    )
    from app.capabilities.analytics.ir import AnalyticalQuery, Condition, DateWindow, Qualification, validate_query

    receipt = artifact.structured_data["execution_receipt"]
    query = AnalyticalQuery.model_validate(receipt["validated_ir"])
    if filters is not None:
        query.filters = [Condition.model_validate(f) for f in filters]
    if minimum is not None:
        query.qualification = Qualification(basis="PA", minimum=minimum)
    if group_by is not None:
        query.group_by = group_by
    if grouped_window:
        query.season = None
        query.date_range = DateWindow(start="2024-01-01", end="2025-12-31")
    if narrowed_window:
        query.date_range = DateWindow(start="2024-06-01", end="2024-12-31")
    query = validate_query(query)
    old = ExecutionAttestation.model_validate(receipt["attestation"])
    compiled = SQLCompiler().compile(query, "POSTGRES")
    executed = attest(query, compiled, availability=old.availability, executed=True)
    requested = RequestedAnalyticalContract.model_validate(receipt["requested"]).model_copy(update={
        "filters": executed.filters, "qualification": executed.qualification,
        "group_by": executed.group_by,
        "season": executed.season, "date_range": executed.date_range,
    })
    artifact.actual_scope = scope_from_execution(executed)
    artifact.structured_data["sql"] = compiled.sql
    artifact.structured_data["actual_measures"] = list(executed.measures)
    receipt.update(validated_ir=query.model_dump(mode="json"),
                   requested=requested.model_dump(mode="json"),
                   attestation=executed.model_dump(mode="json"),
                   attestation_digest=digest(executed),
                   result_digest=digest(artifact.structured_data["rows"]),
                   conformance=compare(requested, executed).model_dump(mode="json"))
    assert receipt_usable(artifact)
    return artifact


def _judge(goal, artifacts, needs):
    result = Judge().evaluate(goal, artifacts, needs)
    prop = next(p for p in result.propositions if p.kind == PropositionKind.COMPARISON)
    return result, prop


def _comparison_claims(result, prop):
    return [c for c in result.claims if prop.id in c.proposition_ids]


def test_two_seasons_same_measure_gt_completes_with_lineage():
    g, arts, needs = _case()
    result, prop = _judge(g, arts, needs)
    assert result.goal_status == GoalStatus.COMPLETE
    assert prop.coverage == CoverageState.SATISFIED
    assert set(prop.supporting_artifact_ids) == {"result_lhs", "result_rhs"}
    claims = _comparison_claims(result, prop)
    assert len(claims) == 1
    support = claims[0].comparison_support
    assert support.lhs.artifact_id == "result_lhs" and support.rhs.artifact_id == "result_rhs"
    assert support.lhs.value == "0.321" and support.rhs.value == "0.314"
    assert support.lhs.column == support.rhs.column == "avg"
    assert support.lhs.row_index == support.rhs.row_index == 0
    assert {r.ref_id for r in claims[0].support_refs} == {"result_lhs", "result_rhs"}
    assert "lhs GT rhs" in claims[0].text


def test_composer_cannot_relabel_an_approved_relation():
    from app.contracts.models import AnswerProjection, ConsistencyStatus
    from app.response.accepted_results import render_accepted_results
    from app.verification.consistency import check_status_consistency, repair_answer
    g, arts, needs = _case()
    result, prop = _judge(g, arts, needs)
    only_comparison = AnswerProjection(goal_id=g.id,goal_status=GoalStatus.COMPLETE,
        claims=_comparison_claims(result, prop), propositions=[prop])
    canonical = render_accepted_results(only_comparison)
    assert check_status_consistency(g, only_comparison, canonical).status == ConsistencyStatus.CONSISTENT
    inverted = canonical.replace("lhs GT rhs", "lhs LT rhs")
    assert check_status_consistency(g, only_comparison, inverted).status != ConsistencyStatus.CONSISTENT
    assert repair_answer(only_comparison, inverted) == canonical
    appended = canonical + "\nlhs LT rhs"
    assert check_status_consistency(g, only_comparison, appended).status != ConsistencyStatus.CONSISTENT
    assert repair_answer(only_comparison, appended) == canonical


def test_two_entities_same_measure_lt_completes():
    g, arts, needs = _case(lhs_id=700117, rhs_id=700118, lhs_year=2025, rhs_year=2025,
                           lhs_value=.281, rhs_value=.324, relation=ComparativeRelation.LT)
    result, prop = _judge(g, arts, needs)
    assert result.goal_status == GoalStatus.COMPLETE
    assert prop.coverage == CoverageState.SATISFIED
    assert "BATTER:700117" in _comparison_claims(result, prop)[0].text
    assert "BATTER:700118" in _comparison_claims(result, prop)[0].text


def test_equality_is_numeric_and_preserves_operand_order():
    g, arts, needs = _case(lhs_value=.318, rhs_value=.318, relation=ComparativeRelation.EQ)
    result, prop = _judge(g, arts, needs)
    assert prop.coverage == CoverageState.SATISFIED
    assert "lhs EQ rhs" in _comparison_claims(result, prop)[0].text


@pytest.mark.parametrize("missing", ["lhs", "rhs"])
def test_missing_side_is_partial_and_never_approves_comparison(missing):
    g, arts, needs = _case()
    arts = [a for a in arts if a.id != f"result_{missing}"]
    result, prop = _judge(g, arts, needs)
    assert prop.coverage != CoverageState.SATISFIED
    assert not _comparison_claims(result, prop)
    assert result.goal_status != GoalStatus.COMPLETE
    assert any(c for c in result.claims if c.proposition_ids), "independent value should survive"


@pytest.mark.parametrize("defect", ["wrong_entity", "wrong_measure", "wrong_season", "wrong_population",
                                     "foreign_need", "rejected", "partial_receipt", "null_value"])
def test_invalid_rhs_remains_unproved(defect):
    g, arts, needs = _case()
    rhs = next(a for a in arts if a.id == "result_rhs")
    if defect == "wrong_entity":
        other = with_receipt(Artifact(id="wrong",kind="DATABASE_RESULT",need_id="need_rhs",
            structured_data={"rows":[{"avg": .314}], "row_count":1},
            actual_scope=Scope(entities=["700119"],season=2024,measures=["avg"])))
        arts[arts.index(rhs)] = other
    elif defect == "wrong_measure":
        other = with_receipt(Artifact(id="wrong",kind="DATABASE_RESULT",need_id="need_rhs",
            structured_data={"rows":[{"obp": .314}], "row_count":1},
            actual_scope=Scope(entities=["700117"],season=2024,measures=["obp"])))
        arts[arts.index(rhs)] = other
    elif defect == "wrong_season":
        other = with_receipt(Artifact(id="wrong",kind="DATABASE_RESULT",need_id="need_rhs",
            structured_data={"rows":[{"avg": .314}], "row_count":1},
            actual_scope=Scope(entities=["700117"],season=2022,measures=["avg"])))
        arts[arts.index(rhs)] = other
    elif defect == "wrong_population":
        lhs = with_receipt(Artifact(id="valid_population",kind="DATABASE_RESULT",need_id="need_lhs",
            structured_data={"rows":[{"avg": .321}], "row_count":1},
            actual_scope=Scope(entities=["700117"],season=2025,
                               game_population="REGULAR_SEASON",measures=["avg"])))
        arts[arts.index(next(a for a in arts if a.id == "result_lhs"))] = lhs
        rhs = with_receipt(Artifact(id="wrong",kind="DATABASE_RESULT",need_id="need_rhs",
            structured_data={"rows":[{"avg": .314}], "row_count":1},
            actual_scope=Scope(entities=["700117"],season=2024,
                               game_population="POSTSEASON",measures=["avg"])))
        arts[arts.index(next(a for a in arts if a.id == "result_rhs"))] = rhs
        g.requirements.append(UserRequirement(id="pop",kind=RequirementKind.GAME_POPULATION,
                                                statement="regular season",value="REGULAR_SEASON"))
        owner = next(r for r in g.requirements if r.id == "comparison")
        c = owner.comparison
        owner.comparison = ComparisonContract(
            lhs=ComparisonOperand(requirement_ids=(*c.lhs.requirement_ids, "pop"),
                                  measure_requirement_id=c.lhs.measure_requirement_id),
            rhs=ComparisonOperand(requirement_ids=(*c.rhs.requirement_ids, "pop"),
                                  measure_requirement_id=c.rhs.measure_requirement_id),
            relation=c.relation)
    elif defect == "foreign_need":
        rhs.need_id = "foreign"
        needs.append(PlannerNeed(id="foreign",objective="other",capability="local_analytics",requires=["unrelated"]))
    elif defect == "rejected":
        rhs.status = "REJECTED"
    elif defect == "partial_receipt":
        rhs.structured_data["execution_receipt"]["result_digest"] = "stale"
    elif defect == "null_value":
        rhs.structured_data["rows"] = [{"avg": None}]
        from app.capabilities.analytics.conformance import digest
        rhs.structured_data["execution_receipt"]["result_digest"] = digest(rhs.structured_data["rows"])
    result, prop = _judge(g, arts, needs)
    assert prop.coverage != CoverageState.SATISFIED
    assert not _comparison_claims(result, prop)


def test_extra_time_restriction_on_one_side_is_not_a_window_match():
    g, arts, needs = _case()
    _re_receipt(next(a for a in arts if a.id == "result_rhs"), narrowed_window=True)
    result, prop = _judge(g, arts, needs)
    assert prop.coverage != CoverageState.SATISFIED
    assert not _comparison_claims(result, prop)


def test_swapping_operands_without_inverting_relation_changes_truth():
    g, arts, needs = _case()
    comp = g.requirements[-1].comparison
    g.requirements[-1].comparison = ComparisonContract(lhs=comp.rhs, rhs=comp.lhs,
                                                         relation=comp.relation)
    result, prop = _judge(g, arts, needs)
    assert prop.coverage != CoverageState.SATISFIED
    assert not _comparison_claims(result, prop)


def test_swapping_operands_and_inverting_relation_is_equivalent():
    g, arts, needs = _case()
    c = g.requirements[-1].comparison
    g.requirements[-1].comparison = ComparisonContract(lhs=c.rhs, rhs=c.lhs,
                                                         relation=ComparativeRelation.LT)
    result, prop = _judge(g, arts, needs)
    assert prop.coverage == CoverageState.SATISFIED
    assert "lhs LT rhs" in _comparison_claims(result, prop)[0].text


def test_llm_proposition_spec_cannot_replace_the_typed_comparison():
    g, arts, needs = _case()
    g.requirements[-1].value["propositions"] = [
        {"statement":"ordinary analysis", "kind":"GROUNDED"}]
    result, prop = _judge(g, arts, needs)
    assert prop.coverage == CoverageState.SATISFIED
    assert not any(p.requirement_id == "comparison" and p.kind == PropositionKind.GROUNDED
                   for p in result.propositions)


def test_inferred_comparison_cannot_launder_authority():
    g, arts, needs = _case()
    g.requirements[-1].source = "inferred"
    result, prop = _judge(g, arts, needs)
    assert prop.coverage != CoverageState.SATISFIED
    assert not _comparison_claims(result, prop)


def test_untyped_and_proposition_only_forgery_remain_fail_closed():
    g, arts, needs = _case()
    comp = g.requirements[-1].comparison
    g.requirements[-1].comparison = None
    g.propositions = [AnswerProposition(id="forged",goal_id=g.id,requirement_id="comparison",
                      kind=PropositionKind.COMPARISON,statement="compare",
                      required_information="compare",comparison=comp)]
    result, prop = _judge(g, arts, needs)
    assert prop.coverage != CoverageState.SATISFIED
    assert not _comparison_claims(result, prop)


def test_extra_unrelated_nonempty_artifact_does_not_change_proof():
    g, arts, needs = _case()
    other = with_receipt(Artifact(id="unrelated",kind="DATABASE_RESULT",need_id="foreign",
            structured_data={"rows":[{"avg": .999}],"row_count":1},
            actual_scope=Scope(entities=["700120"],season=2026,measures=["avg"])))
    arts.append(other)
    needs.append(PlannerNeed(id="foreign",objective="other",capability="local_analytics",requires=["foreign"]))
    result, prop = _judge(g, arts, needs)
    assert prop.coverage == CoverageState.SATISFIED
    assert "unrelated" not in prop.supporting_artifact_ids


def test_same_one_row_or_two_rows_cannot_invent_a_second_scalar_scope():
    g, arts, needs = _case()
    rhs = next(a for a in arts if a.id == "result_rhs")
    arts.remove(rhs)
    lhs = next(a for a in arts if a.id == "result_lhs")
    lhs.structured_data["rows"] = [{"avg": .321, "prior": .314}]
    from app.capabilities.analytics.conformance import digest
    lhs.structured_data["execution_receipt"]["result_digest"] = digest(lhs.structured_data["rows"])
    result, prop = _judge(g, arts, needs)
    assert prop.coverage != CoverageState.SATISFIED
    assert not _comparison_claims(result, prop)


def _shared_constraint(goal, key, value):
    goal.requirements.append(UserRequirement(id=key,kind=RequirementKind.CONSTRAINT,
                                              statement=key,value=value))
    root_goal(goal)
    owner = next(r for r in goal.requirements if r.id == "comparison")
    c = owner.comparison
    owner.comparison = ComparisonContract(
        lhs=ComparisonOperand(requirement_ids=(*c.lhs.requirement_ids, key),
                              measure_requirement_id=c.lhs.measure_requirement_id),
        rhs=ComparisonOperand(requirement_ids=(*c.rhs.requirement_ids, key),
                              measure_requirement_id=c.rhs.measure_requirement_id),
        relation=c.relation)


def test_same_filter_two_conformant_receipts_is_supported():
    g, arts, needs = _case()
    condition = {"op":"COMPARE", "field":"p_throws", "comparator":"=", "value":"L"}
    _shared_constraint(g, "hand", {"filters": [condition]})
    for a in arts:
        if a.kind == "DATABASE_RESULT":
            _re_receipt(a, filters=[condition])
    result, prop = _judge(g, arts, needs)
    assert prop.coverage == CoverageState.SATISFIED
    assert result.goal_status == GoalStatus.COMPLETE


def test_one_side_missing_filter_cannot_be_borrowed_as_the_operand():
    g, arts, needs = _case()
    condition = {"op":"COMPARE", "field":"p_throws", "comparator":"=", "value":"L"}
    _shared_constraint(g, "hand", {"filters": [condition]})
    _re_receipt(next(a for a in arts if a.id == "result_lhs"), filters=[condition])
    result, prop = _judge(g, arts, needs)
    assert prop.coverage != CoverageState.SATISFIED
    assert not _comparison_claims(result, prop)


def test_matching_qualification_works_and_missing_one_side_is_partial():
    g, arts, needs = _case()
    _shared_constraint(g, "qual", {"qualification": {"basis":"PA","minimum":10}})
    lhs, rhs = (next(a for a in arts if a.id == f"result_{side}") for side in ("lhs","rhs"))
    _re_receipt(lhs, minimum=10)
    _re_receipt(rhs, minimum=10)
    result, prop = _judge(g, arts, needs)
    assert prop.coverage == CoverageState.SATISFIED
    rhs = with_receipt(Artifact(id="wrong_qualification",kind="DATABASE_RESULT",need_id="need_rhs",
        structured_data={"rows":[{"avg":.314}],"row_count":1},
        actual_scope=Scope(entities=["700117"],season=2024,measures=["avg"])))
    arts[arts.index(next(a for a in arts if a.id == "result_rhs"))] = rhs
    result, prop = _judge(g, arts, needs)
    assert prop.coverage != CoverageState.SATISFIED
    assert not _comparison_claims(result, prop)


def test_revision_invalidates_previous_typed_proposition_snapshot():
    from app.verification.propositions import build_propositions
    g, arts, needs = _case()
    g.propositions = build_propositions(g)
    c = g.requirements[-1].comparison
    g.requirements[-1].comparison = ComparisonContract(lhs=c.rhs, rhs=c.lhs,
                                                         relation=ComparativeRelation.GT)
    result, prop = _judge(g, arts, needs)
    assert prop.coverage != CoverageState.SATISFIED
    assert not _comparison_claims(result, prop)


def test_forged_proposition_cannot_reassign_owner_operands():
    g, arts, needs = _case()
    from app.verification.propositions import build_propositions
    g.propositions = build_propositions(g)
    proposition = next(p for p in g.propositions if p.kind == PropositionKind.COMPARISON)
    proposition.comparison = ComparisonContract(
        lhs=proposition.comparison.rhs, rhs=proposition.comparison.lhs,
        relation=ComparativeRelation.GT)
    result, prop = _judge(g, arts, needs)
    assert prop.coverage != CoverageState.SATISFIED
    assert not _comparison_claims(result, prop)


def test_unsatisfied_external_dependency_cannot_be_laundered_through_operands():
    g, arts, needs = _case()
    from app.verification.propositions import build_propositions
    g.propositions = build_propositions(g)
    prop = next(p for p in g.propositions if p.kind == PropositionKind.COMPARISON)
    g.requirements.append(UserRequirement(id="external", kind=RequirementKind.EVIDENCE,
        statement="an additional finding",value={"objective":"an additional finding"}))
    g.propositions.append(AnswerProposition(id="external_prop",goal_id=g.id,
        requirement_id="external",kind=PropositionKind.FACT,statement="additional finding"))
    prop.depends_on = ["external_prop"]
    result, prop = _judge(g, arts, needs)
    assert prop.coverage != CoverageState.SATISFIED
    assert not _comparison_claims(result, prop)


def test_extra_proposition_cannot_be_upgraded_from_comparison_requirement_state():
    g, arts, needs = _case()
    from app.verification.propositions import build_propositions
    g.propositions = build_propositions(g)
    g.propositions.append(AnswerProposition(id="extra",goal_id=g.id,
        requirement_id="comparison",kind=PropositionKind.GROUNDED,
        statement="extra",required_information="extra"))
    result, prop = _judge(g, arts, needs)
    assert prop.coverage != CoverageState.SATISFIED
    assert not _comparison_claims(result, prop)


def test_unassigned_mandatory_constraint_cannot_be_ignored():
    g, arts, needs = _case()
    g.requirements.append(UserRequirement(id="hand",kind=RequirementKind.CONSTRAINT,
        statement="left-handed pitchers",value={"filters":[{"op":"COMPARE","field":"p_throws",
        "comparator":"=","value":"L"}]}))
    result, prop = _judge(g, arts, needs)
    assert prop.coverage != CoverageState.SATISFIED


def test_web_prose_even_with_both_values_is_not_typed_support():
    g, arts, needs = _case()
    arts = [a for a in arts if a.id != "result_rhs"]
    arts.append(Artifact(id="web",kind="WEB_EVIDENCE",need_id="need_rhs",status="ACCEPTED",
                         text_content="Player 700117 had .321 in 2025 and .314 in 2024."))
    result, prop = _judge(g, arts, needs)
    assert prop.coverage != CoverageState.SATISFIED
    assert not _comparison_claims(result, prop)


def test_conflicting_prebound_direction_cannot_be_overridden_by_typed_relation():
    from app.contracts.models import RelationKind, RelationSemantics
    g, arts, needs = _case()
    owner = next(r for r in g.requirements if r.id == "comparison")
    owner.relation_semantics = RelationSemantics(relation_kind=RelationKind.COMPARISON,
                                                  comparison_direction="decrease")
    result, prop = _judge(g, arts, needs)
    assert prop.coverage != CoverageState.SATISFIED
    assert not _comparison_claims(result, prop)


def test_negated_relation_remains_untyped_and_unproved():
    from app.contracts.models import RelationKind, RelationSemantics
    g, arts, needs = _case()
    g.requirements[-1].relation_semantics = RelationSemantics(
        relation_kind=RelationKind.COMPARISON, polarity="NEGATED")
    result, prop = _judge(g, arts, needs)
    assert prop.coverage != CoverageState.SATISFIED
    assert not _comparison_claims(result, prop)


def test_compound_causal_relation_cannot_be_proved_by_scalar_order():
    from app.contracts.models import RelationKind, RelationSemantics
    g, arts, needs = _case()
    g.requirements[-1].relation_semantics = RelationSemantics(
        relation_kind=RelationKind.COMPARISON,
        relation_kinds=[RelationKind.COMPARISON, RelationKind.CAUSE])
    result, prop = _judge(g, arts, needs)
    assert prop.coverage != CoverageState.SATISFIED
    assert not _comparison_claims(result, prop)


def test_untyped_relation_and_grouped_row_selector_stay_closed():
    g, arts, needs = _case()
    with pytest.raises(ValueError):
        ComparisonContract.model_validate({"lhs": g.requirements[-1].comparison.lhs.model_dump(),
                                           "rhs": g.requirements[-1].comparison.rhs.model_dump(),
                                           "relation": "DELTA"})
    # Exact grouped row-to-operand mapping is not yet in the typed contract.
    g, arts, needs = _case()
    rhs = next(a for a in arts if a.id == "result_rhs")
    arts.remove(rhs)
    lhs = next(a for a in arts if a.id == "result_lhs")
    lhs.structured_data["rows"] = [{"season":2024,"avg":.314},{"season":2025,"avg":.321}]
    lhs.structured_data["row_count"] = 2
    _re_receipt(lhs, group_by=["season"], grouped_window=True)
    result, prop = _judge(g, arts, needs)
    assert prop.coverage != CoverageState.SATISFIED
