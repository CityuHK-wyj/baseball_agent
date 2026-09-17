"""General temporal and field resolution helpers.

These are language-level utilities, not query-specific rules. ``parse_time_window``
understands common relative windows ("last 30 days", "过去 3 个月") and explicit
ISO/season forms so explicit user time scope is preserved for planning. Nothing here
branches on a known dogfooding query.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from app.models.contracts import TimeRange

_ISO = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")
_RELATIVE = re.compile(
    r"(?:last|past|previous|recent|最近|近|过去|過去|前)\s*(\d+)\s*"
    r"(day|days|d|week|weeks|w|month|months|mo|year|years|y|"
    r"天|日|周|週|个?月|個月|年)",
    re.IGNORECASE)
_YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)")

_UNIT_DAYS = {
    "day": 1, "days": 1, "d": 1, "天": 1, "日": 1,
    "week": 7, "weeks": 7, "w": 7, "周": 7, "週": 7,
    "month": 30, "months": 30, "mo": 30, "月": 30, "个月": 30, "個月": 30,
    "year": 365, "years": 365, "y": 365, "年": 365,
}


def parse_time_window(text: str, today: date | None = None) -> TimeRange | None:
    """Return an explicit or relative window, or ``None`` when the text has no window."""
    if not text or not text.strip():
        return None
    today = today or date.today()
    match = _RELATIVE.search(text)
    if match:
        count = int(match.group(1))
        unit = match.group(2).casefold()
        days = _UNIT_DAYS.get(unit)
        if days and count > 0:
            start = today - timedelta(days=count * days - 1)
            return TimeRange(start=start, end=today)
    dates = _ISO.findall(text)
    if len(dates) >= 2:
        try:
            return TimeRange(start=date.fromisoformat(dates[0]),
                             end=date.fromisoformat(dates[1]))
        except ValueError:
            return None
    if dates:
        try:
            day = date.fromisoformat(dates[0])
        except ValueError:
            return None
        return TimeRange(start=day, end=day)
    year = _YEAR.search(text)
    if year:
        season = int(year.group(1))
        return TimeRange(start=date(season, 1, 1), end=date(season, 12, 31))
    return None


def parse_season(text: str) -> int | None:
    if not text:
        return None
    match = _YEAR.search(text)
    return int(match.group(1)) if match else None
