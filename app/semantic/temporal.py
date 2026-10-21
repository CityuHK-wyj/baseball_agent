"""Canonical temporal-scope resolution (M2.5).

Relative temporal expressions ("recent", "this season", "past few weeks", …) are resolved
**once** at Requirement normalization time against the Goal anchor date and then shared by
every downstream consumer. Individual capabilities must not re-interpret the same expression
independently.

Design rules:

- resolution is deterministic for a given ``(text, constraints, relation labels, anchor)``;
- explicit user dates/seasons/durations always override inferred domain defaults;
- defaults are centrally defined and inspectable here, never scattered across tools;
- if a relative expression cannot be confidently resolved, the scope is left ``UNRESOLVED``
  with the ambiguity recorded rather than inventing precision;
- the resolution is descriptive: it never chooses a capability.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Iterable

from app.contracts.models import Constraint, RelationKind, TemporalScope

# --------------------------------------------------------------------------- defaults
# One inspectable place for the domain default windows. A capability must not declare its
# own "30 days"; it should read the resolved Requirement scope instead.
DEFAULT_WINDOW_DAYS: dict[str, int] = {
    "INJURY": 30,
    "TRANSACTION": 30,
    "STATUS": 30,
    "PERFORMANCE": 30,
    "GAME": 14,
    "INTERVIEW": 90,
    "COMMUNITY": 180,
    "NARRATIVE": 90,
    "GENERIC": 30,
}

# Phrases whose window is fixed by calendar convention rather than a domain default.
_FIXED_RELATIVE_DAYS: tuple[tuple[str, int], ...] = (
    ("today", 1), ("right now", 1), ("现在", 1), ("今天", 1),
    ("this week", 7), ("本周", 7), ("这周", 7), ("這週", 7),
    ("this month", 30), ("本月", 30), ("这个月", 30), ("這個月", 30),
    ("last month", 30), ("past month", 30), ("上个月", 30), ("上個月", 30),
    ("past week", 7), ("last week", 7), ("上周", 7), ("上週", 7),
    ("past few weeks", 21), ("last few weeks", 21), ("recent weeks", 21),
    ("past few days", 7), ("last few days", 7), ("前几天", 7), ("前幾天", 7),
)

# Expressions that resolve from the information-class default.
_CLASS_RELATIVE_TERMS = (
    "recent", "recently", "latest", "lately", "current", "currently", "as of",
    "最近", "最新", "近期", "目前", "当前", "當前", "近来", "近來",
)

_THIS_SEASON = ("this season", "current season", "本赛季", "本賽季", "这个赛季", "這個賽季")
_LAST_SEASON = ("last season", "previous season", "上赛季", "上賽季", "前一个赛季", "前一個賽季")

_DURATION_RE = re.compile(
    r"(?:last|past|过去|過去|最近|近)\s*(\d+)\s*(day|days|week|weeks|month|months|天|日|周|週|个月|個月)",
    re.IGNORECASE,
)
_ABSOLUTE_DATE_RE = re.compile(r"\b((?:19|20)\d{2})-(\d{2})-(\d{2})\b")
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")

_DURATION_UNIT_DAYS = {
    "day": 1, "days": 1, "天": 1, "日": 1,
    "week": 7, "weeks": 7, "周": 7, "週": 7,
    "month": 30, "months": 30, "个月": 30, "個月": 30,
}

_INJURY_WORDS = ("injury", "injured", "roster", "status", "soreness", "伤病", "受傷", "受伤", "伤情", "阵容", "陣容")
_TRANSACTION_WORDS = ("trade", "traded", "signing", "signed", "contract", "交易", "签约", "簽約", "合同")


def information_class(relation_kinds: Iterable[RelationKind], text: str = "") -> str:
    """Classify the information environment for temporal-default selection."""
    kinds = set(relation_kinds)
    low = (text or "").lower()
    if kinds & {RelationKind.STATUS, RelationKind.FRESHNESS} or any(word in low for word in _INJURY_WORDS):
        return "INJURY"
    if any(word in low for word in _TRANSACTION_WORDS):
        return "TRANSACTION"
    if kinds & {RelationKind.QUOTE}:
        return "INTERVIEW"
    if kinds & {RelationKind.CAUSE, RelationKind.EFFECT, RelationKind.EXPLANATION}:
        return "NARRATIVE"
    if kinds & {RelationKind.VALUE, RelationKind.AGGREGATION, RelationKind.COMPARISON}:
        return "PERFORMANCE"
    if kinds & {RelationKind.EVENT}:
        return "GAME"
    return "GENERIC"


def default_window_days(information_class: str) -> int:
    return DEFAULT_WINDOW_DAYS.get(information_class, DEFAULT_WINDOW_DAYS["GENERIC"])


def _iso(value: dt.date) -> str:
    return value.isoformat()


def _anchor(anchor_date: dt.date | str | None) -> dt.date | None:
    if anchor_date is None:
        return None
    if isinstance(anchor_date, dt.datetime):
        return anchor_date.date()
    if isinstance(anchor_date, dt.date):
        return anchor_date
    try:
        return dt.date.fromisoformat(str(anchor_date)[:10])
    except ValueError:
        return None


def _season_window(year: int) -> tuple[str, str]:
    return f"{year}-03-01", f"{year}-11-30"


def _scope_from_constraints(constraints: Iterable[Constraint]) -> TemporalScope | None:
    for constraint in constraints:
        if constraint.kind == "season" and constraint.value is not None:
            try:
                year = int(constraint.value)
            except (TypeError, ValueError):
                continue
            return TemporalScope(
                kind="SEASON", original_text=str(constraint.value), season=year,
                information_class="GENERIC", confirmed=bool(constraint.explicit),
                note="explicit season constraint",
            )
        if constraint.kind == "date_range" and constraint.value:
            start, end = _parse_range(constraint.value)
            if start or end:
                return TemporalScope(
                    kind="RANGE", original_text=str(constraint.value), start=start, end=end,
                    confirmed=bool(constraint.explicit), note="explicit date-range constraint",
                )
    return None


def _parse_range(value: Any) -> tuple[str | None, str | None]:
    if isinstance(value, dict):
        start = value.get("start")
        end = value.get("end")
        return (_iso_date(start), _iso_date(end))
    text = str(value)
    matches = _ABSOLUTE_DATE_RE.findall(text)
    if len(matches) >= 2:
        return (f"{matches[0][0]}-{matches[0][1]}-{matches[0][2]}", f"{matches[1][0]}-{matches[1][1]}-{matches[1][2]}")
    if len(matches) == 1:
        only = f"{matches[0][0]}-{matches[0][1]}-{matches[0][2]}"
        return only, only
    return None, None


def _iso_date(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return str(value)


def resolve_temporal_scope(
    text: str,
    *,
    relation_kinds: Iterable[RelationKind] = (),
    constraints: Iterable[Constraint] = (),
    information_class_name: str | None = None,
    anchor_date: dt.date | str | None = None,
) -> TemporalScope:
    """Resolve the temporal scope of one Requirement deterministically."""
    kinds = list(relation_kinds)
    text = text or ""
    low = text.lower()
    info_class = information_class_name or information_class(kinds, text)

    explicit = _scope_from_constraints(constraints)
    if explicit is not None:
        explicit.information_class = info_class
        explicit.anchor_date = _iso(_anchor(anchor_date)) if _anchor(anchor_date) else None
        return explicit

    anchor = _anchor(anchor_date)
    absolute = _ABSOLUTE_DATE_RE.search(text)
    if absolute:
        return TemporalScope(
            kind="DATE", original_text=absolute.group(0), start=absolute.group(0), end=absolute.group(0),
            information_class=info_class, anchor_date=_iso(anchor) if anchor else None,
            confirmed=True, note="explicit absolute date",
        )
    year_match = _YEAR_RE.search(text)
    if year_match and not _is_duration_context(low, year_match):
        year = int(year_match.group(1))
        return TemporalScope(
            kind="SEASON", original_text=year_match.group(0), season=year,
            information_class=info_class, anchor_date=_iso(anchor) if anchor else None,
            confirmed=True, note="explicit year in the requirement",
        )

    # Explicit duration ("past 3 weeks", "最近 5 天").
    duration = _DURATION_RE.search(text)
    if duration:
        days = int(duration.group(1)) * _DURATION_UNIT_DAYS.get(duration.group(2).lower(), 1)
        if anchor is None:
            return TemporalScope(
                kind="UNRESOLVED", original_text=duration.group(0), window_days=days,
                information_class=info_class, confirmed=True,
                note="relative duration needs a Goal anchor date",
            )
        return TemporalScope(
            kind="RELATIVE", original_text=duration.group(0),
            start=_iso(anchor - dt.timedelta(days=days)), end=_iso(anchor),
            window_days=days, information_class=info_class, anchor_date=_iso(anchor),
            confirmed=True, note=f"explicit relative duration of {days} day(s)",
        )

    this_season = next((term for term in _THIS_SEASON if term in low), None)
    if this_season:
        if anchor is None:
            return TemporalScope(kind="UNRESOLVED", original_text=this_season, information_class=info_class, note="season needs a Goal anchor date")
        start, end = _season_window(anchor.year)
        return TemporalScope(
            kind="RANGE", original_text=this_season, start=start, end=end,
            information_class=info_class, anchor_date=_iso(anchor), confirmed=True,
            note=f"current {anchor.year} season",
        )
    last_season = next((term for term in _LAST_SEASON if term in low), None)
    if last_season:
        if anchor is None:
            return TemporalScope(kind="UNRESOLVED", original_text=last_season, information_class=info_class, note="season needs a Goal anchor date")
        start, end = _season_window(anchor.year - 1)
        return TemporalScope(
            kind="RANGE", original_text=last_season, start=start, end=end,
            information_class=info_class, anchor_date=_iso(anchor), confirmed=True,
            note=f"previous {anchor.year - 1} season",
        )

    fixed = next(((phrase, days) for phrase, days in _FIXED_RELATIVE_DAYS if phrase in low), None)
    relative_term = next((term for term in _CLASS_RELATIVE_TERMS if term in low), None)
    if fixed or relative_term:
        phrase = fixed[0] if fixed else relative_term
        days = fixed[1] if fixed else default_window_days(info_class)
        if anchor is None:
            return TemporalScope(
                kind="UNRESOLVED", original_text=phrase or "", window_days=days,
                information_class=info_class, note="relative expression needs a Goal anchor date",
            )
        return TemporalScope(
            kind="RELATIVE", original_text=phrase or "", start=_iso(anchor - dt.timedelta(days=days)), end=_iso(anchor),
            window_days=days, information_class=info_class, anchor_date=_iso(anchor),
            confirmed=bool(fixed), note=f"inferred {info_class} default of {days} day(s) for '{phrase}'",
        )

    return TemporalScope(
        kind="OPEN", original_text="", information_class=info_class,
        anchor_date=_iso(anchor) if anchor else None, note="no temporal expression",
    )


def _is_duration_context(low: str, match: re.Match[str]) -> bool:
    # A year adjacent to a duration marker ("past 2 years") is a duration, not a season.
    window = low[max(0, match.start() - 6): match.end() + 6]
    return any(marker in window for marker in ("past", "last", "过去", "過去", "最近"))


def scope_to_time_dict(scope: TemporalScope | None) -> dict[str, Any]:
    """Render a resolved scope into the ``time_scope`` dict shape used by propositions."""
    if scope is None:
        return {}
    if scope.kind == "SEASON" and scope.season is not None:
        return {"season": scope.season}
    if scope.is_window():
        return {
            "date_range": {"start": scope.start, "end": scope.end},
            "resolved_kind": scope.kind,
            "window_days": scope.window_days,
            "anchor_date": scope.anchor_date,
        }
    return {}


def merge_resolved_scope(base: dict[str, Any], scope: TemporalScope | None) -> dict[str, Any]:
    """Merge a resolved scope over an existing goal scope dict without overwriting explicit keys."""
    merged = dict(base or {})
    resolved = scope_to_time_dict(scope)
    for key, value in resolved.items():
        merged.setdefault(key, value)
    return merged
