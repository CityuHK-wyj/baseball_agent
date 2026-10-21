"""Post-independent-review derived-dependency adversarial regression suite.

The decisive counterexample is a program that reads a valid factual operand and then
returns an unrelated literal. Syntactic operand access is not semantic output
dependency; replaying a fabricated program only reproduces the fabricated result.

These tests use fresh operand values (2023 home_runs=39, 2024 home_runs=19) and the
production tool + Judge path. They are mechanism tests, not frozen-vocabulary tests.
"""
from __future__ import annotations

import pytest

from app.capabilities.python_compute import PythonComputeTool
from app.contracts.models import (
    AnalysisRequest,
    Goal,
    InputBinding,
    PlannerNeed,
    RelationKind,
    RelationSemantics,
    SemanticInterpretation,
    ToolRequest,
)
from app.semantic.goals import RequirementBuilder
from app.verification.judge import Judge
from tests.test_runtime_dataflow_phase5a import _ctx, _stats_artifact

_DELTA_OPERATION = {"kind": "DELTA", "identity": "PLAYER", "partition_by": "SEASON"}


def _operands():
    from tests.m212_receipt_fixture import with_receipt
    left = with_receipt(_stats_artifact("art_2023", need_id="n2023", season=2023,
                                        rows=[{"season": 2023, "home_runs": 39, "avg": 0.307, "hits": 179}]))
    right = with_receipt(_stats_artifact("art_2024", need_id="n2024", season=2024,
                                         rows=[{"season": 2024, "home_runs": 19, "avg": 0.276, "hits": 130}]))
    return left, right


def _bindings():
    return [
        InputBinding(need_id="n2", export_id="exp_art_2023", export_type="STATISTICAL_RESULT",
                     artifact_id="art_2023", source_need_id="n2023"),
        InputBinding(need_id="n2", export_id="exp_art_2024", export_type="STATISTICAL_RESULT",
                     artifact_id="art_2024", source_need_id="n2024"),
    ]


_VALUE_RELATION = RelationSemantics(relation_kind=RelationKind.VALUE, relation_kinds=[RelationKind.VALUE])


def _goal(objective: str, *, relation: RelationSemantics | None = None) -> Goal:
    # Build through the real Requirement builder so provenance/authority is governed,
    # exactly as the live path does; a hand-built Requirement has unproved ancestry.
    semantic = SemanticInterpretation(
        raw_query=objective, understanding=objective, language="en",
        analysis_requests=[AnalysisRequest(
            text=objective, user_span=objective, objective=objective,
            relation_semantics=relation or RelationSemantics(
                relation_kind=RelationKind.COMPARISON,
                relation_kinds=[RelationKind.COMPARISON, RelationKind.AGGREGATION]),
        )],
    )
    return RequirementBuilder().build(semantic)


def _run(program: str, *, operation=None):
    left, right = _operands()
    request = ToolRequest(
        id="tr", need_id="n2", capability="python_compute",
        objective="numerical difference between the two seasons",
        inputs={"operation": operation or _DELTA_OPERATION, "global_comparison": True, "program": program},
        bindings=_bindings(),
    )
    result = PythonComputeTool().execute(request, _ctx([left, right]))
    assert result.artifact is not None, result.outcome
    return result.artifact, [left, right]


def _project(goal: Goal, artifact, sources):
    requires = [r.id for r in goal.requirements]
    needs = [PlannerNeed(id="n2", capability="python_compute", objective=goal.raw_query, requires=requires)]
    return Judge().evaluate(goal, [*sources, artifact], needs)


# ----------------------------------------------------------------- positive controls

def test_valid_difference_still_admitted():
    from tests.governed_operation_fixture import compute, project
    goal,artifact,sources = compute()
    projection = project(goal,artifact,sources)
    assert projection.goal_status.value == "COMPLETE"
    assert any("20" in claim.text for claim in projection.claims)
    assessment = next(a for a in projection.artifact_assessments if a.artifact_id == artifact.id)
    assert 'SUBTRACT' in assessment.derived_operations


def test_valid_ratio_admitted_for_ratio_requirement():
    from tests.governed_operation_fixture import compute, goal as typed_goal, project
    goal,artifact,sources = compute(typed_goal('RATIO',field='ratio'))
    projection = project(goal,artifact,sources)
    assert projection.goal_status.value == "COMPLETE"
    assert any("ratio" in claim.text for claim in projection.claims)


def test_valid_sum_admitted_for_total_requirement():
    from tests.governed_operation_fixture import compute, goal as typed_goal, project
    goal,artifact,sources = compute(typed_goal('SUM',field='total'))
    projection = project(goal,artifact,sources)
    assert projection.goal_status.value == "COMPLETE"


def test_valid_mean_admitted_for_average_requirement():
    from tests.governed_operation_fixture import compute, goal as typed_goal, project
    goal,artifact,sources = compute(typed_goal('MEAN',field='average'))
    projection = project(goal,artifact,sources)
    assert projection.goal_status.value == "COMPLETE"


def test_valid_ranking_admitted_for_ranking_requirement():
    from tests.governed_operation_fixture import compute, goal as typed_goal, project
    goal,artifact,sources = compute(typed_goal('RANK',field='ranking',parameters={'direction':'DESC'}))
    projection = project(goal,artifact,sources)
    assert projection.goal_status.value == "COMPLETE"


# ----------------------------------------------------------------- negative controls

_AUDIT_ATTACKS = {
    "alias_constant": 'seen = inputs["n2023"][0]["home_runs"]\nfabricated = 8675309\noutput = [{"difference": fabricated}]',
    "arithmetic_constant": 'seen = inputs["n2023"][0]["home_runs"]\noutput = [{"difference": 8000000 + 675309}]',
    "categorical": 'seen = inputs["n2023"][0]["home_runs"]\nlabel = "UNANIMOUS_HALL_OF_FAME_SELECTION"\noutput = [{"career_honor": label}]',
    "medical": 'seen = inputs["n2023"][0]["avg"]\nhealth = "CLEARED_FOR_FULL_BASEBALL_ACTIVITY"\noutput = [{"medical_clearance": health}]',
    "foreign_ranking": 'seen = inputs["n2023"][0]["home_runs"]\nname = "Bobby Witt Jr."\nrank = 1\noutput = [{"league_leader": name, "rank": rank}]',
    "literal_control": 'seen = inputs["n2023"][0]["home_runs"]\noutput = [{"difference": 8675309}]',
    "hidden_temp": 'value = 8675309\ntmp = value\noutput = [{"difference": tmp}]',
    "read_then_overwrite": 'a = inputs["n2023"][0]["home_runs"]\na = 8675309\noutput = [{"difference": a}]',
    "dead_branch": 'if False:\n    a = inputs["n2023"][0]["home_runs"]\nelse:\n    a = 8675309\noutput = [{"difference": a}]',
    "logging_only": 'seen = inputs["n2023"][0]["home_runs"]\nnote = str(seen)\noutput = [{"difference": 8675309}]',
    "helper_constant": 'def helper(x):\n    return 8675309\nseen = inputs["n2023"][0]["home_runs"]\noutput = [{"difference": helper(seen)}]',
    "arithmetic_identity": 'seen = inputs["n2023"][0]["home_runs"]\noutput = [{"difference": seen * 0 + 8675309}]',
    "mixed_valid_and_fabricated": 'a = inputs["n2023"][0]["home_runs"]\nb = inputs["n2024"][0]["home_runs"]\noutput = [{"difference": a - b, "note": "CLEARED"}]',
}


@pytest.mark.parametrize("name", sorted(_AUDIT_ATTACKS))
def test_fabricated_or_unsupported_output_never_mints_a_fact(name):
    artifact, sources = _run(_AUDIT_ATTACKS[name])
    goal = _goal("Calculate the numerical difference between these two season totals.")
    projection = _project(goal, artifact, sources)
    assert projection.goal_status.value != "COMPLETE"
    assert not projection.claims
    assessment = next(a for a in projection.artifact_assessments if a.artifact_id == artifact.id)
    assert assessment.status in {"REJECTED", "QUALIFIED"}


def test_wrong_operation_does_not_satisfy_a_difference_requirement():
    artifact, sources = _run('a = inputs["n2023"][0]["home_runs"]\nb = inputs["n2024"][0]["home_runs"]\noutput = [{"ratio": a / b}]')
    goal = _goal("Calculate the numerical difference between these two season totals.")
    projection = _project(goal, artifact, sources)
    assert projection.goal_status.value != "COMPLETE"
    assert not projection.claims


def test_wrong_field_difference_does_not_satisfy_requested_field():
    # The program reads a real but wrong column (avg); the operand field is factual,
    # yet the value cannot be tied to the requested measure, so it is not admitted as
    # the requested home-run difference.
    artifact, sources = _run('a = inputs["n2023"][0]["avg"]\nb = inputs["n2024"][0]["avg"]\noutput = [{"difference": a - b}]')
    goal = _goal("Calculate the numerical difference in home runs between these two seasons.")
    projection = _project(goal, artifact, sources)
    # Operation correspondence alone does not launder a field the operand receipt never
    # declared as the requested measure.
    assessment = next(a for a in projection.artifact_assessments if a.artifact_id == artifact.id)
    assert assessment.status == 'REJECTED'
    assert not projection.claims  # Python/operand occurrence cannot authorize any lane


def test_ranking_on_a_different_field_does_not_satisfy_the_requested_measure():
    artifact, sources = _run('rows = inputs["n2023"] + inputs["n2024"]\noutput = sorted(rows, key=lambda row: row["home_runs"], reverse=True)')
    goal = _goal("Rank the seasons by batting average, highest first.", relation=_VALUE_RELATION)
    projection = _project(goal, artifact, sources)
    assert projection.goal_status.value != "COMPLETE"
    assert not projection.claims


def test_one_unapproved_operand_fails_closed():
    artifact, sources = _run('a = inputs["n2023"][0]["home_runs"]\nb = inputs["n2024"][0]["home_runs"]\noutput = [{"difference": a - b}]')
    forged = artifact.model_copy(deep=True)
    forged.structured_data["input_artifact_ids"] = ["art_2023", "art_missing"]
    forged.structured_data["input_names"] = {
        "n2023": {"artifact_id": "art_2023", "export_id": "exp_art_2023"},
        "n2024": {"artifact_id": "art_missing", "export_id": "exp_art_missing"},
    }
    goal = _goal("Calculate the numerical difference between these two season totals.")
    projection = _project(goal, forged, sources)
    assert projection.goal_status.value != "COMPLETE"
    assert not projection.claims
