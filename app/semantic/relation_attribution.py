"""Conservative, proposition-local attribution for directed cause/effect evidence.

This is a candidate gate, not a factual inference engine. It accepts only a causal
clause whose expressed arguments match the proposition's bound argument(s). Unknown
wording remains available as source material but cannot become verified support through
lexical overlap alone.
"""

from __future__ import annotations

import re
from typing import Any

from app.contracts.models import RelationKind, RelationSemantics

_GENERIC = {"the", "a", "an", "of", "for", "in", "on", "at", "to", "and", "or", "its",
            "event", "events", "season", "seasons", "year", "years", "mlb"}
_CLAUSE = re.compile(r"(?<=[.!?。！？;；])\s+|\n+")
_PATTERNS = (
    # Every pattern yields antecedent then consequence, independent of surface voice.
    re.compile(r"(?P<source>.+?)\s*,?\s+(?:caused|causes|causing|triggered|triggers|triggering|led to|leads to|leading to|resulted in|results in|resulting in|brought about)\s+(?P<target>.+)", re.I),
    re.compile(r"(?P<target>.+?)\s+(?:was|were|is|are)\s+(?:caused|triggered|brought about)\s+by\s+(?P<source>.+)", re.I),
    re.compile(r"(?P<target>.+?)\s+(?:because of|due to|resulting from|stemmed from)\s+(?P<source>.+)", re.I),
    re.compile(r"(?:as a result of|because of|due to|following)\s+(?P<source>.+?),\s*(?P<target>.+)", re.I),
    re.compile(r"(?P<target>.+?)\s+(?:because|since)\s+(?P<source>.+)", re.I),
)


def _tokens(value: str) -> set[str]:
    return {part for part in re.findall(r"[a-z][a-z0-9-]*|\d+", value.lower())
            if part not in _GENERIC and not part.isdigit()}


def _argument_matches(bound: str, expressed: str) -> bool:
    required = _tokens(bound)
    present = _tokens(expressed)
    return bool(required) and required <= present


def directed_support(proposition: Any, passage: str) -> bool:
    """Whether one local clause states the requested directed relationship.

    Unbound relations retain legacy handling. A bound cause/effect relation requires
    an explicit antecedent/consequence clause; two different clauses cannot donate
    arguments to one another.
    """
    relation = getattr(proposition, "relation_semantics", None)
    if isinstance(relation, dict):
        try:
            relation = RelationSemantics.model_validate(relation)
        except Exception:
            return False
    if relation is None or relation.relation_kind not in {RelationKind.CAUSE, RelationKind.EFFECT}:
        return True
    source, target = relation.relation_source, relation.relation_target
    if not source and not target:
        return True
    scope = getattr(proposition, "time_scope", {}) or {}
    season = scope.get("season") if isinstance(scope, dict) else None
    population = (getattr(proposition, "population", None) or "").lower().replace("_", " ")
    for clause in _CLAUSE.split(passage or ""):
        clause = clause.strip()
        # Scope must describe this causal fact; a year/population in a neighbouring
        # statement cannot be borrowed to complete it.
        if season is not None and not re.search(rf"(?<!\d){re.escape(str(season))}(?!\d)", clause):
            continue
        if population and population not in clause.lower():
            continue
        passive = bool(re.search(r"\b(?:caused|triggered|brought about)\s+by\b", clause, re.I))
        for index, pattern in enumerate(_PATTERNS):
            if passive and index == 0:
                continue
            match = pattern.search(clause)
            if match is None:
                continue
            if source and not _argument_matches(source, match.group("source")):
                continue
            if target and not _argument_matches(target, match.group("target")):
                continue
            return True
    return False
