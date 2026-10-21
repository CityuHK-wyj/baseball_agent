"""Typed per-source search outcomes (M1-T2, ADR 0027 / SPEC §13.3).

A provider that is blocked, rate-limited, timing out or erroring must be distinguishable
from a provider that genuinely returned no results. The mapping is deterministic and
transport-oriented, so a dead provider can never be reported as ``NO_RESULTS``.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any


class SourceOutcome(str, Enum):
    """The typed result of one provider call."""

    OK = "OK"
    EMPTY = "EMPTY"
    RATE_LIMITED = "RATE_LIMITED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    BLOCKED = "BLOCKED"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    return "timeout" in type(exc).__name__.lower()


def _classify_text(text: str, default: SourceOutcome) -> SourceOutcome:
    lowered = (text or "").lower()
    if re.search(r"\b402\b", lowered) or "quota" in lowered or "insufficient credits" in lowered:
        return SourceOutcome.QUOTA_EXCEEDED
    if re.search(r"\b429\b", lowered) or "too many requests" in lowered or "rate limit" in lowered:
        return SourceOutcome.RATE_LIMITED
    if re.search(r"\b401\b", lowered) or "unauthorized" in lowered:
        return SourceOutcome.AUTH_REQUIRED
    if (
        re.search(r"\b40[13]\b", lowered)
        or "forbidden" in lowered
        or "blocked" in lowered
        or "captcha" in lowered
    ):
        return SourceOutcome.BLOCKED
    if re.search(r"\b408\b", lowered) or "timeout" in lowered or "timed out" in lowered:
        return SourceOutcome.TIMEOUT
    return default


def classify_search_result(
    *,
    hits: list[Any] | None = None,
    error: str = "",
    raised: BaseException | None = None,
) -> SourceOutcome:
    """Classify one provider call from its hits, ``last_error`` text or raised exception."""
    hits = hits or []
    if hits:
        return SourceOutcome.OK
    if raised is not None:
        return _classify_text(f"{type(raised).__name__}: {raised}", SourceOutcome.TIMEOUT if _is_timeout(raised) else SourceOutcome.ERROR)
    if not error:
        return SourceOutcome.EMPTY
    return _classify_text(error, SourceOutcome.ERROR)
