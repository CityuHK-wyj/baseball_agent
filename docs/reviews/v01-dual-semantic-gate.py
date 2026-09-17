"""Dual semantic reconciliation gate.

Mandatory acceptance evidence for the v0.1 dual semantic runtime. It runs entirely
offline with scripted providers and exits nonzero while any case is wrong.

For every case it checks:

* the reconciliation outcome (agreement, material disagreement, ambiguity, failure);
* the canonical typed semantics OR a clarification;
* that no explicit constraint is silently dropped.

Run from the repository root:

    python3 docs/reviews/v01-dual-semantic-gate.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.llm.provider import FakeModelProvider, ProviderError  # noqa: E402
from app.models.contracts import (CountConstraint, NumericConstraint,  # noqa: E402
                                  PopulationConstraint, QualificationConstraint,
                                  RankingConstraint)
from app.semantic.dual_parser import (DualSemanticParser,  # noqa: E402
                                      MATERIAL_DISAGREEMENT, SEMANTIC_UNAVAILABLE)
from app.semantic.semantic_extractor import (LLMSemanticExtractor,  # noqa: E402
                                             LLMSemanticReviewer)

VOCABULARY = None


def payload(*constraints, ambiguities=()):
    return json.dumps({"constraints": list(constraints), "ambiguities": list(ambiguities)})


NUMERIC_PV_95 = {"kind": "NUMERIC", "metric": "pitch_velocity", "operator": "GTE",
                 "value": 95, "unit": "mph", "origin": "USER_EXPLICIT",
                 "evidence": {"text": "fastballs at least 95 mph"}}
NUMERIC_EV_95 = {"kind": "NUMERIC", "metric": "exit_velocity", "operator": "GTE",
                 "value": 95, "unit": "mph", "origin": "USER_EXPLICIT",
                 "evidence": {"text": "exit velocity at least 95 mph"}}
RANK_MAX_EV = {"kind": "RANKING", "metric_key": "exit_velocity", "aggregation": "MAX",
               "direction": "DESC", "limit": 5, "origin": "USER_EXPLICIT",
               "evidence": {"text": "top 5 by maximum exit velocity"}}
RANK_AVG_EV = {"kind": "RANKING", "metric_key": "exit_velocity", "aggregation": "AVG",
               "direction": "DESC", "limit": 5, "origin": "USER_EXPLICIT",
               "evidence": {"text": "top 5 by average exit velocity"}}
QUAL_100 = {"kind": "QUALIFICATION", "min_batted_balls": 100, "origin": "USER_EXPLICIT",
            "evidence": {"text": "at least 100 BBE"}}
QUAL_20 = {"kind": "QUALIFICATION", "min_batted_balls": 20, "origin": "USER_EXPLICIT",
           "evidence": {"text": "at least 20 BBE"}}
POP_EXHIBITION = {"kind": "POPULATION", "game_types": ["EXHIBITION"],
                  "event_population": "BATTED_BALL", "origin": "USER_EXPLICIT",
                  "evidence": {"text": "exhibition games"}}
POP_ALL_PITCHES = {"kind": "POPULATION", "game_types": ["REGULAR_SEASON"],
                   "event_population": "ALL_PITCHES", "origin": "USER_EXPLICIT",
                   "evidence": {"text": "all pitches"}}
COUNT_0_2_1_1 = {"kind": "COUNT", "origin": "USER_EXPLICIT",
                 "states": [{"balls": 0, "strikes": 2}, {"balls": 1, "strikes": 1}],
                 "evidence": {"text": "0-2 or 1-1"}}
LOC_UPPER_EDGE = {"kind": "LOCATION", "definition": "BATTER_RELATIVE_UPPER_EDGE",
                  "origin": "USER_EXPLICIT", "evidence": {"text": "batter-relative upper edge"}}

FAIL = object()


def build_parser(extractor_payloads, reviewer_payloads):
    if extractor_payloads is FAIL:
        extractor = LLMSemanticExtractor(FakeModelProvider(error=ProviderError("down")),
                                         "extractor")
    else:
        extractor = LLMSemanticExtractor(FakeModelProvider(responses=extractor_payloads),
                                         "extractor")
    if reviewer_payloads is FAIL:
        reviewer = LLMSemanticReviewer(FakeModelProvider(error=ProviderError("down")),
                                       "reviewer")
    else:
        reviewer = LLMSemanticReviewer(FakeModelProvider(responses=reviewer_payloads),
                                       "reviewer")
    return DualSemanticParser(extractor, reviewer, extractor_model="extractor",
                              reviewer_model="reviewer")


def kinds(result):
    return {item.kind for item in result.constraints}


def has(result, cls, **fields):
    for item in result.constraints:
        if isinstance(item, cls) and all(getattr(item, key) == value
                                         for key, value in fields.items()):
            return True
    return False


def run_case(case):
    parser = build_parser(case.get("extractor", [payload()]), case.get("reviewer", [payload()]))
    result = parser.parse(case["query"])
    failures = []
    if case["expect_clarification"] != bool(result.clarification_reason
                                            or result.location_wording_requested):
        failures.append(f"clarification={result.clarification_reason!r} "
                        f"requested={result.location_wording_requested}")
    if case["expect_clarification"] and result.constraints:
        failures.append("constraints produced despite clarification")
    for check in case.get("checks", ()):  # noqa: B007 - executed below
        if not check(result):
            failures.append(f"check failed: {check.__name__}")
    if case.get("expect_agreement") and result.review is not None \
            and result.review.agreement_status != case["expect_agreement"]:
        failures.append(f"agreement={result.review.agreement_status}")
    return failures


# -- the 20-case evaluation corpus ------------------------------------------

def c_full_compound(result):
    return has(result, NumericConstraint, key="pitch_velocity", operator="GTE", value=95.0) \
        and has(result, RankingConstraint, metric_key="exit_velocity", aggregation="MAX") \
        and has(result, QualificationConstraint, min_batted_balls=100)


def c_avg_qualification(result):
    return has(result, RankingConstraint, metric_key="exit_velocity", aggregation="AVG") \
        and has(result, QualificationConstraint, min_batted_balls=20)


def c_both_metrics(result):
    return has(result, NumericConstraint, key="pitch_velocity", operator="GTE", value=95.0) \
        and has(result, NumericConstraint, key="exit_velocity", operator="GTE", value=95.0)


def c_count_states(result):
    count = next((item for item in result.constraints if isinstance(item, CountConstraint)), None)
    return count is not None and count.exact_states == ((0, 2), (1, 1))


def c_generic_two_strikes(result):
    count = next((item for item in result.constraints if isinstance(item, CountConstraint)), None)
    return count is not None and len(count.exact_states) == 4


def c_game_types(expected):
    def check(result):
        pop = next((item for item in result.constraints
                    if isinstance(item, PopulationConstraint)), None)
        return pop is not None and tuple(pop.game_types) == tuple(expected)
    check.__name__ = f"game_types_{expected}"
    return check


def c_all_pitches(result):
    pop = next((item for item in result.constraints
                if isinstance(item, PopulationConstraint)), None)
    return pop is not None and pop.event_population == "ALL_PITCHES"


def c_location_definition(expected):
    def check(result):
        from app.models.contracts import LocationConstraint
        return any(isinstance(item, LocationConstraint) and item.definition == expected
                   for item in result.constraints)
    check.__name__ = f"location_{expected}"
    return check


def c_qualification(value):
    def check(result):
        return has(result, QualificationConstraint, min_batted_balls=value)
    check.__name__ = f"qualification_{value}"
    return check


def c_ranking(expected_metric, expected_aggregation):
    def check(result):
        return has(result, RankingConstraint, metric_key=expected_metric,
                   aggregation=expected_aggregation)
    check.__name__ = f"ranking_{expected_metric}_{expected_aggregation}"
    return check


CASES = (
    # 1
    dict(id="fastball_95_max_ev_qualification",
         query="top 5 by maximum exit velocity on fastballs at least 95 mph with at least 100 BBE in 2025",
         extractor=[payload(NUMERIC_PV_95, RANK_MAX_EV, QUAL_100)],
         reviewer=[payload(NUMERIC_PV_95, RANK_MAX_EV, QUAL_100)],
         expect_clarification=False, checks=[c_full_compound]),
    # 2
    dict(id="avg_ev_qualification",
         query="top 5 by average exit velocity with at least 20 BBE in 2025",
         extractor=[payload(RANK_AVG_EV, QUAL_20)],
         reviewer=[payload(RANK_AVG_EV, QUAL_20)],
         expect_clarification=False, checks=[c_avg_qualification]),
    # 3
    dict(id="pitch_and_exit_velocity_same_sentence",
         query=("top 5 by maximum exit velocity on fastballs at least 95 mph with exit velocity "
                "at least 95 mph in 2025"),
         extractor=[payload(NUMERIC_PV_95, NUMERIC_EV_95, RANK_MAX_EV)],
         reviewer=[payload(NUMERIC_EV_95, NUMERIC_PV_95, RANK_MAX_EV)],
         expect_clarification=False, checks=[c_both_metrics]),
    # 4
    dict(id="exact_compound_counts",
         query="top 5 by maximum exit velocity on 0-2 or 1-1 counts in 2025",
         extractor=[payload(RANK_MAX_EV, COUNT_0_2_1_1)],
         reviewer=[payload(COUNT_0_2_1_1, RANK_MAX_EV)],
         expect_clarification=False, checks=[c_count_states]),
    # 5
    dict(id="generic_two_strikes",
         query="top 5 by average exit velocity after two strikes in 2025",
         extractor=[payload(RANK_AVG_EV, {"kind": "COUNT", "strikes": 2, "origin": "USER_EXPLICIT",
                                          "evidence": {"text": "two strikes"}})],
         reviewer=[payload(RANK_AVG_EV, {"kind": "COUNT", "strikes": 2, "origin": "USER_EXPLICIT",
                                         "evidence": {"text": "after two strikes"}})],
         expect_clarification=False, checks=[c_generic_two_strikes]),
    # 6
    dict(id="regular_season",
         query="top 5 by average exit velocity in the regular season in 2025",
         extractor=[payload(RANK_AVG_EV, {"kind": "POPULATION", "game_types": ["REGULAR_SEASON"],
                                          "event_population": "BATTED_BALL",
                                          "origin": "USER_EXPLICIT",
                                          "evidence": {"text": "regular season"}})],
         reviewer=[payload(RANK_AVG_EV)],
         expect_clarification=False, checks=[c_game_types(("REGULAR_SEASON",))]),
    # 7
    dict(id="postseason",
         query="top 5 by average exit velocity in the postseason in 2025",
         extractor=[payload(RANK_AVG_EV, {"kind": "POPULATION", "game_types": ["POSTSEASON"],
                                          "event_population": "BATTED_BALL",
                                          "origin": "USER_EXPLICIT",
                                          "evidence": {"text": "postseason"}})],
         reviewer=[payload(RANK_AVG_EV, {"kind": "POPULATION", "game_types": ["POSTSEASON"],
                                         "event_population": "BATTED_BALL",
                                         "origin": "USER_EXPLICIT",
                                         "evidence": {"text": "postseason"}})],
         expect_clarification=False, checks=[c_game_types(("POSTSEASON",))]),
    # 8
    dict(id="exhibition",
         query="top 5 by maximum exit velocity in exhibition games in 2025",
         extractor=[payload(RANK_MAX_EV, POP_EXHIBITION)],
         reviewer=[payload(RANK_MAX_EV, POP_EXHIBITION)],
         expect_clarification=False, checks=[c_game_types(("EXHIBITION",))]),
    # 9
    dict(id="explicit_all_pitches",
         query="rank hitters by maximum exit velocity over all pitches in 2025",
         extractor=[payload(RANK_MAX_EV, POP_ALL_PITCHES)],
         reviewer=[payload(RANK_MAX_EV)],
         expect_clarification=True,
         expect_agreement="MATERIAL_DISAGREEMENT"),
    # 10
    dict(id="all_pitches_exit_velocity_incompatible",
         query="rank hitters by maximum exit velocity over all pitches in 2025",
         extractor=[payload(RANK_MAX_EV, POP_ALL_PITCHES)],
         reviewer=[payload(RANK_MAX_EV, POP_ALL_PITCHES)],
         expect_clarification=True),
    # 11
    dict(id="ambiguous_high_fastballs",
         query="top 5 by exit velocity on high fastballs in 2025",
         extractor=[payload(RANK_AVG_EV, LOC_UPPER_EDGE,
                            ambiguities=[{"kind": "location.upper_edge",
                                          "evidence": {"text": "high fastballs"}}])],
         reviewer=[payload(RANK_AVG_EV, LOC_UPPER_EDGE)],
         expect_clarification=True),
    # 12
    dict(id="batter_relative_upper_edge",
         query="top 5 by exit velocity on fastballs near the upper edge of the strike zone in 2025",
         extractor=[payload(RANK_AVG_EV, LOC_UPPER_EDGE)],
         reviewer=[payload(RANK_AVG_EV, LOC_UPPER_EDGE)],
         expect_clarification=True),
    # 13
    dict(id="multiple_numeric_constraints",
         query=("top 5 by maximum exit velocity on fastballs at least 95 mph with exit velocity "
                "at least 100 mph in 2025"),
         extractor=[payload(NUMERIC_PV_95,
                            dict(NUMERIC_EV_95, value=100, evidence={"text": "exit velocity at least 100 mph"}),
                            RANK_MAX_EV)],
         reviewer=[payload(dict(NUMERIC_EV_95, value=100, evidence={"text": "exit velocity at least 100 mph"}),
                           NUMERIC_PV_95, RANK_MAX_EV)],
         expect_clarification=False,
         checks=[lambda result: has(result, NumericConstraint, key="exit_velocity", value=100.0)]),
    # 14
    dict(id="spelled_number_qualification",
         query="top 5 by maximum exit velocity with at least twenty batted balls in 2025",
         extractor=[payload(RANK_MAX_EV, dict(QUAL_20, evidence={"text": "at least twenty batted balls"}))],
         reviewer=[payload(RANK_MAX_EV, dict(QUAL_20, evidence={"text": "twenty batted balls"}))],
         expect_clarification=False, checks=[c_qualification(20)],
         expect_agreement="AGREE"),
    # 15
    dict(id="contradictory_aggregation_wording",
         query="top 5 by maximum exit velocity and average exit velocity in 2025",
         extractor=[payload(RANK_MAX_EV, dict(RANK_AVG_EV, evidence={"text": "average exit velocity"}))],
         reviewer=[payload(RANK_MAX_EV, dict(RANK_AVG_EV, evidence={"text": "average exit velocity"}))],
         expect_clarification=True),
    # 16
    dict(id="provider_extractor_failure",
         query="top 5 by maximum exit velocity on fastballs at least 95 mph with at least 100 BBE in 2025",
         extractor=FAIL,
         reviewer=[payload(NUMERIC_PV_95, RANK_MAX_EV, QUAL_100)],
         expect_clarification=False, checks=[c_full_compound],
         expect_agreement="EXTRACTOR_UNAVAILABLE"),
    # 17
    dict(id="provider_reviewer_failure",
         query="top 5 by maximum exit velocity on fastballs at least 95 mph with at least 100 BBE in 2025",
         extractor=[payload(NUMERIC_PV_95, RANK_MAX_EV, QUAL_100)],
         reviewer=FAIL,
         expect_clarification=False, checks=[c_full_compound],
         expect_agreement="REVIEWER_UNAVAILABLE"),
    # 18
    dict(id="both_providers_failure",
         query="top 5 by maximum exit velocity on fastballs at least 95 mph with at least 100 BBE in 2025",
         extractor=FAIL, reviewer=FAIL,
         expect_clarification=True),
    # 19
    dict(id="materially_disagreeing_candidates",
         query="top 5 by maximum exit velocity on fastballs at least 95 mph with at least 100 BBE in 2025",
         extractor=[payload(NUMERIC_PV_95, RANK_MAX_EV, QUAL_100)],
         reviewer=[payload(dict(NUMERIC_PV_95, operator="LTE", value=20), RANK_MAX_EV, QUAL_100)],
         expect_clarification=True,
         expect_agreement="MATERIAL_DISAGREEMENT"),
    # 20
    dict(id="equivalent_candidates_different_wording",
         query="top 5 by maximum exit velocity on fastballs at least 95 mph with at least 100 BBE in 2025",
         extractor=[payload(NUMERIC_PV_95, RANK_MAX_EV, QUAL_100)],
         reviewer=[payload(dict(NUMERIC_PV_95, evidence={"text": "at least 95 mph", "start": 0, "end": 13}),
                           dict(RANK_MAX_EV, evidence={"text": "maximum exit velocity"}),
                           dict(QUAL_100, evidence={"text": "100 BBE"}))],
         expect_clarification=False, checks=[c_full_compound],
         expect_agreement="AGREE"),
)


def main() -> int:
    failures = []
    for case in CASES:
        try:
            problems = run_case(case)
        except Exception as error:  # noqa: BLE001 - a gate must report, not crash
            problems = [f"exception {type(error).__name__}: {error}"]
        if problems:
            failures.append(case["id"])
        print(json.dumps({"case": case["id"], "passed": not problems,
                          "problems": problems}), flush=True)
    print(json.dumps({"unresolved_blockers": failures}))
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
