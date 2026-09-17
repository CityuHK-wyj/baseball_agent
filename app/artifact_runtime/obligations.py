"""Frozen user obligations: the baseline COMPLETE must be proven against.

The Planner may interpret, decompose, re-route and add supporting work, but it may not
*unilaterally* decide that its own decomposition fully represents the user's Goal. This
module derives an immutable obligation set from the user message and the confirmed
semantic brief, and deterministically checks whether the Planner's accepted work actually
covers each obligation.

It is deliberately not a giant deterministic parser. The semantic layer supplies
``constraints``/``proposed_scope``; a small set of high-confidence lexical anchors (years,
explicit date windows, game-type words, explicit thresholds) acts only as a *safety net*
so a missing or malformed extraction cannot silently delete explicit user meaning.
"""

from __future__ import annotations

import re

from app.models.artifact_runtime import (Goal, Need, RuntimeArtifact, Scope, SemanticBrief,
                                         UserObligation)

_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_THRESHOLD = re.compile(
    r"(?:at\s+least|minimum\s+of|>=|≥|至少|不少于)\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
_UPPER_BOUND = re.compile(
    r"(?:at\s+most|no\s+more\s+than|<=|≤|最多|不超过)\s*(\d+(?:\.\d+)?)", re.IGNORECASE)

_GAME_TYPE_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("POSTSEASON", ("playoff", "postseason", "post-season", "world series", "季后赛",
                    "世界大赛")),
    ("REGULAR_SEASON", ("regular season", "常规赛", "regular-season")),
    ("SPRING_TRAINING", ("spring training", "春训")),
)

_POPULATION_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("batters", ("batter", "hitter", "position player", "打者", "击球员", "野手")),
    ("pitchers", ("pitcher", "starter", "reliever", "投手")),
    ("teams", ("team", "club", "球队", "队伍")),
)

_MEMBERSHIP_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("official_roster", ("official roster", "postseason roster", "季后赛名单", "正式名单",
                         "40-man", "40 人")),
    ("active_roster", ("active roster", "现役名单")),
    ("observed_participants", ("appeared", "played in", "出场", "参赛")),
)

_RANKING_TOKENS = ("highest", "lowest", "top", "most", "fewest", "best", "worst",
                   "rank", "排名", "最高", "最低", "最多", "最少", "排行")
_COMPARISON_TOKENS = ("compare", "compared", "versus", " vs ", "contrast", "相比",
                      "对比", "比较")
_EXPLANATION_TOKENS = ("why", "explain", "explanation", "reason", "because",
                       "为什么", "解释", "原因", "为何")
_METRIC_TOKENS = ("average", "avg", "mean", "rate", "percentage", "woba", "ops", "obp",
                  "slg", "era", "whip", "spin", "velocity", "velo", "exit velocity",
                  "launch angle", "平均", "率", "占比", "打击率")
_MEASURE_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("exit_velocity", ("exit velocity", "ev", "平均ev", "击球初速")),
    ("launch_angle", ("launch angle", "仰角")),
    ("pitch_velocity", ("pitch velocity", "球速", "投球速度")),
    ("spin_rate", ("spin rate", "转速")),
    ("woba", ("woba",)),
    ("ops", ("ops",)),
)


def _add(obligations: list[UserObligation], seen: set[str], *, kind: str, description: str,
         value: str = "", source_ref: str = "", origin: str = "USER_EXPLICIT") -> None:
    key = f"{kind}:{value}:{description}"
    if key in seen:
        return
    seen.add(key)
    obligations.append(UserObligation(
        obligation_id=f"obl-{len(obligations) + 1}", kind=kind, description=description,
        value=value, source_ref=source_ref, origin=origin))


def extract_obligations(*, message: str, brief: SemanticBrief, source_ref: str = "",
                        clarification_refs: tuple[str, ...] = ()) -> tuple[UserObligation, ...]:
    """Derive the frozen obligation baseline for one Goal revision."""
    obligations: list[UserObligation] = []
    seen: set[str] = set()
    text = message or ""
    folded = text.casefold()

    # 1. Semantic-brief constraints and proposed scope are authoritative where present.
    scope = brief.proposed_scope
    if scope is not None:
        if scope.time_range is not None:
            _add(obligations, seen, kind="TIME",
                 description=f"time window {scope.time_range.start}..{scope.time_range.end}",
                 value=f"{scope.time_range.start}..{scope.time_range.end}",
                 source_ref=source_ref)
        for season in scope.seasons:
            _add(obligations, seen, kind="SEASON", description=f"season {season}",
                 value=str(season), source_ref=source_ref)
        for game_type in scope.game_types:
            _add(obligations, seen, kind="GAME_TYPE", description=f"game type {game_type}",
                 value=game_type, source_ref=source_ref)
        if scope.metric:
            _add(obligations, seen, kind="METRIC", description=f"measure {scope.metric}",
                 value=scope.metric, source_ref=source_ref)
        if scope.membership_basis:
            _add(obligations, seen, kind="MEMBERSHIP",
                 description=f"membership basis {scope.membership_basis}",
                 value=scope.membership_basis, source_ref=source_ref)
        if scope.population:
            _add(obligations, seen, kind="POPULATION",
                 description=f"population {scope.population}", value=scope.population,
                 source_ref=source_ref)
        for entity in (*scope.entities, *scope.canonical_entities):
            _add(obligations, seen, kind="ENTITY", description=f"entity {entity}",
                 value=entity, source_ref=source_ref)
    for constraint in brief.constraints:
        _add(obligations, seen, kind="SEASON" if _YEAR.search(constraint) else "TIME",
             description=f"stated constraint: {constraint}", value=constraint.strip(),
             source_ref=source_ref)

    # 2. High-confidence lexical safety net: cannot be silently dropped by extraction.
    for year in _YEAR.findall(text):
        _add(obligations, seen, kind="SEASON", description=f"mentioned year {year}",
             value=year, source_ref=source_ref)
    for iso in _ISO_DATE.findall(text):
        _add(obligations, seen, kind="TIME", description=f"explicit date {iso}",
             value=iso, source_ref=source_ref)
    for canonical, tokens in _GAME_TYPE_TOKENS:
        if any(token in folded for token in tokens):
            _add(obligations, seen, kind="GAME_TYPE", description=f"game type {canonical}",
                 value=canonical, source_ref=source_ref)
    for canonical, tokens in _POPULATION_TOKENS:
        if any(token in folded for token in tokens):
            _add(obligations, seen, kind="POPULATION", description=f"population {canonical}",
                 value=canonical, source_ref=source_ref)
    for canonical, tokens in _MEMBERSHIP_TOKENS:
        if any(token in folded for token in tokens):
            _add(obligations, seen, kind="MEMBERSHIP",
                 description=f"membership {canonical}", value=canonical,
                 source_ref=source_ref)
    for match in _THRESHOLD.finditer(text):
        _add(obligations, seen, kind="QUALIFICATION",
             description=f"at least {match.group(1)} (minimum qualification)",
             value=f">={match.group(1)}", source_ref=source_ref)
    for match in _UPPER_BOUND.finditer(text):
        _add(obligations, seen, kind="QUALIFICATION",
             description=f"at most {match.group(1)} (maximum qualification)",
             value=f"<={match.group(1)}", source_ref=source_ref)
    if any(token in folded for token in _RANKING_TOKENS):
        _add(obligations, seen, kind="RANKING", description="ordinal ranking requested",
             value="rank", source_ref=source_ref)
    if any(token in folded for token in _COMPARISON_TOKENS):
        _add(obligations, seen, kind="COMPARISON", description="explicit comparison",
             value="comparison", source_ref=source_ref)
    if any(token in folded for token in _EXPLANATION_TOKENS):
        _add(obligations, seen, kind="EXPLANATION", description="explanation requested",
             value="explanation", source_ref=source_ref)
    if any(token in folded for token in _METRIC_TOKENS):
        measure = ""
        for canonical, tokens in _MEASURE_TOKENS:
            if any(token in folded for token in tokens):
                measure = canonical
                break
        _add(obligations, seen, kind="METRIC",
             description=f"measurement requested ({measure or 'unspecified measure'})",
             value=measure, source_ref=source_ref)
    for entity in brief.entities:
        _add(obligations, seen, kind="ENTITY", description=f"entity {entity}",
             value=entity, source_ref=source_ref)
    for ref in clarification_refs:
        _add(obligations, seen, kind="CLAIM_TYPE",
             description="requirement confirmed by the user in clarification",
             value=ref, source_ref=ref, origin="USER_CONFIRMED")
    return tuple(obligations)


# ---------------------------------------------------------------------------
# Deterministic coverage of frozen obligations by accepted Planner work
# ---------------------------------------------------------------------------


def _reflected(obligation: UserObligation, need: Need,
               artifacts: dict[str, RuntimeArtifact]) -> bool:
    scope = need.required_scope
    value = (obligation.value or "").strip()
    if obligation.kind == "SEASON":
        if scope is not None and value.isdigit() and int(value) in {
                int(item) for item in scope.seasons if str(item).isdigit()}:
            return True
        if need.parameters.get("season") and str(need.parameters["season"]) == value:
            return True
        return any(artifact.actual_scope is not None
                   and value.isdigit()
                   and int(value) in {int(item) for item in artifact.actual_scope.seasons
                                      if str(item).isdigit()}
                   for artifact in (artifacts.get(a) for a in need.linked_artifacts)
                   if artifact is not None)
    if obligation.kind == "TIME":
        if scope is not None and scope.time_range is not None:
            return True
        if need.parameters.get("start") and need.parameters.get("end"):
            return True
        return False
    if obligation.kind == "GAME_TYPE":
        if scope is not None and scope.game_types:
            return True
        query = need.parameters.get("analytical_query")
        if isinstance(query, dict):
            for condition in query.get("filters", ()) or ():
                if isinstance(condition, dict) and condition.get("field") == "game_type":
                    return True
        return False
    if obligation.kind == "MEMBERSHIP":
        if scope is not None and scope.membership_basis:
            return True
        return any(
            artifact is not None and artifact.actual_scope is not None
            and artifact.actual_scope.membership_basis
            for artifact in (artifacts.get(a) for a in need.linked_artifacts))
    if obligation.kind == "POPULATION":
        if scope is not None and scope.population:
            return True
        return any(
            artifact is not None and artifact.actual_scope is not None
            and artifact.actual_scope.population
            for artifact in (artifacts.get(a) for a in need.linked_artifacts))
    if obligation.kind == "ENTITY":
        if scope is not None and (scope.entities or scope.canonical_entities):
            return True
        return any(
            artifact is not None and artifact.actual_scope is not None
            and (artifact.actual_scope.entities or artifact.actual_scope.canonical_entities)
            for artifact in (artifacts.get(a) for a in need.linked_artifacts))
    if obligation.kind == "METRIC":
        if scope is not None and scope.metric:
            return True
        if need.parameters.get("metric"):
            return True
        if any(item in ("DERIVED_MEASURE",) for item in need.preferred_capabilities):
            return True
        if need.proposed_capability == "compute":
            return True
        return any(
            artifact is not None and artifact.actual_scope is not None and artifact.actual_scope.metric
            for artifact in (artifacts.get(a) for a in need.linked_artifacts))
    if obligation.kind == "QUALIFICATION":
        if need.parameters.get("min_rows") is not None:
            return True
        if need.parameters.get("qualification"):
            return True
        query = need.parameters.get("analytical_query")
        if isinstance(query, dict) and query.get("min_rows") is not None:
            return True
        if scope is not None and scope.qualification:
            return True
        return False
    if obligation.kind == "RANKING":
        if "RANKED_ENTITY_SET" in need.preferred_capabilities:
            return True
        if need.parameters.get("order_by") or need.parameters.get("limit"):
            return True
        query = need.parameters.get("analytical_query")
        if isinstance(query, dict) and (query.get("order_by") or query.get("limit")):
            return True
        return any(artifact is not None and artifact.export("RANKED_ENTITY_SET") is not None
                   for artifact in (artifacts.get(a) for a in need.linked_artifacts))
    if obligation.kind == "COMPARISON":
        if any("DIFF" in str(item) or "RATIO" in str(item)
               for item in need.parameters.get("ops", ()) if isinstance(item, str)):
            return True
        query = need.parameters.get("analytical_query")
        if isinstance(query, dict):
            periods = query.get("periods") or []
            if len(periods) >= 2:
                return True
            for selection in query.get("selections", ()) or ():
                expr = (selection or {}).get("expression") or {}
                if expr.get("op") in ("DIFF", "RATIO", "PCT"):
                    return True
        if need.proposed_capability == "compute":
            return True
        return False
    if obligation.kind == "EXPLANATION":
        if need.proposed_capability in ("web_research", "shared_knowledge",
                                        "evidence_entities"):
            return True
        return False
    if obligation.kind in ("CLAIM_TYPE", "GROUPING"):
        return bool(need.satisfies_obligations)
    return False


def obligation_coverage(goal: Goal, needs: tuple[Need, ...],
                        assessments: tuple, artifacts: tuple[RuntimeArtifact, ...]
                        ) -> dict[str, str]:
    """Return obligation_id -> coverage state (VERIFIED/PARTIAL/MISSING).

    Coverage is verified against accepted, SATISFIED evidence-bearing work. A Planner
    declaration alone (``satisfies_obligations``) is necessary but not sufficient: the
    deterministic ``_reflected`` check must also hold.
    """
    artifact_map = {item.artifact_id: item for item in artifacts}
    by_need = {item.need_id: item for item in assessments}
    result: dict[str, str] = {}
    for obligation in goal.obligations:
        covered = False
        partial = False
        for need in needs:
            assessment = by_need.get(need.need_id)
            if assessment is None or getattr(assessment, "verdict", None) != "SATISFIED":
                continue
            declared = obligation.obligation_id in need.satisfies_obligations
            reflected = _reflected(obligation, need, artifact_map)
            if reflected:
                # Deterministic coverage of the Planner's accepted work; not planner
                # self-certification. A declaration without reflected work is not enough.
                covered = True
                break
            if declared:
                # The Planner claimed coverage that the evidence does not show.
                partial = True
        result[obligation.obligation_id] = "VERIFIED" if covered else (
            "PARTIAL" if partial else "MISSING")
    return result


def obligation_gaps(goal: Goal, coverage: dict[str, str]) -> tuple[str, ...]:
    gaps: list[str] = []
    for obligation in goal.obligations:
        state = coverage.get(obligation.obligation_id, "MISSING")
        if state == "MISSING":
            gaps.append(f"user obligation not covered: {obligation.description}")
        elif state == "PARTIAL":
            gaps.append(f"user obligation only partially covered: {obligation.description}")
    return tuple(gaps)
