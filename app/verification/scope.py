"""Scope comparison: requested vs actual. A broader window never satisfies a narrower one."""

from __future__ import annotations

from app.contracts.models import Scope, ScopeGap


def compare_scope(requested: Scope | None, actual: Scope | None) -> list[ScopeGap]:
    if requested is None or requested.is_empty():
        return []
    if actual is None:
        return [ScopeGap(channel="scope", requested="declared", actual="none", material=True)]
    gaps: list[ScopeGap] = []

    if requested.season is not None and actual.season is not None and requested.season != actual.season:
        gaps.append(ScopeGap(channel="season", requested=str(requested.season), actual=str(actual.season)))
    if requested.game_population and actual.game_population and requested.game_population != actual.game_population:
        gaps.append(
            ScopeGap(channel="population", requested=requested.game_population, actual=actual.game_population)
        )
    if requested.date_range and actual.date_range:
        # Any mismatch is a gap: a broader window changes rate statistics just as a
        # narrower one does, so it must not silently satisfy the request.
        if requested.date_range.start != actual.date_range.start:
            gaps.append(
                ScopeGap(channel="time", requested=str(requested.date_range.start), actual=str(actual.date_range.start))
            )
        if requested.date_range.end != actual.date_range.end:
            gaps.append(
                ScopeGap(channel="time", requested=str(requested.date_range.end), actual=str(actual.date_range.end))
            )
    if requested.entities and actual.entities:
        missing = [e for e in requested.entities if e not in actual.entities]
        if missing:
            gaps.append(ScopeGap(channel="entity", requested=",".join(missing), actual=",".join(actual.entities)))
    if requested.measures:
        actual_measures = set(actual.measures or [])
        missing = [m for m in requested.measures if m not in actual_measures]
        if missing:
            gaps.append(
                ScopeGap(channel="measure", requested=",".join(missing),
                         actual=",".join(sorted(actual_measures)) or "none")
            )
    return gaps


def binding_scope_conflicts(
    requested: Scope | None, actual: Scope | None
) -> list[tuple[str, str, str]]:
    """Strict compatibility for binding: only report a conflict on a channel where both
    sides carry a concrete value and those values disagree.

    Unlike ``compare_scope`` (sufficiency), an absent actual channel is not a conflict
    here: a DERIVED_MEASURE or WEB_EVIDENCE artifact may legitimately carry a partial
    scope. A concrete mismatch always is.
    """
    if requested is None or actual is None:
        return []
    conflicts: list[tuple[str, str, str]] = []
    if requested.season is not None and actual.season is not None and requested.season != actual.season:
        conflicts.append(("season", str(requested.season), str(actual.season)))
    if requested.game_population and actual.game_population and requested.game_population != actual.game_population:
        conflicts.append(("population", requested.game_population, actual.game_population))
    if requested.date_range and actual.date_range:
        if requested.date_range.start != actual.date_range.start:
            conflicts.append(("time", str(requested.date_range.start), str(actual.date_range.start)))
        if requested.date_range.end != actual.date_range.end:
            conflicts.append(("time", str(requested.date_range.end), str(actual.date_range.end)))
    if requested.entities and actual.entities:
        missing = [e for e in requested.entities if e not in actual.entities]
        if missing:
            conflicts.append(("entity", ",".join(missing), ",".join(actual.entities)))
    if requested.measures:
        actual_measures = set(actual.measures or [])
        if actual_measures:
            missing = [m for m in requested.measures if m not in actual_measures]
            if missing:
                conflicts.append(("measure", ",".join(missing), ",".join(sorted(actual_measures))))
    return conflicts
