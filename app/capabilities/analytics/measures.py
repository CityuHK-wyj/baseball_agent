"""Measure semantics: the single authority for what an analytical measure *is*.

This module owns the mapping between a user-facing measure (Chinese/English text), a
canonical measure key, its calculation definition, and whether it is derivable from the
pitch-level SchemaCatalog. It is a *definitions and verification* layer over the catalog,
not a second schema: every base quantity it recognises is expressed with the real catalog
fields and event vocabulary.

It is used by:
- the semantic layer, to turn a requested measure into a typed UserRequirement value;
- ``local_analytics``, to declare what an executed query actually computed from its
  validated Safe IR (never from the LLM's prose);
- the Judge, to verify that an accepted result covers the requested measure.

Nothing here trusts a SQL column name or an LLM description by itself: a measure is only
attested when its definition can be structurally recognized from the validated IR.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from app.capabilities.analytics.catalog import (
    HIT_EVENTS,
    NON_AT_BAT_EVENTS,
)
from app.capabilities.analytics.ir import Aggregate, AnalyticalQuery, Condition


# --------------------------------------------------------------------------- specs


@dataclass(frozen=True)
class MeasureSpec:
    key: str
    label: str
    definition: str
    aliases: tuple[str, ...] = ()
    derivable: bool = True
    note: str = ""


_MEASURE_LIST: list[MeasureSpec] = [
    # ---- derivable batting quantities ----------------------------------------
    MeasureSpec("pa", "Plate appearances (PA)", "COUNT(events) or COUNT_NON_NULL(events)",
                ("打席", "打席数", "plate appearances", "plate appearance", "pa")),
    MeasureSpec("at_bats", "At-bats (AB)",
                "COUNT_IF(events IS NOT NULL AND events NOT IN non-at-bat events)",
                ("打数", "at bats", "at-bats", "atbat", "ab")),
    MeasureSpec("hits", "Hits (H)", "COUNT_IF(events IN single,double,triple,home_run)",
                ("安打", "安打数", "hits", "base hits")),
    MeasureSpec("singles", "Singles (1B)", "COUNT_IF(events = single)",
                ("一垒安打", "singles")),
    MeasureSpec("doubles", "Doubles (2B)", "COUNT_IF(events = double)",
                ("二垒安打", "doubles")),
    MeasureSpec("triples", "Triples (3B)", "COUNT_IF(events = triple)",
                ("三垒安打", "triples")),
    MeasureSpec("home_runs", "Home runs (HR)", "COUNT_IF(events = home_run)",
                ("本垒打", "全垒打", "本垒打数", "home run", "home runs", "homer", "homers", "hr")),
    MeasureSpec("walks", "Walks (BB)", "COUNT_IF(events IN walk,intent_walk)",
                ("保送", "四坏球", "walks", "walk", "base on balls", "bb")),
    MeasureSpec("hbp", "Hit by pitch (HBP)", "COUNT_IF(events = hit_by_pitch)",
                ("触身球", "触身", "hit by pitch", "hbp")),
    MeasureSpec("sac_flies", "Sacrifice flies (SF)", "COUNT_IF(events = sac_fly)",
                ("高飞牺牲打", "牺牲飞球", "sacrifice fly", "sac flies", "sf")),
    MeasureSpec("strikeouts", "Strikeouts (K)",
                "COUNT_IF(events IN strikeout,strikeout_double_play)",
                ("三振", "被三振", "strikeout", "strikeouts", "strike out", "k")),
    MeasureSpec("tb", "Total bases (TB)", "1B + 2*2B + 3*3B + 4*HR",
                ("垒打数", "total bases", "tb")),
    MeasureSpec("avg", "Batting average (AVG)", "H / AB",
                ("打击率", "击球率", "batting average", "avg", "ba", "被打击率")),
    MeasureSpec("obp", "On-base percentage (OBP)",
                "(H + BB + HBP) / (AB + BB + HBP + SF)",
                ("上垒率", "被上垒率", "on-base percentage", "on base percentage", "obp")),
    MeasureSpec("slg", "Slugging percentage (SLG)", "TB / AB",
                ("长打率", "slugging", "slugging percentage", "slg")),
    MeasureSpec("ops", "On-base plus slugging (OPS)", "OBP + SLG",
                ("攻击指数", "整体攻击指数", "进攻指数", "on-base plus slugging", "ops")),
    MeasureSpec("k_pct", "Strikeout rate (K%)", "100 * K / PA",
                ("三振率", "strikeout rate", "k rate", "k%")),
    MeasureSpec("bb_pct", "Walk rate (BB%)", "100 * BB / PA",
                ("保送率", "walk rate", "bb rate", "bb%")),
    MeasureSpec("hard_hit_pct", "Hard-hit rate", "100 * COUNT_IF(launch_speed >= 95) / COUNT_NON_NULL(launch_speed)",
                ("硬击球率", "强击球率", "hard-hit rate", "hard hit rate", "hardhit", "hard-hit%")),
    # ---- derivable pitching/contact measurements ------------------------------
    MeasureSpec("pitches", "Pitches thrown", "COUNT(*)",
                ("投球数", "球数", "pitch count", "pitches", "pitches thrown")),
    MeasureSpec("avg_release_speed", "Average release speed", "AVG(release_speed)",
                ("平均球速", "均速", "出手速度", "球速", "release speed", "average velocity",
                 "avg velocity", "velocity", "mph")),
    MeasureSpec("avg_spin_rate", "Average spin rate", "AVG(release_spin_rate)",
                ("平均转速", "转速", "spin rate", "spin")),
    MeasureSpec("exit_velocity", "Average exit velocity", "AVG(launch_speed)",
                ("击球初速", "出球速度", "击球速度", "exit velocity", "exit velo", "ev")),
    MeasureSpec("launch_angle", "Average launch angle", "AVG(launch_angle)",
                ("击球仰角", "发射角", "launch angle")),
    # ---- explicitly NOT derivable from pitch-level Statcast -------------------
    MeasureSpec("era", "Earned run average (ERA)", "earned runs / innings pitched",
                ("era", "防御率", "自責分率", "自责分率", "earned run average"),
                derivable=False, note="earned runs and innings pitched are not in the pitch-level data"),
    MeasureSpec("whip", "WHIP", "(walks + hits) / innings pitched",
                ("whip",), derivable=False, note="innings pitched is not in the pitch-level data"),
    MeasureSpec("wins", "Wins (W)", "team win credited to the pitcher",
                ("胜投", "胜场", "wins"), derivable=False, note="game decisions are not in the pitch-level data"),
    MeasureSpec("losses", "Losses (L)", "team loss charged to the pitcher",
                ("败投", "败场", "losses"), derivable=False, note="game decisions are not in the pitch-level data"),
    MeasureSpec("saves", "Saves (SV)", "recorded save",
                ("救援成功", "救援", "saves", "save"), derivable=False, note="game decisions are not in the pitch-level data"),
    MeasureSpec("rbi", "Runs batted in (RBI)", "runs driven in by a plate appearance",
                ("打点", "打點", "rbi", "runs batted in"), derivable=False,
                note="RBIs are not encoded as a plate-appearance event in the data"),
    MeasureSpec("stolen_bases", "Stolen bases (SB)", "successful steal",
                ("盗垒", "盗壘", "偷垒", "stolen base", "stolen bases", "steals", "sb"), derivable=False,
                note="baserunning events are not in the pitch-level data"),
    MeasureSpec("caught_stealing", "Caught stealing (CS)", "runner thrown out stealing",
                ("盗垒失败", "caught stealing", "cs"), derivable=False,
                note="baserunning events are not in the pitch-level data"),
    MeasureSpec("innings_pitched", "Innings pitched (IP)", "outs recorded / 3",
                ("投球局数", "局数", "innings pitched", "innings"), derivable=False,
                note="outs recorded are not in the pitch-level data"),
    MeasureSpec("earned_runs", "Earned runs (ER)", "runs that are earned",
                ("自责分", "earned runs"), derivable=False,
                note="earned runs are not in the pitch-level data"),
    MeasureSpec("team_record", "Team win-loss record", "team wins and losses",
                ("战绩", "球队战绩", "胜率", "胜负场", "win-loss record", "team record", "standings"),
                derivable=False, note="the local sources have no team dimension or game results"),
]

MEASURES: dict[str, MeasureSpec] = {m.key: m for m in _MEASURE_LIST}


def measure(key: str) -> MeasureSpec | None:
    return MEASURES.get(key)


def is_derivable(key: str | None) -> bool:
    spec = MEASURES.get(key or "")
    return bool(spec and spec.derivable)


def _contract_form(symbol: str) -> str:
    return re.sub(r"[\s_\-]+", " ", str(symbol or "").strip().casefold())


def normalize_measure_key(symbol: str) -> str | None:
    """Canonical governed key for a typed-contract measure symbol, else None.

    This canonicalizes only the existing finite MEASURES vocabulary (keys,
    labels and governed aliases, insensitive to case/underscore/hyphen
    encoding of those same symbols). It adds no user-language patterns and
    resolves nothing outside the vocabulary.
    """
    form = _contract_form(symbol)
    if not form:
        return None
    for key, spec in MEASURES.items():
        forms = {key, spec.label, *spec.aliases}
        if any(_contract_form(candidate) == form for candidate in forms if str(candidate).strip()):
            return key
    return None


# --------------------------------------------------------------------------- resolve


@dataclass
class MeasureResolution:
    text: str
    key: str | None
    label: str
    derivable: bool
    matched: bool
    note: str = ""

    def requirement_value(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "derivable": self.derivable,
                "matched": self.matched, "note": self.note, "requested_text": self.text}


_ASCII_ALIAS: dict[str, str] = {}
_CJK_ALIAS: dict[str, str] = {}
for _spec in _MEASURE_LIST:
    for _alias in _spec.aliases:
        if _alias.strip() and all(ord(ch) < 128 for ch in _alias):
            _ASCII_ALIAS[_alias.lower()] = _spec.key
        else:
            _CJK_ALIAS[_alias] = _spec.key


def _ascii_pattern(alias: str) -> re.Pattern[str]:
    escaped = re.escape(alias)
    if re.fullmatch(r"[a-z0-9][a-z0-9%\- ]*", alias):
        return re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", re.IGNORECASE)
    return re.compile(escaped, re.IGNORECASE)


def resolve_text(text: str) -> list[MeasureResolution]:
    """Resolve every non-overlapping measure mention in ``text``.

    Longer aliases win over shorter ones, so ``保送率`` resolves to ``bb_pct`` and not to
    ``walks``. ASCII aliases require word boundaries to avoid matching inside words. A
    non-string value (a malformed planner constraint) is coerced or ignored, never a crash.
    """
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    matches: list[tuple[int, int, str, str]] = []  # start, end, key, alias
    for alias, key in _ASCII_ALIAS.items():
        for m in _ascii_pattern(alias).finditer(text):
            matches.append((m.start(), m.end(), key, alias))
    lowered = text.lower()
    for alias, key in _CJK_ALIAS.items():
        start = lowered.find(alias.lower())
        while start != -1:
            matches.append((start, start + len(alias), key, alias))
            start = lowered.find(alias.lower(), start + 1)
    matches.sort(key=lambda item: (-(item[1] - item[0]), item[0]))
    selected: list[tuple[int, int, str, str]] = []
    for candidate in matches:
        start, end, _, _ = candidate
        if any(not (end <= s or start >= e) for s, e, _, _ in selected):
            continue
        selected.append(candidate)
    selected.sort(key=lambda item: item[0])
    resolutions: list[MeasureResolution] = []
    seen: set[str] = set()
    for start, end, key, alias in selected:
        if key in seen:
            continue
        seen.add(key)
        spec = MEASURES[key]
        resolutions.append(
            MeasureResolution(
                text=alias,
                key=key,
                label=spec.label,
                derivable=spec.derivable,
                matched=True,
                note=spec.note,
            )
        )
    return resolutions


def resolve_texts(texts: Iterable[str]) -> list[MeasureResolution]:
    merged: list[MeasureResolution] = []
    seen: set[str] = set()
    for text in texts:
        for resolution in resolve_text(text):
            if resolution.key in seen:
                continue
            seen.add(resolution.key)
            merged.append(resolution)
    return merged


# ------------------------------------------------------------------ IR structural tags

_SINGLE_EVENT_TAGS = {
    "single": "event:single",
    "double": "event:double",
    "triple": "event:triple",
    "home_run": "event:home_run",
    "walk": "event:unintentional_walk",
    "intent_walk": "event:intentional_walk",
    "hit_by_pitch": "event:hbp",
    "sac_fly": "event:sac_fly",
    "strikeout": "event:single_strikeout",
    "strikeout_double_play": "event:double_play_strikeout",
    "catcher_interf": "event:catcher_interf",
}

_HIT_EVENT_SET = frozenset(HIT_EVENTS)
_NON_AT_BAT_SET = frozenset(NON_AT_BAT_EVENTS)


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def condition_tag(condition: Condition | None) -> str | None:
    """Return a stable semantic tag for a condition, or None if it is not recognized."""
    if condition is None:
        return None
    if condition.op == "IN" and condition.field == "events":
        values = frozenset(str(v) for v in (condition.values or []))
        if values == _HIT_EVENT_SET:
            return "event:hits"
        if values == frozenset({"walk", "intent_walk"}):
            return "event:walk"
        if values == frozenset({"strikeout", "strikeout_double_play"}):
            return "event:strikeout"
        if len(values) == 1:
            return _SINGLE_EVENT_TAGS.get(next(iter(values)))
        return None
    if condition.op == "COMPARE" and condition.field == "events" and condition.comparator == "=":
        return _SINGLE_EVENT_TAGS.get(str(condition.value))
    if condition.op == "IS_NOT_NULL" and condition.field == "events":
        return "event:pa"
    if condition.op == "IS_NULL" and condition.field == "events":
        return "event:no_pa"
    if (
        condition.op == "COMPARE"
        and condition.field == "launch_speed"
        and condition.comparator == ">="
        and _num(condition.value) == 95
    ):
        return "hard_hit"
    if condition.op == "AND":
        children = list(condition.conditions or [])
        if _is_at_bat(children):
            return "event:at_bats"
        tags = [condition_tag(child) for child in children]
        if tags and all(tag == tags[0] for tag in tags) and tags[0]:
            return tags[0]
        return None
    if condition.op == "OR":
        children = list(condition.conditions or [])
        tags = [condition_tag(child) for child in children]
        if tags and all(tag == tags[0] for tag in tags) and tags[0]:
            return tags[0]
        return None
    if condition.op == "NOT":
        children = list(condition.conditions or [])
        if len(children) == 1 and children[0].op == "IN" and children[0].field == "events":
            values = frozenset(str(v) for v in (children[0].values or []))
            if values == _NON_AT_BAT_SET:
                return "not:non_at_bat"
        return None
    return None


def _is_at_bat(children: list[Condition]) -> bool:
    if len(children) != 2:
        return False
    tags = [condition_tag(child) for child in children]
    return "event:pa" in tags and "not:non_at_bat" in tags


def aggregate_tag(agg: Aggregate) -> str:
    if agg.function == "COUNT":
        return "count:*" if agg.field in (None, "*") else f"count:{agg.field}"
    if agg.function == "COUNT_NON_NULL":
        return f"count:{agg.field}"
    if agg.function == "COUNT_DISTINCT":
        return f"count_distinct:{agg.field}"
    if agg.function == "COUNT_IF":
        tag = condition_tag(agg.condition)
        return tag or "if:unrecognized"
    if agg.function in {"AVG", "SUM", "MIN", "MAX", "ANY_VALUE"}:
        return f"{agg.function.lower()}:{agg.field}"
    return f"{agg.function.lower()}:{agg.field}"


_AGG_KEY = {
    "event:hits": "hits",
    "event:single": "singles",
    "event:double": "doubles",
    "event:triple": "triples",
    "event:home_run": "home_runs",
    "event:walk": "walks",
    "event:hbp": "hbp",
    "event:sac_fly": "sac_flies",
    "event:strikeout": "strikeouts",
    "event:at_bats": "at_bats",
    "event:pa": "pa",
    "count:events": "pa",
    "count:*": "pitches",
    "avg:release_speed": "avg_release_speed",
    "avg:release_spin_rate": "avg_spin_rate",
    "avg:launch_speed": "exit_velocity",
    "avg:launch_angle": "launch_angle",
}


# --------------------------------------------------------------- derived recognition

Signature = tuple


def _operand_sig(operand, agg_tags: dict[str, str], derived_sigs: dict[str, Signature]) -> Signature:
    if operand.kind == "aggregate":
        return ("agg", agg_tags.get(operand.name or "", "unknown"))
    if operand.kind == "derived":
        return derived_sigs.get(operand.name or "", ("unknown", operand.name))
    if operand.kind == "literal":
        value = _num(operand.value)
        return ("lit", value if value is not None else operand.value)
    if operand.kind == "field":
        return ("field", operand.name)
    return ("unknown", None)


def build_signatures(query: AnalyticalQuery) -> tuple[dict[str, str], dict[str, Signature]]:
    agg_tags: dict[str, str] = {agg.name: aggregate_tag(agg) for agg in query.aggregates}
    derived_sigs: dict[str, Signature] = {}
    by_name = {d.name: d for d in query.derived}
    resolving: set[str] = set()

    def resolve(name: str) -> Signature:
        if name in derived_sigs:
            return derived_sigs[name]
        if name in resolving:
            return ("cycle", name)
        resolving.add(name)
        derived = by_name[name]
        left = _operand_sig(derived.left, agg_tags, _LazySignatures(resolve))
        right = _operand_sig(derived.right, agg_tags, _LazySignatures(resolve))
        sig = (_op_name(derived.op), left, right)
        derived_sigs[name] = sig
        resolving.discard(name)
        return sig

    for derived in query.derived:
        resolve(derived.name)
    return agg_tags, derived_sigs


class _LazySignatures(dict):
    def __init__(self, resolver):
        super().__init__()
        self._resolver = resolver

    def __contains__(self, key):  # type: ignore[override]
        return True

    def get(self, key, default=None):  # type: ignore[override]
        return self._resolver(key)


def _op_name(op: str) -> str:
    return {
        "ADD": "add", "SUB": "sub", "MUL": "mul",
        "DIV": "div", "SAFE_DIV": "div", "RATE": "div", "PCT": "pct", "DIFF": "sub",
    }.get(op, op.lower())


def _linear(sig: Signature | None) -> tuple[float, dict[str, float]] | None:
    """Return (constant, {tag: coefficient}) for a +/-/scalar* expression."""
    if not sig:
        return None
    op = sig[0]
    if op == "agg":
        return (0.0, {sig[1]: 1.0})
    if op == "lit":
        value = _num(sig[1])
        return (value, {}) if value is not None else None
    if op == "add" and len(sig) == 3:
        left = _linear(sig[1])
        right = _linear(sig[2])
        if left is None or right is None:
            return None
        coeffs = dict(left[1])
        for tag, value in right[1].items():
            coeffs[tag] = coeffs.get(tag, 0.0) + value
        return (left[0] + right[0], {k: v for k, v in coeffs.items() if abs(v) > 1e-9})
    if op == "sub" and len(sig) == 3:
        left = _linear(sig[1])
        right = _linear(sig[2])
        if left is None or right is None:
            return None
        coeffs = dict(left[1])
        for tag, value in right[1].items():
            coeffs[tag] = coeffs.get(tag, 0.0) - value
        return (left[0] + right[0], {k: v for k, v in coeffs.items() if abs(v) > 1e-9})
    if op == "mul" and len(sig) == 3:
        left = _linear(sig[1])
        right = _linear(sig[2])
        if left is None or right is None:
            return None
        if not left[1] and abs(left[0]) > 0:
            return (right[0] * left[0], {k: v * left[0] for k, v in right[1].items()})
        if not right[1] and abs(right[0]) > 0:
            return (left[0] * right[0], {k: v * right[0] for k, v in left[1].items()})
        return None
    return None


def _ratio(sig: Signature) -> tuple[Signature, Signature, float] | None:
    if not sig:
        return None
    if sig[0] == "div" and len(sig) == 3:
        return (sig[1], sig[2], 1.0)
    if sig[0] == "pct" and len(sig) == 3:
        return (sig[1], sig[2], 100.0)
    if sig[0] == "mul" and len(sig) == 3:
        left, right = sig[1], sig[2]
        for scale_sig, other in ((left, right), (right, left)):
            if scale_sig[0] == "lit" and abs((_num(scale_sig[1]) or 0) - 100.0) < 1e-6:
                inner = _ratio(other)
                if inner:
                    return (inner[0], inner[1], 100.0)
    return None


def _is_pa_tag(tag: str) -> bool:
    return tag in {"event:pa", "count:events"}


def _coeffs_equal(coeffs: dict[str, float], expected: dict[str, float]) -> bool:
    if set(coeffs) != set(expected):
        return False
    return all(abs(coeffs[key] - expected[key]) < 1e-6 for key in expected)


def recognize_signature(sig: Signature, memo: dict[Signature, str | None] | None = None) -> str | None:
    """Recognize a canonical measure key from a derived expression signature."""
    memo = memo if memo is not None else {}
    if sig in memo:
        return memo[sig]
    memo[sig] = None  # guard against recursion
    result = _recognize(sig, memo)
    memo[sig] = result
    return result


def _recognize(sig: Signature, memo: dict[Signature, str | None]) -> str | None:
    if not sig:
        return None
    if sig[0] == "agg":
        return _AGG_KEY.get(sig[1])

    ratio = _ratio(sig)
    if ratio is not None:
        num, den, factor = ratio
        if num[0] == "agg" and den[0] == "agg":
            if factor == 1 and num[1] == "event:hits" and den[1] == "event:at_bats":
                return "avg"
            if factor == 100 and _is_pa_tag(den[1]):
                if num[1] == "event:strikeout":
                    return "k_pct"
                if num[1] == "event:walk":
                    return "bb_pct"
            if factor == 100 and num[1] == "hard_hit" and den[1] == "count:launch_speed":
                return "hard_hit_pct"
        if factor == 1 and den[0] == "agg" and den[1] == "event:at_bats":
            num_linear = _linear(num)
            if num_linear and num_linear[0] == 0 and num_linear[1]:
                if _coeffs_equal(num_linear[1], {
                    "event:single": 1.0, "event:double": 2.0,
                    "event:triple": 3.0, "event:home_run": 4.0,
                }):
                    return "slg"
        if factor == 1 and num[0] == "add" and den[0] == "add":
            num_linear = _linear(num)
            den_linear = _linear(den)
            if num_linear and den_linear and num_linear[0] == 0 and den_linear[0] == 0:
                if _coeffs_equal(num_linear[1], {"event:hits": 1.0, "event:walk": 1.0, "event:hbp": 1.0}) and \
                        _coeffs_equal(den_linear[1], {"event:at_bats": 1.0, "event:walk": 1.0,
                                                       "event:hbp": 1.0, "event:sac_fly": 1.0}):
                    return "obp"

    if sig[0] == "add" and len(sig) == 3:
        left_key = recognize_signature(sig[1], memo)
        right_key = recognize_signature(sig[2], memo)
        if {left_key, right_key} == {"obp", "slg"}:
            return "ops"

    linear = _linear(sig)
    if linear and linear[0] == 0 and linear[1] and _coeffs_equal(linear[1], {
        "event:single": 1.0, "event:double": 2.0,
        "event:triple": 3.0, "event:home_run": 4.0,
    }):
        return "tb"
    return None


@dataclass
class DeclaredMeasure:
    column: str
    key: str | None
    kind: str                 # aggregate | derived | dimension | enrichment
    verified: bool
    source: str               # canonical_pack | ir_structure | normalized
    definition: str
    signature: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "key": self.key,
            "kind": self.kind,
            "verified": self.verified,
            "source": self.source,
            "definition": self.definition,
            "signature": self.signature,
        }


def _sig_text(sig: Signature) -> str:
    if not sig:
        return ""
    op = sig[0]
    if op in {"agg", "field", "lit"}:
        return f"{op}:{sig[1]}"
    parts = []
    for part in sig[1:]:
        parts.append(_sig_text(part) if isinstance(part, tuple) else str(part))
    return op + "(" + ", ".join(parts) + ")"


def declare_measures(query: AnalyticalQuery, *, ir_origin: str = "llm",
                     normalized: list[str] | None = None) -> list[DeclaredMeasure]:
    """Turn a validated Safe IR into the measures it actually computes.

    ``verified`` is True only when the definition is structurally recognized. A column
    name alone never attests a measure.
    """
    agg_tags, derived_sigs = build_signatures(query)
    declared: list[DeclaredMeasure] = []
    canonical = ir_origin == "canonical_metric_pack"

    for name, tag in agg_tags.items():
        key = _AGG_KEY.get(tag)
        declared.append(
            DeclaredMeasure(
                column=name,
                key=key,
                kind="aggregate",
                verified=bool(key),
                source="canonical_pack" if (canonical and key) else "ir_structure",
                definition=tag,
                signature=tag,
            )
        )
    for name, sig in derived_sigs.items():
        key = recognize_signature(sig)
        verified = bool(key)
        source = "ir_structure"
        if canonical and key:
            source = "canonical_pack"
        declared.append(
            DeclaredMeasure(
                column=name,
                key=key,
                kind="derived",
                verified=verified,
                source=source,
                definition=_sig_text(sig),
                signature=_sig_text(sig),
            )
        )
    for field in query.group_by:
        declared.append(
            DeclaredMeasure(column=field, key=None, kind="dimension", verified=False,
                            source="ir_structure", definition=f"GROUP BY {field}")
        )
    return declared


def declared_keys(measures: Iterable[dict[str, Any]], *, verified_only: bool = True) -> set[str]:
    keys: set[str] = set()
    for item in measures:
        if not isinstance(item, dict):
            continue
        if verified_only and not item.get("verified"):
            continue
        key = item.get("key")
        if key:
            keys.add(str(key))
    return keys
