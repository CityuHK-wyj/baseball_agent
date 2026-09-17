"""Freshness policy: how long a knowledge statement stays trustworthy.

Different categories age at different rates. Official rules change on a season or an
official-change trigger; community creators must be rechecked every 30-90 days. The
policy is declarative metadata, not an LLM judgement.
"""

from datetime import date, datetime, timezone

from app.models.knowledge import KnowledgeItem, KnowledgeSource, FreshnessPolicy

# Maximum age in days before a category is considered stale.
MAX_AGE_DAYS: dict[str, int] = {
    "OFFICIAL_RULES": 400,   # a season / official-change triggered
    "CBA": 400,              # agreement / rule-change triggered
    "SEASONAL": 200,         # season structure, once per season
    "ANNUAL": 400,           # teams, divisions, ballparks, awards
    "PERIODIC": 180,         # source directory, roster conventions
    "FREQUENT": 14,          # anything identity-like that drifts
    "LOW": 730,              # glossary, historical context
    "COMMUNITY": 90,         # creator activity status
    "STATIC": 3650,          # physical dimensions, league history
}

# Lower rank sorts first. 0 fresh, 1 due soon, 2 stale, 3 never verified.
_FRESH = 0
_DUE_SOON = 1
_STALE = 2
_UNKNOWN = 3


def max_age_days(policy: FreshnessPolicy | str) -> int:
    return MAX_AGE_DAYS.get(str(policy), MAX_AGE_DAYS["PERIODIC"])


def _as_datetime(value: date | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)


def age_days(item: KnowledgeItem | KnowledgeSource, today: date) -> int | None:
    stamp = _as_datetime(getattr(item, "last_verified_at", None) or getattr(item, "last_checked", None))
    if stamp is None:
        return None
    anchor = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    return max(0, (anchor - stamp).days)


def is_stale(item: KnowledgeItem | KnowledgeSource, today: date) -> bool:
    age = age_days(item, today)
    if age is None:
        return True
    return age > max_age_days(item.freshness_policy)


def freshness_rank(item: KnowledgeItem, today: date) -> int:
    """Ordering rank for retrieval: 0 fresh, 1 due soon, 2 stale, 3 never verified."""
    age = age_days(item, today)
    if age is None:
        return _UNKNOWN
    limit = max_age_days(item.freshness_policy)
    if age > limit:
        return _STALE
    if age > limit * 4 // 5:
        return _DUE_SOON
    return _FRESH
