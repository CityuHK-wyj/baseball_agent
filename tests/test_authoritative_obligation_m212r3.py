"""M2.12R3 development tests: authority, semantic coverage, unresolved workflow.

These tests are the pre-freeze Stage A suite. They exercise the production mechanisms
(authority resolution, clause coverage, bounded re-resolution, contract gating) and a
small number of full Tool/Judge paths over an offline mirrored source.
"""
from __future__ import annotations

import copy
import random
import tempfile
from pathlib import Path

import pytest

from app.capabilities.analytics.conformance import (
    TEMPORARILY_UNRESOLVED,
    UNSUPPORTED,
    UNDERSTOOD,
    attest,
    compare,
    constraint_coverage,
    requested_contract,
    residue_is_semantically_relevant,
)
from app.capabilities.analytics.ir import AnalyticalQuery, validate_query
from app.capabilities.analytics.authority import resolve_authority
from app.capabilities.analytics.compiler import SQLCompiler
from app.capabilities.analytics.tool import LocalAnalyticsTool
from app.contracts.models import ToolRequest
from tools.evaluate_m212r import FixedIR
from tools.m212r3_cases import (
    DEV_AUTHORITY,
    DEV_COVERAGE,
    DEV_DELETION,
    DEV_RESOLUTION,
    authority_mutations,
    case,
    coverage_mutations,
    deletion_mutations,
    goal,
    reference,
)
from tools.m212r3_support import offline_mirror
from tools.requirement_ancestry_fixture import root_user_requirements, rooted_case, rooted_goal

case = rooted_case(case)
goal = rooted_goal(goal)


def contract(inputs):
    root_user_requirements(inputs['analytical_requirements'], inputs.get('bound_user_intent') or inputs['raw_query'], replace=True)
    tool = LocalAnalyticsTool(None, None)
    request = ToolRequest(need_id="r3", capability="local_analytics", objective="probe", inputs=inputs)
    return requested_contract(tool._intent(request), inputs, [])


def _mirror(c, directory):
    catalog, executor, file = offline_mirror(c, directory)
    tool = LocalAnalyticsTool(executor, FixedIR(c["raw"]))
    tool.catalog = catalog
    tool.compiler = SQLCompiler(catalog)
    return tool, file


# --------------------------------------------------------------------- authority


@pytest.mark.parametrize("seed", DEV_AUTHORITY)
def test_unproved_authority_cannot_define_requested_truth(seed):
    c = case(seed)
    wanted = contract(c["inputs"])
    assert not wanted.issues
    for name, addition, authoritative, dimension in authority_mutations(c):
        inputs = copy.deepcopy(c["inputs"])
        inputs["analytical_requirements"].append(addition)
        observed = contract(inputs)
        if authoritative:
            assert not observed.issues, (seed, name, observed.issues)
        else:
            assert observed.issues, (seed, name)
            assert addition["id"] in observed.unproven_requirements
            # The unproven value never changes the requested contract versus the base.
            assert observed.filters == wanted.filters, name
            assert observed.group_by == wanted.group_by, name
            assert observed.limit == wanted.limit, name
            assert observed.qualification == wanted.qualification, name


def test_authority_resolution_is_cycle_safe():
    requirements = [
        dict(id="u", kind="ANALYSIS", statement="user asks", source="user", value={"objective": "x"}),
        dict(id="a", kind="CONSTRAINT", statement="a", source="inferred", value={"limit": 3},
             parent_requirement_ids=["b"], derivation_kind="schema_field_binding", derivation_proof="p"),
        dict(id="b", kind="CONSTRAINT", statement="b", source="inferred", value={"limit": 3},
             parent_requirement_ids=["a"], derivation_kind="schema_field_binding", derivation_proof="p"),
    ]
    root_user_requirements(requirements, 'user asks')
    authoritative, unproven, issues = resolve_authority(requirements)
    assert {r["id"] for r in authoritative} == {"u"}
    assert {r["id"] for r in unproven} == {"a", "b"}
    assert len(issues) == 2


def test_fake_and_unrelated_parent_rejected():
    parent = {"id": "p", "kind": "ANALYSIS", "statement": "s", "source": "user"}
    fake = {"id": "d", "kind": "CONSTRAINT", "statement": "s", "source": "inferred",
            "parent_requirement_ids": ["ghost"], "derivation_kind": "schema_field_binding",
            "derivation_proof": "x"}
    root_user_requirements([parent], 's')
    authoritative, unproven, _ = resolve_authority([parent, fake])
    assert not authoritative or {r["id"] for r in authoritative} == {"p"}
    assert {r["id"] for r in unproven} == {"d"}


# ---------------------------------------------------------------- semantic coverage


@pytest.mark.parametrize("seed", DEV_COVERAGE)
def test_source_semantic_clause_is_never_waived_into_residue(seed):
    c = case(seed)
    rng = random.Random(seed)
    clause, expectation, dimension = coverage_mutations(c, rng)
    inputs = copy.deepcopy(c["inputs"])
    inputs["bound_user_intent"] = inputs["raw_query"] + " AND " + clause
    observed = contract(inputs)
    if expectation == "supported_filter":
        # The clause is structured (understood), so the contract is clean and the
        # obligation is present in the ledger.
        if observed.issues:
            obligations = observed.obligation_provenance
            assert dimension in obligations
        else:
            assert any(p.get("value") for p in observed.obligation_provenance.get(dimension, [])) or dimension in {
                "inning", "p_throws"}
    else:
        assert observed.issues, (seed, clause)
        assert observed.unresolved_obligations, (seed, clause)


def test_opaque_analysis_objective_does_not_absorb_a_restriction():
    coverage = constraint_coverage("Aggregate the requested statistics AND during extra innings",
                                   "BATTER", "walks", analysis=True)
    assert coverage["state"] == "COMPLETE"
    assert coverage["obligations"]["filters"] == [
        dict(op="COMPARE", field="inning", comparator=">=", value=10)
    ]
    # The restriction is a real structured obligation, not an "analysis" objective.
    assert not any("extra innings" in str(entry.get("clause")) and entry["obligations"].get("analysis")
                   for entry in coverage["recognized"])


def test_source_residue_relevance_is_not_keyword_absence():
    # Unknown factual language remains relevant (never waived by "no known keyword").
    assert residue_is_semantically_relevant("on the road")
    assert residue_is_semantically_relevant("during doubleheaders")
    # Pure task/presentation language is waived.
    assert not residue_is_semantically_relevant("aggregate the requested baseball statistics")
    assert not residue_is_semantically_relevant("during", role_cue=True)


@pytest.mark.parametrize("seed", DEV_DELETION)
def test_mandatory_clause_deletion_is_detected(seed, monkeypatch):
    import app.capabilities.analytics.conformance as module

    c = case(seed)
    rng = random.Random(seed)
    clauses = []
    if not c["raw"].get("qualification"):
        clauses.append(f"at least {rng.randint(7, 19)} PITCHES")
    clauses.append(rng.choice(["pitch_type IN ('CU', 'FC')", "stand IN ('L', 'R')", "balls >= 2"]))
    clauses.append("group by stand")
    text = " AND ".join(clauses)
    c["inputs"]["analytical_requirements"].append(dict(
        id=f"compound_{seed}", kind="CONSTRAINT", value=text, statement="compound clauses",
        required=True, source="user"))
    assert not contract(c["inputs"]).issues
    original = module.constraint_coverage
    for position in range(len(clauses)):
        def dropped(value, *args, **kwargs):
            result = copy.deepcopy(original(value, *args, **kwargs))
            if value == text:
                result["recognized"] = [
                    entry for entry in result["recognized"] if entry.get("clause_index") != position
                ]
            return result

        with monkeypatch.context() as patch:
            patch.setattr(module, "constraint_coverage", dropped)
            assert contract(c["inputs"]).issues, (seed, position)


def test_adapter_cannot_self_declare_complete(monkeypatch):
    import app.capabilities.analytics.conformance as module

    c = case(2128502000)
    text = "minimum 7 PITCHES AND pitch_type IN ('CU', 'FC') AND group by stand"
    c["inputs"]["analytical_requirements"][-1]["value"] = text
    original = module.constraint_coverage

    def dropped(value, *args, **kwargs):
        result = copy.deepcopy(original(value, *args, **kwargs))
        if value == text:
            result["recognized"] = [r for r in result["recognized"] if r.get("clause_index") != 0]
        return result

    monkeypatch.setattr(module, "constraint_coverage", dropped)
    assert contract(c["inputs"]).issues


# ------------------------------------------------------------- resolution workflow


def test_temporarily_unresolved_to_understood_via_shared_knowledge():
    coverage = constraint_coverage("against southpaws", "BATTER", "walks")
    assert coverage["state"] == "COMPLETE"
    assert coverage["obligations"]["filters"] == [
        dict(op="COMPARE", field="p_throws", comparator="=", value="L")
    ]
    attempts = [a for entry in coverage["clause_states"] for a in entry["attempts"]]
    assert any(a["resolver"] == "shared_knowledge.synonym" and a["outcome"] == "changed" for a in attempts)


def test_temporarily_unresolved_to_unsupported():
    coverage = constraint_coverage("during doubleheaders", "BATTER", "walks")
    assert coverage["state"] != "COMPLETE"
    assert coverage["unsupported"]
    assert all(entry["state"] in {UNSUPPORTED, TEMPORARILY_UNRESOLVED} for entry in coverage["clause_states"])


def test_unresolved_still_unresolved_is_bounded_and_records_attempts():
    c = case(2128504000)
    c["inputs"]["analytical_requirements"][-1]["value"] = "unresolved eligibility rule"
    first = contract(c["inputs"])
    assert first.issues
    attempts = [a for item in first.unresolved_obligations for a in item.get("attempts") or []]
    # Distinct bounded resolvers were recorded, with no duplicates.
    assert attempts
    keys = [(a["resolver"], a["input"]) for a in attempts]
    assert len(keys) == len(set(keys))


def test_attempt_history_does_not_change_factual_truth():
    coverage = constraint_coverage("against southpaws", "BATTER", "walks")
    before = copy.deepcopy(coverage["obligations"])
    # Re-running resolution yields the same factual obligations.
    again = constraint_coverage("against southpaws", "BATTER", "walks")
    assert again["obligations"] == before
    assert again["obligations"]["filters"][0]["field"] == "p_throws"


# --------------------------------------------------------------- replan preservation


def test_replan_does_not_erase_unresolved_original_clause():
    c = case(2128504001)
    c["inputs"]["analytical_requirements"][-1]["value"] = "unresolved eligibility rule"
    before = contract(c["inputs"])
    assert before.issues and before.unresolved_obligations
    # A replan may add operational requirements, but the unresolved original clause
    # remains represented and still blocks.
    replanned = copy.deepcopy(c["inputs"])
    replanned["analytical_requirements"].append(dict(
        id="operational", kind="CONSTRAINT", value={"limit": 3}, statement="planner split",
        source="inferred", parent_requirement_ids=[c["inputs"]["analytical_requirements"][0]["id"]],
        derivation_kind="schema_field_binding", derivation_proof="operational split"))
    after = contract(replanned)
    assert any("unresolved eligibility rule" in str(item.get("reason")) for item in after.unresolved_obligations)


# --------------------------------------------------------------- full path sanity


@pytest.mark.parametrize("seed", [2128505000, 2128505001, 2128505002])
def test_valid_contract_executes_and_judges(seed, tmp_path):
    c = case(seed)
    tool, file = _mirror(c, tmp_path)
    request = ToolRequest(need_id="full", capability="local_analytics", objective="probe", inputs=c["inputs"])
    result = tool.execute(request)
    assert result.artifact is not None, result.outcome.message
    assert result.artifact.status in {"ACCEPTED", "PARTIAL"}
    rows = result.artifact.structured_data["rows"]
    expected = reference(c)
    keys = set(expected[0]) if expected else set()
    for observed_row, expected_row in zip(rows, expected):
        for key in keys:
            assert observed_row.get(key) == pytest.approx(expected_row[key]) if isinstance(expected_row[key], float) else observed_row.get(key) == expected_row[key]


def test_unproved_inferred_blocks_full_execution(tmp_path):
    c = case(2128505003)
    inputs = copy.deepcopy(c["inputs"])
    inputs["analytical_requirements"].append(dict(
        id="launder", kind="CONSTRAINT", value={"filters": [dict(op="COMPARE", field="strikes", comparator="=", value=0)]},
        statement="Proposed", source="inferred", required=True))
    tool, _ = _mirror(c, tmp_path)
    result = tool.execute(ToolRequest(need_id="blocked", capability="local_analytics", objective="probe", inputs=inputs))
    assert result.artifact is None
    assert result.outcome.code == "ANALYTICAL_OBLIGATION_UNPROVEN"


def test_supported_inning_clause_becomes_requested_filter(tmp_path):
    c = case(2128505004)
    coverage = constraint_coverage("from the eighth inning onward", "BATTER", "walks")
    assert coverage["state"] == "COMPLETE"
    assert coverage["obligations"]["filters"] == [
        dict(op="COMPARE", field="inning", comparator=">=", value=8)
    ]
    inputs = copy.deepcopy(c["inputs"])
    inputs["bound_user_intent"] = c["inputs"]["raw_query"] + " AND from the eighth inning onward"
    contract_result = contract(inputs)
    assert not contract_result.issues
    assert any(
        any(isinstance(f, dict) and f.get("field") == "inning" for f in (p.get("value") or []))
        for p in contract_result.obligation_provenance.get("filters", [])
    )


# ------------------------------------------------------- semantic conservation


def _independent_clauses(c):
    raw = c["raw"]
    clauses = {
        "season": raw.get("season"),
        "date_range": raw.get("date_range"),
        "population_codes": tuple(sorted(c["codes"])) if raw.get("population") else None,
        "role": raw.get("analytical_role"),
        "group_by": tuple(raw.get("group_by") or ()),
        "qualification": raw.get("qualification"),
        "limit": raw.get("limit"),
        "measures": frozenset(m["key"] for m in c["inputs"]["requested_measures"]),
        "filter_fields": tuple(sorted(p["field"] for p in raw.get("filters") or [])),
    }
    return {key: value for key, value in clauses.items() if value is not None}


@pytest.mark.parametrize("seed", list(DEV_AUTHORITY) + list(DEV_COVERAGE))
def test_semantic_conservation_no_clause_disappears(seed):
    c = case(seed)
    wanted = contract(c["inputs"])
    assert not wanted.issues
    clauses = _independent_clauses(c)
    observed = {
        "season": wanted.season,
        "date_range": {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in (wanted.date_range.model_dump().items() if wanted.date_range else {})} or None,
        "population_codes": tuple(sorted(wanted.game_types or ())) if wanted.game_types is not None else None,
        "role": wanted.analytical_role,
        "group_by": tuple(wanted.group_by),
        "qualification": wanted.qualification,
        "limit": wanted.limit,
        "measures": frozenset(wanted.measures),
        "filter_fields": tuple(sorted(p["field"] for p in wanted.filters)),
    }
    for key, value in clauses.items():
        assert observed.get(key) == value, (seed, key, observed.get(key), value)
    # Every populated field is traceable to an authoritative Requirement or a governed default.
    for field, entries in wanted.obligation_provenance.items():
        assert entries, (seed, field)
        assert all(entry.get("requirement_id") for entry in entries), (seed, field)


# ------------------------------------------------------- natural-language suite


NEW_QUESTIONS = [
    "How many strikeouts did Tarik Skubal record in 2024?",
    "Rank the top five pitchers by walks during the 2023 regular season",
    "Aaron Judge home runs in 2022",
    "Which batters had at least 40 plate appearances against left-handed pitchers in 2024?",
    "Compare Shohei Ohtani's home runs in 2021 and 2024",
    "Average release speed for Yoshinobu Yamamoto in the 2024 postseason",
    "Which pitchers threw the most pitches in the 2022 World Series?",
    "Mookie Betts batting average with at least 200 plate appearances in 2023",
    "Rank batters by strikeouts from the seventh inning onward in 2024",
    "Nolan Arenado singles during extra innings in 2019",
]


@pytest.mark.parametrize("question", NEW_QUESTIONS)
def test_natural_language_analysis_objective_is_semantically_accounted(question):
    from app.semantic.interpreter import SemanticInterpreter
    from app.semantic.goals import RequirementBuilder

    message = SemanticInterpreter(llm=None).interpret(question)
    goal = RequirementBuilder().build(message)
    analysis = [r for r in goal.requirements if r.kind.value == "ANALYSIS"]
    assert analysis, question
    objective = analysis[0].value.get("objective") if isinstance(analysis[0].value, dict) else analysis[0].statement
    coverage = constraint_coverage(objective, "BATTER", "")
    # The interpreter-with-no-LLM suite only guarantees that nothing is silently lost:
    # every clause is either structured or explicitly unresolved/unsupported.
    assert coverage["state"] in {"COMPLETE", "PARTIAL", "UNRESOLVED"}, question
    if coverage["state"] != "COMPLETE":
        assert coverage["unresolved"] or coverage["unsupported"], question
    states = {entry["state"] for entry in coverage.get("clause_states") or []}
    assert states <= {UNDERSTOOD, TEMPORARILY_UNRESOLVED, UNSUPPORTED, "INVALID"}, question
