"""Canonical metric packs: deterministic Safe IR for standard batting/pitching lines.

Ad-hoc analysis still goes through the LLM IR builder. This module only covers the
canonical "overall batting/pitching line" shape, where a fixed, catalog-validated set of
aggregates is exactly what the user asked for. It removes model sampling variance from the
most common class of questions and keeps canonical metric definitions in one place.
"""

from __future__ import annotations

from typing import Any

from app.capabilities.analytics.ir import (
    Aggregate,
    AnalyticalQuery,
    Condition,
    Derived,
    EntityFilter,
    Operand,
    Qualification,
)

GENERAL_TERMS = (
    "batting", "pitching", "performance", "overall", "stats", "statistics", "line",
    "打击", "投球", "表现", "数据", "成绩", "整体", "怎么样",
)
SPECIFIC_TERMS = (
    "split", "versus", "vs ", "against left", "against right", "left-handed", "right-handed",
    "pitch type", "fastball", "slider", "curveball", "exit velocity", "launch angle",
    "recent", "last ", "since", "before", "after", "difference", "compare", "rank", "top ",
    "threshold", "at least", "minimum", "hard-hit", "whiff", "zone", "inning", "month",
    "左右", "分项", "球种", "面对", "最近", "排名", "前", "至least", "至少", "比较", "差异",
    "硬击球", "挥空", "好球带", "局", "月份", "上下", "前后",
)


def is_general_performance(intent: dict[str, Any]) -> bool:
    text = f"{intent.get('measure') or ''} {intent.get('objective') or ''}".lower()
    if any(term in text for term in SPECIFIC_TERMS):
        return False
    return any(term in text for term in GENERAL_TERMS)


def build_canonical_query(intent: dict[str, Any]) -> AnalyticalQuery | None:
    entities = intent.get("entities") or []
    if not entities:
        return None
    if any(str(e.get("role", "")).upper() not in {"BATTER", "PITCHER"} for e in entities):
        return None
    entity_filters = [
        EntityFilter(role=str(e.get("role", "BATTER")).upper(), ids=[int(i) for i in e.get("ids", [])], names=list(e.get("names", [])))
        for e in entities
        if e.get("ids")
    ]
    if not entity_filters:
        return None

    bat = _condition("IN", "events", values=["single", "double", "triple", "home_run"])
    double = _condition("COMPARE", "events", comparator="=", value="double")
    triple = _condition("COMPARE", "events", comparator="=", value="triple")
    home_run = _condition("COMPARE", "events", comparator="=", value="home_run")
    single = _condition("COMPARE", "events", comparator="=", value="single")
    walk = _condition("IN", "events", values=["walk", "intent_walk"])
    hbp = _condition("COMPARE", "events", comparator="=", value="hit_by_pitch")
    sac_fly = _condition("COMPARE", "events", comparator="=", value="sac_fly")
    at_bat = Condition(
        op="AND",
        conditions=[
            Condition(op="IS_NOT_NULL", field="events"),
            Condition(
                op="NOT",
                conditions=[
                    Condition(
                        op="IN",
                        field="events",
                        values=[
                            "walk", "intent_walk", "hit_by_pitch", "catcher_interf", "sac_fly",
                            "sac_bunt", "sac_fly_double_play", "sac_bunt_double_play",
                            "truncated_pa", "game_advisory",
                        ],
                    )
                ],
            ),
        ],
    )

    aggregates = [
        Aggregate(name="pa", function="COUNT_NON_NULL", field="events"),
        Aggregate(name="hits", function="COUNT_IF", condition=bat),
        Aggregate(name="singles", function="COUNT_IF", condition=single),
        Aggregate(name="doubles", function="COUNT_IF", condition=double),
        Aggregate(name="triples", function="COUNT_IF", condition=triple),
        Aggregate(name="home_runs", function="COUNT_IF", condition=home_run),
        Aggregate(name="walks", function="COUNT_IF", condition=walk),
        Aggregate(name="hbp", function="COUNT_IF", condition=hbp),
        Aggregate(name="sac_flies", function="COUNT_IF", condition=sac_fly),
        Aggregate(name="strikeouts", function="COUNT_IF", condition=_condition("IN", "events", values=["strikeout", "strikeout_double_play"])),
        Aggregate(name="at_bats", function="COUNT_IF", condition=at_bat),
    ]

    role = entity_filters[0].role
    if role == "PITCHER":
        aggregates.append(Aggregate(name="pitches", function="COUNT", field="*"))
        aggregates.append(Aggregate(name="avg_release_speed", function="AVG", field="release_speed"))
        aggregates.append(Aggregate(name="avg_spin_rate", function="AVG", field="release_spin_rate"))

    # Build total bases explicitly with numeric literals to keep the expression tree simple.
    derived = [
        Derived(
            name="tb_hr",
            op="MUL",
            left=Operand(kind="aggregate", name="home_runs"),
            right=Operand(kind="literal", value=4),
        ),
        Derived(
            name="tb_triples",
            op="MUL",
            left=Operand(kind="aggregate", name="triples"),
            right=Operand(kind="literal", value=3),
        ),
        Derived(
            name="tb_doubles",
            op="MUL",
            left=Operand(kind="aggregate", name="doubles"),
            right=Operand(kind="literal", value=2),
        ),
        Derived(
            name="tb_mid",
            op="ADD",
            left=Operand(kind="derived", name="tb_doubles"),
            right=Operand(kind="derived", name="tb_triples"),
        ),
        Derived(
            name="tb_extra",
            op="ADD",
            left=Operand(kind="derived", name="tb_mid"),
            right=Operand(kind="derived", name="tb_hr"),
        ),
        Derived(
            name="tb",
            op="ADD",
            left=Operand(kind="aggregate", name="singles"),
            right=Operand(kind="derived", name="tb_extra"),
        ),
        Derived(
            name="avg",
            op="SAFE_DIV",
            left=Operand(kind="aggregate", name="hits"),
            right=Operand(kind="aggregate", name="at_bats"),
        ),
        Derived(
            name="obp_num",
            op="ADD",
            left=Operand(kind="aggregate", name="walks"),
            right=Operand(kind="aggregate", name="hbp"),
        ),
        Derived(
            name="obp_den",
            op="ADD",
            left=Operand(kind="aggregate", name="at_bats"),
            right=Operand(kind="derived", name="obp_num"),
        ),
        Derived(
            name="obp_den_full",
            op="ADD",
            left=Operand(kind="derived", name="obp_den"),
            right=Operand(kind="aggregate", name="sac_flies"),
        ),
        Derived(
            name="obp_top",
            op="ADD",
            left=Operand(kind="aggregate", name="hits"),
            right=Operand(kind="derived", name="obp_num"),
        ),
        Derived(
            name="obp",
            op="SAFE_DIV",
            left=Operand(kind="derived", name="obp_top"),
            right=Operand(kind="derived", name="obp_den_full"),
        ),
        Derived(
            name="slg",
            op="SAFE_DIV",
            left=Operand(kind="derived", name="tb"),
            right=Operand(kind="aggregate", name="at_bats"),
        ),
        Derived(
            name="ops",
            op="ADD",
            left=Operand(kind="derived", name="obp"),
            right=Operand(kind="derived", name="slg"),
        ),
        Derived(
            name="k_pct",
            op="PCT",
            left=Operand(kind="aggregate", name="strikeouts"),
            right=Operand(kind="aggregate", name="pa"),
        ),
        Derived(
            name="bb_pct",
            op="PCT",
            left=Operand(kind="aggregate", name="walks"),
            right=Operand(kind="aggregate", name="pa"),
        ),
    ]

    query = AnalyticalQuery(
        relation="pitch",
        analytical_role=intent.get("analytical_role") or role,
        entity_filters=entity_filters,
        population=intent.get("population") or None,
        season=intent.get("season") or None,
        # Only the independently bound contract, not Planner for_comparison or
        # entity-set cardinality, can authorize output grouping.
        group_by=list((intent.get('bound_obligations') or {}).get('group_by') or []),
        aggregates=aggregates,
        derived=derived,
        intent_summary=f"Canonical {role.lower()} line: {intent.get('objective') or intent.get('measure') or ''}".strip(),
    )
    if intent.get("date_range"):
        from app.capabilities.analytics.ir import DateWindow

        date_range = intent["date_range"]
        if isinstance(date_range, dict):
            query.date_range = DateWindow(start=date_range.get("start"), end=date_range.get("end"))
    qualification = intent.get("qualification")
    if isinstance(qualification, dict) and qualification.get("minimum") is not None:
        basis = str(qualification.get("basis") or ("BBE" if role == "PITCHER" else "PA")).upper()
        query.qualification = Qualification(basis=basis, minimum=int(qualification["minimum"]))
    return query


def _condition(op: str, field: str, *, comparator: str | None = None, value: Any = None, values: list[Any] | None = None) -> Condition:
    return Condition(op=op, field=field, comparator=comparator, value=value, values=values)
