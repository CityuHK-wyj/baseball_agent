"""Fresh finite population generator and orthogonal semantic mutations for M2.12R.

Seeds and reference reductions are evaluation-only. No production normalization is
used to calculate expected values. Legacy seeds/entities are deliberately excluded.
"""
from tools.analytical_requirement_fixture import fixture_inputs
import copy
import datetime as dt
import random

DEV_SEEDS = range(212310000, 212310048)

# Authored reference event sets, independent of production catalog/Measure recognition.
HITS = {"single", "double", "triple", "home_run"}
WALKS = {"walk", "intent_walk"}
STRIKEOUTS = {"strikeout", "strikeout_double_play"}


def case(seed):
    r = random.Random(seed)
    year = r.choice([2016, 2017, 2019, 2023, 2024, 2025])
    role = r.choice(["BATTER", "PITCHER"])
    identity = {"BATTER": "batter_id", "PITCHER": "pitcher_id"}[role]
    players = r.sample(range(950001, 1900000), 8)
    subject = players[0] if role == "BATTER" else players[4]
    start = dt.date(year, r.choice([4, 6, 8, 9]), r.randint(2, 11))
    end = start + dt.timedelta(days=r.randint(12, 23))
    population, codes = r.choice([("REGULAR_SEASON", ["R"]), ("DIVISION_SERIES", ["D"]),
                                 ("LEAGUE_CHAMPIONSHIP", ["L"]), ("POSTSEASON", ["F", "D", "L", "W"])])
    field, values = r.choice([("pitch_type", ["SI", "CH", "FC", "ST"]), ("strikes", [0, 1, 2]),
                              ("stand", ["L", "R"]), ("description", ["ball", "foul", "called_strike"]),
                              ("p_throws", ["R", "L"])])
    selected = r.sample(values, r.randint(1, min(2, len(values))))
    filters = [dict(op="IN", field=field, values=selected)] if seed % 4 else []
    if seed % 3 == 0:
        filters.append(dict(op="BETWEEN", field="inning", low=2, high=r.choice([7, 8, 9])))
    metric = r.choice(["walks", "strikeouts", "hard_hit_pct", "bb_pct", "k_pct", "avg_release_speed", "exit_velocity", "launch_angle", "hits"])
    aggregates = [dict(name="sample", function="COUNT_NON_NULL", field="events"),
                  dict(name="h", function="COUNT_IF", condition=dict(op="IN", field="events", values=sorted(HITS))),
                  dict(name="bb", function="COUNT_IF", condition=dict(op="IN", field="events", values=sorted(WALKS))),
                  dict(name="k", function="COUNT_IF", condition=dict(op="IN", field="events", values=sorted(STRIKEOUTS))),
                  dict(name="hh", function="COUNT_IF", condition=dict(op="COMPARE", field="launch_speed", comparator=">=", value=95)),
                  dict(name="bbe", function="COUNT_NON_NULL", field="launch_speed"),
                  dict(name="speed", function="AVG", field="release_speed"),
                  dict(name="ev", function="AVG", field="launch_speed"),
                  dict(name="angle", function="AVG", field="launch_angle")]
    derived = [dict(name="bb_rate", op="PCT", left=dict(kind="aggregate", name="bb"), right=dict(kind="aggregate", name="sample")),
               dict(name="k_rate", op="PCT", left=dict(kind="aggregate", name="k"), right=dict(kind="aggregate", name="sample")),
               dict(name="hh_rate", op="PCT", left=dict(kind="aggregate", name="hh"), right=dict(kind="aggregate", name="bbe"))]
    column = {"walks": "bb", "strikeouts": "k", "hard_hit_pct": "hh_rate", "bb_pct": "bb_rate", "k_pct": "k_rate",
              "avg_release_speed": "speed", "exit_velocity": "ev", "launch_angle": "angle", "hits": "h"}[metric]
    shape = seed % 6
    individual = shape in {0, 1}
    rank = shape in {3, 4}
    split = r.choice(["stand", "pitch_type", "strikes", "p_throws"])
    grouping = [identity] if rank else [split] if shape in {1, 5} else []
    ordering = [dict(name=column, direction=r.choice(["ASC", "DESC"])), dict(name=identity, direction="ASC")] if rank else [dict(name=split, direction="ASC")] if grouping else []
    limit = r.randint(2, 4) if rank else None
    q = dict(basis=r.choice(["PA", "BBE", "PITCHES", "GAMES"]), minimum=r.randint(0, 2)) if seed % 2 else None
    raw = dict(relation="pitch", analytical_role=role, entity_filters=[dict(role=role, ids=[subject])] if individual else [],
               season=year, population=population, date_range=dict(start=start.isoformat(), end=end.isoformat()),
               filters=filters, group_by=grouping, aggregates=aggregates, derived=derived, sort=ordering, limit=limit, qualification=q)
    requested_ordering = [dict(metric=metric, direction=ordering[0]["direction"])] if rank else [dict(field=split, direction="ASC")] if grouping else None
    if rank and seed % 2:
        requested_ordering.append(dict(field=identity, direction="ASC"))
    inputs = dict(intent=dict(objective="Calculate the governed metrics for the bound row population", season=year, population=population,
                              analytical_role=role, date_range=raw["date_range"], qualification=q),
                  entity_role=role, require_entities=individual, entity_sets=[dict(role=role, ids=[str(subject)])] if individual else [],
                  requested_measures=[dict(key=metric), dict(key="pa")],
                  constraints=dict(filters=copy.deepcopy(filters), group_by=grouping, ordering=requested_ordering, limit=limit))
    rows = []
    for j in range(480):
        rows.append(dict(batter_id=r.choice(players[:4]), pitcher_id=r.choice(players[4:]), game_date=start+dt.timedelta(days=r.randint(-2, (end-start).days+2)),
                         game_type=r.choice(codes) if j % 5 else "S", events=r.choice([None, "single", "double", "triple", "walk", "intent_walk", "strikeout", "strikeout_double_play", "field_out"]),
                         pitch_type=r.choice(["SI", "CH", "FC", "ST", "FF"]), description=r.choice(["ball", "foul", "called_strike", "hit_into_play"]),
                         stand=r.choice(["L", "R", None]), p_throws=r.choice(["L", "R", None]), strikes=r.randint(0, 2), inning=r.randint(1, 10),
                         release_speed=r.choice([None, 82.25, 91., 96.5]), launch_speed=r.choice([None, 88., 95., 103.]),
                         launch_angle=r.choice([None, -4., 12., 29.]), plate_z=r.choice([1.2, 2.3, 3.1]), game_pk=j//8))
    inputs = fixture_inputs(inputs)
    return dict(seed=seed, raw=raw, inputs=inputs, rows=rows, identity=identity, subject=subject, metric=metric, metric_column=column, codes=codes)


def mutations(c):
    """Each record identifies one semantic dimension and a valid finite IR mutation."""
    out = []
    def edit(name, dimension, change, expected="MISMATCH"):
        q = copy.deepcopy(c["raw"]); change(q)
        out.append(dict(name=name, dimension=dimension, ir=q, expected=expected, syntactically_valid=True))
    edit("unexpected_row_restriction", "row_population", lambda q: q["filters"].append(dict(op="COMPARE", field="plate_z", comparator=">", value=2.7)))
    if c["raw"]["filters"]:
        edit("missing_row_predicate", "row_population", lambda q: q["filters"].pop(0))
        def value_change(q):
            p = q["filters"][0]
            if p["op"] == "BETWEEN": p["high"] += 1
            else: p["values"] = [next(v for v in {"pitch_type": ["SI", "CH", "FC", "ST", "FF"], "strikes": [0, 1, 2], "stand": ["L", "R"], "p_throws": ["L", "R"], "description": ["ball", "foul", "called_strike", "hit_into_play"]}[p["field"]] if v not in p["values"])] if len(p["values"]) < len({"stand": [0, 1], "p_throws": [0, 1]}.get(p["field"], [0, 1, 2])) else [p["values"][0]]
        edit("changed_predicate_value", "row_population", value_change)
        def operator_change(q):
            p=q["filters"][0];q["filters"][0]=dict(op="COMPARE", field=p["field"], comparator="!=", value=p.get("values", [p.get("low")])[0])
        edit("changed_predicate_operator", "row_population", operator_change)
        edit("redundant_equivalent_filter", "equivalence", lambda q: q["filters"].append(copy.deepcopy(q["filters"][0])), "MATCH")
        edit("reordered_predicates", "equivalence", lambda q: q["filters"].reverse(), "MATCH")
        def default_forms(q):
            for p in q["filters"]:
                for key in ("value", "values", "low", "high", "conditions", "comparator"):
                    p.setdefault(key, None)
                if p["op"] == "IN": p["values"] = list(reversed(p["values"]))
        edit("explicit_defaults_and_in_order", "equivalence", default_forms, "MATCH")
        def global_to_local(q):
            p = q["filters"].pop(0)
            q["aggregates"][1]["condition"] = dict(op="AND", conditions=[q["aggregates"][1]["condition"], p])
        edit("global_predicate_into_one_measure", "predicate_ownership", global_to_local)
    def local_to_global(q):
        p = q["aggregates"][2].pop("condition")
        q["aggregates"][2].update(function="COUNT", field="*")
        q["filters"].append(p)
    edit("measure_local_predicate_into_population", "predicate_ownership", local_to_global)
    edit("role_swap", "analytical_role", lambda q: q.update(analytical_role="PITCHER" if q["analytical_role"] == "BATTER" else "BATTER"))
    if not c["raw"]["entity_filters"] and not c["raw"]["group_by"]:
        edit("missing_analytical_role", "analytical_role", lambda q: q.update(analytical_role=None))
    if c["raw"]["entity_filters"]:
        edit("wrong_individual_entity", "entity", lambda q: q["entity_filters"][0].update(ids=[c["subject"]+31]))
        edit("missing_individual_entity", "entity", lambda q: q.update(entity_filters=[]))
    edit("competition_expansion", "competition", lambda q: q.update(game_types=["S"]))
    edit("temporal_drift", "time", lambda q: q["date_range"].update(end=(dt.date.fromisoformat(q["date_range"]["end"])+dt.timedelta(days=1)).isoformat()))
    if c["raw"]["qualification"]:
        edit("qualification_omission", "qualification", lambda q: q.update(qualification=None))
        edit("qualification_threshold", "qualification", lambda q: q["qualification"].update(minimum=q["qualification"]["minimum"]+1))
    def laundering(q):
        column=c["metric_column"]
        q["derived"]=[d for d in q["derived"] if d["name"] != column]
        for a in q["aggregates"]:
            if a["name"] == column: a.update(function="SUM", field="release_speed", condition=None); break
        else: q["aggregates"].append(dict(name=column, function="SUM", field="release_speed"))
    edit("measure_laundering", "measure", laundering)
    if c["raw"]["sort"] and c["raw"]["limit"]:
        edit("ranking_metric_drift", "primary_ordering", lambda q: q["sort"][0].update(name="sample"))
        edit("tie_break_only", "tie_ordering", lambda q: q["sort"][1].update(direction="DESC"), "MISMATCH" if len(c["inputs"]["constraints"]["ordering"]) > 1 else "MATCH")
        edit("limit_drift", "limit", lambda q: q.update(limit=q["limit"]+1))
        def grouping_role(q):
            other="pitcher_id" if c["identity"] == "batter_id" else "batter_id"
            q["group_by"]=[other];q["sort"][1]["name"]=other
        edit("grouping_role_drift", "grouping", grouping_role)
    elif c["raw"]["group_by"]:
        def split_change(q):
            other="stand" if q["group_by"][0] != "stand" else "strikes"
            q["group_by"]=[other];q["sort"][0]["name"]=other
        edit("split_dimension_drift", "grouping", split_change)
        edit("unexpected_group_limit", "limit", lambda q: q.update(limit=1))
    else:
        edit("unexpected_grouping", "grouping", lambda q: q.update(group_by=["pitch_type"]))
    def competition_encoding(q):
        q.update(population=None, game_types=list(reversed(c["codes"])))
    edit("equivalent_competition_encoding", "equivalence", competition_encoding, "MATCH")
    if c["raw"]["entity_filters"]:
        edit("canonical_numeric_id_string", "equivalence", lambda q: q["entity_filters"][0].update(ids=[str(c["subject"])]), "MATCH")
    return out


def accepts(row, p):
    """Independent finite evaluator for the generator's requested predicates."""
    v=row[p["field"]]
    if p["op"] == "IN": return v is not None and v in p["values"]
    if p["op"] == "BETWEEN": return v is not None and p["low"] <= v <= p["high"]
    raise AssertionError("reference generator introduced an unauthored predicate")


def reference(c):
    q=c["raw"];groups={}
    for row in c["rows"]:
        if row["game_type"] not in c["codes"] or not q["date_range"]["start"] <= row["game_date"].isoformat() <= q["date_range"]["end"]: continue
        if q["entity_filters"] and row[c["identity"]] != c["subject"]: continue
        if not all(accepts(row,p) for p in q["filters"]): continue
        key=tuple(row[k] for k in q["group_by"]);groups.setdefault(key,[]).append(row)
    if not groups and not q["group_by"]: groups[()]=[]
    result=[]
    for key,rows in groups.items():
        pa=sum(x["events"] is not None for x in rows);bbe=sum(x["launch_speed"] is not None for x in rows)
        counts={"PA":pa,"BBE":bbe,"PITCHES":len(rows),"GAMES":len({x["game_pk"] for x in rows})}
        if q["qualification"] and counts[q["qualification"]["basis"]] < q["qualification"]["minimum"]: continue
        bb=sum(x["events"] in WALKS for x in rows);k=sum(x["events"] in STRIKEOUTS for x in rows)
        hh=sum(x["launch_speed"] is not None and x["launch_speed"] >= 95 for x in rows)
        def avg(field):
            values=[x[field] for x in rows if x[field] is not None]
            return sum(values)/len(values) if values else None
        row=dict(zip(q["group_by"],key));row.update(sample=pa,h=sum(x["events"] in HITS for x in rows) if rows else None,
                                                   bb=bb if rows else None,k=k if rows else None,hh=hh if rows else None,bbe=bbe,
                                                   speed=avg("release_speed"),ev=avg("launch_speed"),angle=avg("launch_angle"),
                                                   bb_rate=100*bb/pa if pa else None,k_rate=100*k/pa if pa else None,hh_rate=100*hh/bbe if bbe else None)
        result.append(row)
    for order in reversed(q["sort"]):
        name=order["name"];nonnull=[x for x in result if x[name] is not None];null=[x for x in result if x[name] is None]
        result=sorted(nonnull,key=lambda x:x[name],reverse=order["direction"] == "DESC")+null
    return result[:q["limit"]] if q["limit"] else result
