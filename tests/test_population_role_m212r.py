from tools.analytical_requirement_fixture import fixture_inputs
import copy
from types import SimpleNamespace

import pytest

from app.capabilities.analytics.compiler import SQLCompiler
from app.capabilities.analytics.conformance import attest, bind_obligations, compare, constraint_obligations, requested_contract
from app.capabilities.analytics.ir import AnalyticalQuery, validate_query
from app.capabilities.analytics.predicates import canonical_population, canonical_predicate
from app.capabilities.analytics.tool import LocalAnalyticsTool
from app.contracts.models import ToolRequest
from tools.m212r_cases import DEV_SEEDS, case, mutations


def contracts(c, raw=None):
    c = {**c, "inputs":fixture_inputs(c["inputs"],replace=True)}
    request=ToolRequest(need_id="test", capability="local_analytics", objective="Calculate bound metrics", inputs=c["inputs"])
    tool=LocalAnalyticsTool(None,None)
    intent,issues=bind_obligations(tool._intent(request),request.inputs)
    wanted=requested_contract(intent,{**request.inputs,"obligation_issues":issues},tool._requested_measures(request,intent))
    q=validate_query(AnalyticalQuery.model_validate(raw if raw is not None else c["raw"]))
    return wanted,attest(q,SQLCompiler().compile(q,"POSTGRES"))


@pytest.mark.parametrize("seed",DEV_SEEDS)
def test_fresh_population_role_mutations(seed):
    c=case(seed);wanted,executed=contracts(c)
    assert compare(wanted,executed,check_availability=False).status == "MATCH", (seed,wanted,executed)
    for mutation in mutations(c):
        actual=contracts(c,mutation["ir"])[1]
        comparison=compare(wanted,actual,check_availability=False)
        assert comparison.status == mutation["expected"], (seed,mutation["name"],comparison)


@pytest.mark.parametrize("basis,wording",[("PA","plate appearances"),("PA","打席"),("BBE","BBE"),("PITCHES","pitches"),("GAMES","games")])
@pytest.mark.parametrize("threshold",[0,47,219,431])
def test_finite_qualification_forms(basis,wording,threshold):
    for text in (f"at least {threshold} {wording}",f"minimum {threshold} {wording}",f"至少{threshold}个{wording}"):
        assert constraint_obligations(text)["qualification"] == dict(basis=basis,minimum=threshold)


@pytest.mark.parametrize("wording",["split_by_pitcher_handedness","group by pitcher handedness","split by opposing pitcher handedness (LHP vs RHP)","split by p_throws"])
def test_handedness_split_is_grouping(wording):
    assert constraint_obligations(wording) == {"group_by":["p_throws"]}


def test_top_n_context_and_tie_only_have_distinct_ownership():
    inputs={"entity_role":"BATTER","analytical_requirements":[{"kind":"ANALYSIS","statement":"Rank batters by walks","value":{"objective":"Rank batters by walks"}},{"kind":"CONSTRAINT","value":"top 9 batters"},{"kind":"CONSTRAINT","value":"tie-break by ascending MLBAM ID"}]}
    intent,issues=bind_obligations({"objective":"Rank batters by walks","entities":[]},fixture_inputs(inputs))
    assert not issues
    assert intent["execution_constraints"]["ordering"] == [{"metric":"walks","direction":"DESC"},{"field":"batter_id","direction":"ASC"}]
    assert intent["execution_constraints"]["limit"] == 9
    intent,issues=bind_obligations({"objective":"Calculate walks","entities":[]}, fixture_inputs({"entity_role":"BATTER","analytical_requirements":[{"kind":"CONSTRAINT","value":{"analytical_role":"BATTER"}}, inputs["analytical_requirements"][2]]}))
    assert not issues and "ordering" not in intent["execution_constraints"]
    assert intent["execution_constraints"]["tie_break_ordering"] == [{"field":"batter_id","direction":"ASC"}]


def test_generic_player_is_typed_before_canonical_build_or_execution():
    def fail(*args,**kwargs): raise AssertionError("unresolved role reached execution")
    tool=LocalAnalyticsTool(SimpleNamespace(run=fail),None)
    request=ToolRequest(need_id="test",capability="local_analytics",objective="overall performance",inputs={"entity_role":"PLAYER","entity_sets":[{"role":"PLAYER","ids":[982345]}],"require_entities":True,"intent":{"season":2024}})
    result=tool.execute(request)
    assert result.artifact is None and result.outcome.code == "ANALYTICAL_OBLIGATION_UNPROVEN"


def test_default_population_rejects_every_fresh_additional_predicate():
    c=case(212310002);c["raw"]["filters"]=[];c["inputs"]["constraints"]["filters"]=[]
    wanted,_=contracts(c)
    for p in [dict(op="COMPARE",field="release_speed",comparator=">=",value=87.5),dict(op="IN",field="description",values=["foul","ball"]),dict(op="BETWEEN",field="strikes",low=1,high=2)]:
        q=copy.deepcopy(c["raw"]);q["filters"]=[p]
        assert compare(wanted,contracts(c,q)[1],check_availability=False).status == "MISMATCH"


def test_finite_compound_equivalence_and_sql_not_conjunction():
    a=dict(op="IN",field="pitch_type",values=["CH","SI","CH"])
    b=dict(op="COMPARE",field="strikes",comparator="=",value=2)
    assert canonical_population([a,b]) == canonical_population([dict(op="AND",conditions=[b,a,a])])
    assert canonical_predicate(dict(op="NOT",conditions=[a,b])) == canonical_predicate(dict(op="NOT",conditions=[dict(op="AND",conditions=[b,a])]))
    assert canonical_predicate(dict(op="COMPARE",field="batter_id",comparator="=",value="982345")) == canonical_predicate(dict(op="IN",field="batter_id",values=[982345]))
    assert canonical_predicate(dict(op="BETWEEN",field="release_speed",low=81,high=97)) == canonical_predicate(dict(op="AND",conditions=[dict(op="COMPARE",field="release_speed",comparator=">=",value=81.0),dict(op="COMPARE",field="release_speed",comparator="<=",value=97)]))


def test_unproven_predicate_and_role_cannot_match():
    c=case(212310002);wanted,a=contracts(c)
    assert compare(wanted,a.model_copy(update={"analytical_role":None}),check_availability=False).status == "MISMATCH"
    for p in [dict(op="COMPARE",field="stand",comparator="=",value=7),dict(op="IN",field="pitch_type",values=[None]),dict(op="UNKNOWN",field="inning")]:
        assert compare(wanted,a.model_copy(update={"filters":(p,)}),check_availability=False).status != "MATCH"


def test_structured_qualification_does_not_erase_other_dimensions():
    value=dict(qualification=dict(basis="PA",minimum=219),filters=[dict(op="IN",field="pitch_type",values=["SI","FC"])],group_by=["stand"],limit=4)
    assert constraint_obligations(value) == value
    assert constraint_obligations({**value,"unsupported_context":"unknown"}) is None


def test_bound_analysis_dimensions_supersede_need_drift():
    intent,issues=bind_obligations({"objective":"rank batters by walks","entities":[]},
                                  fixture_inputs({"entity_role":"BATTER","analytical_requirements":[{"kind":"ANALYSIS","statement":"Rank pitchers by strikeouts, top 6","value":{"objective":"Rank pitchers by strikeouts, top 6"}}]}))
    assert not issues and intent["analytical_role"] == "PITCHER"
    assert intent["execution_constraints"]["group_by"] == ["pitcher_id"]
    assert intent["execution_constraints"]["ordering"] == [{"metric":"strikeouts","direction":"DESC"}]
    assert intent["execution_constraints"]["limit"] == 6


def test_bound_measure_identity_does_not_acquire_planner_alias_obligations():
    request=ToolRequest(need_id="test",capability="local_analytics",objective="avg pitch release speed",inputs=fixture_inputs({"requested_measures":[{"key":"avg_release_speed"}],"intent":{"measure":"avg release speed"}}))
    tool=LocalAnalyticsTool(None,None)
    assert [m.key for m in tool._requested_measures(request,tool._intent(request))] == ["avg_release_speed"]


def test_unsupported_ordering_semantics_are_unproven():
    c=case(212310003);c["inputs"]["constraints"]["ordering"][0]["nulls"]="FIRST"
    wanted,a=contracts(c)
    assert wanted.issues and compare(wanted,a,check_availability=False).status != "MATCH"
    c["inputs"]["constraints"]["ordering"][0]["nulls"]="last"
    wanted,a=contracts(c)
    assert not wanted.issues and compare(wanted,a,check_availability=False).status == "MATCH"
