"""Constraint normalization and authority precedence.

Precedence: SYSTEM_POLICY > USER_CONSTRAINT > USER_PREFERENCE > INFERRED_DEFAULT. A
higher-authority constraint on the same key replaces a lower-authority one; conflicting
constraints of equal authority are preserved (for example a numeric range).
"""

from collections.abc import Iterable

from app.models.contracts import AUTHORITY_PRECEDENCE, Constraint


def authority_rank(authority: str) -> int:
    return AUTHORITY_PRECEDENCE.get(authority, 0)


def normalize_constraints(constraints: Iterable[Constraint]) -> tuple[Constraint, ...]:
    groups: dict[tuple[str, str], list[Constraint]] = {}
    for constraint in constraints:
        groups.setdefault((constraint.kind, constraint.key), []).append(constraint)
    result: list[Constraint] = []
    for key in sorted(groups):
        items = groups[key]
        top = max(authority_rank(item.authority) for item in items)
        kept: list[Constraint] = []
        for item in items:
            if authority_rank(item.authority) == top and item not in kept:
                kept.append(item)
        result.extend(kept)
    return tuple(result)


def effective_constraints(*groups: Iterable[Constraint]) -> tuple[Constraint, ...]:
    """Merge constraint groups, letting higher authority win on the same key."""
    merged: list[Constraint] = []
    for group in groups:
        merged.extend(group)
    return normalize_constraints(merged)
