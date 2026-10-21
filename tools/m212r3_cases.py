"""M2.12R3 fresh populations: authority, clause coverage, deletion and resolution.

These seeds are new (not reused from M2.12R/M2.12R2) and the natural-language wording
is newly authored. Facts are sampled at the Requirement layer before any candidate IR.
Independent reference reductions use the generated rows only; no production Artifact is
ground truth.
"""
from __future__ import annotations

import copy
import datetime as dt
import random

DEV_AUTHORITY = range(2128501000, 2128501024)
DEV_COVERAGE = range(2128502000, 2128502024)
DEV_DELETION = range(2128503000, 2128503024)
DEV_RESOLUTION = range(2128504000, 2128504024)
DEV_NATURAL = range(2128505000, 2128505014)

# Fresh analytical wording (not the frozen "Calculate batting metrics" strings).
ANALYSIS_PHRASES = (
    "Aggregate the requested baseball statistics",
    "Produce the requested player metrics",
    "Compute the requested performance numbers",
    "Summarize the requested baseball measures",
)
PITCHER_PHRASES = (
    "Aggregate the requested pitching statistics",
    "Produce the requested pitcher metrics",
)


def _analysis_phrase(rng: random.Random, role: str) -> str:
    return rng.choice(PITCHER_PHRASES if role == "PITCHER" else ANALYSIS_PHRASES)


def case(seed: int) -> dict:
    rng = random.Random(seed)
    role = rng.choice(["BATTER", "PITCHER"])
    year = rng.choice([2016, 2017, 2019, 2021, 2023, 2024, 2025])
    identity = "batter_id" if role == "BATTER" else "pitcher_id"
    start = dt.date(year, rng.choice([4, 6, 8, 9]), rng.randint(2, 17))
    end = start + dt.timedelta(days=rng.randint(9, 21))
    population, codes = rng.choice(
        [("REGULAR_SEASON", ["R"]), ("WORLD_SERIES", ["W"]), ("WILDCARD", ["F"]), ("POSTSEASON", ["F", "D", "L", "W"])]
    )
    metric, column, operation = rng.choice(
        [
            ("walks", "walk_total", dict(function="COUNT_IF", condition=dict(op="IN", field="events", values=["intent_walk", "walk"]))),
            ("strikeouts", "strike_total", dict(function="COUNT_IF", condition=dict(op="IN", field="events", values=["strikeout_double_play", "strikeout"]))),
            ("home_runs", "hr_total", dict(function="COUNT_IF", condition=dict(op="COMPARE", field="events", comparator="=", value="home_run"))),
            ("avg_release_speed", "velocity", dict(function="AVG", field="release_speed")),
        ]
    )
    shape = seed % 4
    group = [identity] if shape == 2 else [rng.choice(["pitch_type", "stand", "inning"])] if shape == 1 else []
    limit = rng.randint(2, 5) if shape == 2 else None
    direction = rng.choice(["ASC", "DESC"])
    qual = dict(basis=rng.choice(["PA", "PITCHES", "BBE"]), minimum=rng.randint(2, 6)) if shape == 3 else None
    predicate = dict(op="IN", field=rng.choice(["pitch_type", "stand", "p_throws"]), values=rng.sample(["FC", "ST", "CU", "FF"], 2))
    if predicate["field"] != "pitch_type":
        predicate["values"] = ["R"]
    filters = [predicate] if shape == 3 else []

    reqs: list[dict] = []

    def req(kind, value, statement, **extra):
        entry = dict(id=f"r3_{seed}_{len(reqs)}", kind=kind, value=copy.deepcopy(value), statement=statement,
                     required=True, source="user")
        entry.update(extra)
        reqs.append(entry)

    req("SEASON", year, f"Restrict to the {year} season")
    req("TIME", dict(start=start.isoformat(), end=end.isoformat()), "Exact inclusive window")
    req("GAME_POPULATION", population, f"Competition population {population}")
    req("MEASURE", dict(key=metric, derivable=True), metric)
    req("MEASURE", dict(key="pa", derivable=True), "Plate appearances")
    req("ANALYSIS", dict(objective=_analysis_phrase(rng, role)), "Requested analysis")
    constraint = dict(analytical_role=role, group_by=group, filters=filters)
    if limit:
        constraint.update(limit=limit, ordering=[dict(metric=metric, direction=direction)])
    if qual:
        constraint["qualification"] = qual
    req("CONSTRAINT", constraint, "Bound analytical dimensions")

    raw = dict(
        analytical_role=role, season=year,
        date_range=dict(start=start.isoformat(), end=end.isoformat()), population=population,
        filters=copy.deepcopy(filters), group_by=group,
        aggregates=[dict(name=column, **operation), dict(name="sample", function="COUNT_NON_NULL", field="events")],
        sort=[dict(name=column, direction=direction), dict(name=identity, direction="ASC")] if limit
        else [dict(name=group[0], direction="ASC")] if group else [],
        limit=limit, qualification=qual,
    )
    inputs = dict(
        analytical_requirements=copy.deepcopy(reqs),
        raw_query=_analysis_phrase(rng, role),
        intent=dict(objective="Aggregate the requested statistics", season=year, population=population,
                    date_range=raw["date_range"]),
        entity_role=role,
        requested_measures=[dict(key=metric), dict(key="pa")],
        constraints={},
    )
    ids = rng.sample(range(2100001, 3900000), 6)
    rows = []
    for j in range(260):
        rows.append(dict(
            game_date=start + dt.timedelta(days=rng.randint(-2, (end - start).days + 2)),
            game_type=rng.choice([*codes, "S", "R"]),
            batter_id=rng.choice(ids[:3]), pitcher_id=rng.choice(ids[3:]),
            events=rng.choice([None, "intent_walk", "walk", "single", "field_out", "strikeout_double_play", "strikeout", "home_run"]),
            release_speed=rng.choice([None, 78.5, 89.75, 97.25]),
            launch_speed=rng.choice([None, 90.0, 101.0]),
            pitch_type=rng.choice(["FC", "ST", "CU", "FF"]), stand=rng.choice(["L", "R", None]),
            p_throws=rng.choice(["R", "L", None]), inning=rng.randint(1, 12), strikes=rng.randint(0, 2),
            balls=rng.randint(0, 3), game_pk=j // 9,
        ))
    return dict(seed=seed, requirements=reqs, raw=raw, inputs=inputs, rows=rows, codes=codes,
                identity=identity, metric=metric, column=column, role=role)


def reference(c: dict) -> list[dict]:
    q = c["raw"]
    groups: dict = {}
    for row in c["rows"]:
        if row["game_type"] not in c["codes"]:
            continue
        if not q["date_range"]["start"] <= row["game_date"].isoformat() <= q["date_range"]["end"]:
            continue
        if any(row[p["field"]] is None or row[p["field"]] not in p["values"] for p in q["filters"]):
            continue
        key = tuple(row[g] for g in q["group_by"])
        groups.setdefault(key, []).append(row)
    if not q["group_by"] and not groups:
        groups[()] = []
    result = []
    for key, rows in groups.items():
        pa = sum(x["events"] is not None for x in rows)
        counts = {"PA": pa, "PITCHES": len(rows), "BBE": sum(x["launch_speed"] is not None for x in rows)}
        if q["qualification"] and counts[q["qualification"]["basis"]] < q["qualification"]["minimum"]:
            continue
        values = [x["release_speed"] for x in rows if x["release_speed"] is not None]
        if c["metric"] == "walks":
            value = sum(x["events"] in {"walk", "intent_walk"} for x in rows)
        elif c["metric"] == "strikeouts":
            value = sum(x["events"] in {"strikeout", "strikeout_double_play"} for x in rows)
        elif c["metric"] == "home_runs":
            value = sum(x["events"] == "home_run" for x in rows)
        else:
            value = sum(values) / len(values) if values else None
        result.append({**dict(zip(q["group_by"], key)), c["column"]: value, "sample": pa})
    for sort in reversed(q["sort"]):
        k = sort["name"]
        nonnull = [r for r in result if r[k] is not None]
        null = [r for r in result if r[k] is None]
        result = sorted(nonnull, key=lambda x: x[k], reverse=sort["direction"] == "DESC") + null
    return result[: q["limit"]] if q["limit"] else result


def goal(c: dict):
    """Build a current Goal from the case inputs (independent of any candidate IR)."""
    from app.contracts.models import Goal, RequirementKind, UserRequirement

    requirements = []
    for item in c["inputs"]["analytical_requirements"]:
        kind = RequirementKind(item["kind"])
        requirements.append(UserRequirement(
            id=item["id"], kind=kind, statement=item.get("statement", ""),
            required=item.get("required", True), source=item.get("source", "user"),
            value=copy.deepcopy(item.get("value")),
            parent_requirement_ids=list(item.get("parent_requirement_ids") or []),
            derivation_kind=item.get("derivation_kind", ""),
            derivation_proof=item.get("derivation_proof", ""),
            original_intent=item.get("original_intent", ""),
        ))
    return Goal(statement=c["inputs"]["raw_query"], raw_query=c["inputs"]["raw_query"], requirements=requirements)


# ------------------------------------------------------------------ authority mutations


def authority_mutations(c: dict):
    """Yield (name, inputs, expected_authoritative, semantic addition)."""
    parent = c["inputs"]["analytical_requirements"][0]["id"]
    addition_filter = [dict(op="COMPARE", field="strikes", comparator="=", value=0)]
    addition_group = ["p_throws"]
    addition_limit = (c["raw"]["limit"] or 0) + 1
    addition_qual = dict(basis="PITCHES", minimum=37)

    def base(value):
        return dict(id="authority_probe", kind="CONSTRAINT", value=value,
                    statement="Proposed analytical refinement", required=True, source="inferred",
                    original_intent="Execution-planning inference", references=[])

    yield ("unproved_inferred_filter", base(dict(filters=addition_filter)), False, "filters")
    yield ("unproved_inferred_grouping", base(dict(group_by=addition_group)), False, "group_by")
    yield ("unproved_inferred_limit", base(dict(limit=addition_limit)), False, "limit")
    yield ("unproved_inferred_qualification", base(dict(qualification=addition_qual)), False, "qualification")
    forged = {**base(dict(filters=addition_filter)), "source": "inferred",
              "parent_requirement_ids": ["does_not_exist"], "derivation_kind": "schema_field_binding",
              "derivation_proof": "forged"}
    yield ("fake_parent", forged, False, "filters")
    unrelated = {**base(dict(filters=addition_filter)), "source": "inferred",
                 "parent_requirement_ids": [parent], "derivation_kind": "not_a_governed_derivation",
                 "derivation_proof": "unproven transformation"}
    yield ("ungoverned_derivation", unrelated, False, "filters")
    planner = {**base(dict(filters=addition_filter)), "source": "planner"}
    yield ("planner_hint", planner, False, "filters")
    tool = {**base(dict(group_by=addition_group)), "source": "tool"}
    yield ("tool_proposal", tool, False, "group_by")
    valid = {**base(dict(filters=addition_filter)), "source": "inferred",
             "parent_requirement_ids": [parent], "derivation_kind": "schema_field_binding",
             "derivation_proof": "user strike-count clause mapped to governed field"}
    # R4: the named parent does not contain this strike-count restriction. The
    # former positive control was precisely the metadata-only proof vulnerability.
    yield ("semantic_changing_named_derivation", valid, False, "filters")
    domain_value = dict(qualification=c["raw"]["qualification"] or addition_qual)
    domain_default = {**base(domain_value), "source": "inferred",
                      "derivation_kind": "domain_default", "derivation_proof": "full_eligible_row_population"}
    yield ("contradictory_domain_default_payload", domain_default, False, "qualification")


# ------------------------------------------------------------------ clause coverage


SOURCE_CLAUSE_VARIANTS = (
    ("during extra innings", "supported_filter", "inning"),
    ("from the eighth inning onward", "supported_filter", "inning"),
    ("against southpaws", "supported_filter", "p_throws"),
    ("on the road", "unsupported", None),
    ("during doubleheaders", "unsupported", None),
    ("in day games", "unsupported", None),
    ("in night games", "unsupported", None),
)


def coverage_mutations(c: dict, rng: random.Random):
    variant = SOURCE_CLAUSE_VARIANTS[rng.randrange(len(SOURCE_CLAUSE_VARIANTS))]
    return variant


def deletion_mutations(c: dict, rng: random.Random):
    """Yield (name, mutated_inputs, mutated_raw) each deleting one mandatory clause."""
    base = copy.deepcopy(c["inputs"])
    raw = copy.deepcopy(c["raw"])
    # original requirement last entry carries role/group/filters/...
    req = base["analytical_requirements"][-1]
    value = copy.deepcopy(req["value"] or {})
    role = value.get("analytical_role")
    yield ("delete_role", {**base, "analytical_requirements": base["analytical_requirements"][:-1]},
           {**raw, "analytical_role": None})
    # remove season requirement but keep IR season => must mismatch/block
    yield ("delete_season", {**base, "analytical_requirements": [r for r in base["analytical_requirements"] if r["kind"] != "SEASON"]},
           raw)
    yield ("delete_time", {**base, "analytical_requirements": [r for r in base["analytical_requirements"] if r["kind"] != "TIME"]},
           raw)
    yield ("delete_population", {**base, "analytical_requirements": [r for r in base["analytical_requirements"] if r["kind"] != "GAME_POPULATION"]},
           raw)
    # remove a mandatory measure
    measures = [r for r in base["analytical_requirements"] if r["kind"] == "MEASURE"]
    if measures:
        yield ("delete_measure", {**base, "analytical_requirements": [r for r in base["analytical_requirements"] if r is not measures[-1]]},
               raw)
    if value.get("group_by"):
        pruned = {k: v for k, v in value.items() if k != "group_by"}
        yield ("delete_grouping", {**base, "analytical_requirements": base["analytical_requirements"][:-1] + [{**req, "value": pruned}]},
               {**raw, "group_by": []})
    if value.get("filters"):
        pruned = {k: v for k, v in value.items() if k != "filters"}
        yield ("delete_population_filter", {**base, "analytical_requirements": base["analytical_requirements"][:-1] + [{**req, "value": pruned}]},
               {**raw, "filters": []})
    if value.get("qualification"):
        pruned = {k: v for k, v in value.items() if k != "qualification"}
        yield ("delete_qualification", {**base, "analytical_requirements": base["analytical_requirements"][:-1] + [{**req, "value": pruned}]},
               {**raw, "qualification": None})
    if value.get("limit"):
        pruned = {k: v for k, v in value.items() if k not in {"limit", "ordering"}}
        yield ("delete_limit_and_ranking", {**base, "analytical_requirements": base["analytical_requirements"][:-1] + [{**req, "value": pruned}]},
               {**raw, "limit": None, "sort": []})
