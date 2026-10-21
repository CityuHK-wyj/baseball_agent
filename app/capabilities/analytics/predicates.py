"""Finite semantic forms for catalog predicates, preserving SQL NULL semantics.

Only Safe IR operators are accepted. Conjunctions/disjunctions are flattened,
deduplicated and sorted; singleton IN and equality share a form. No implication,
distribution, complement, or data-dependent equivalence is attempted.
"""
from __future__ import annotations

import json
import math
from datetime import date

from app.capabilities.analytics.catalog import CATALOG
from app.capabilities.analytics.ir import Condition, _validate_condition, canonical_value

SCOPE_FIELDS = frozenset({"batter_id", "pitcher_id", "season", "game_date", "game_type"})


def _value(field, value, relation):
    spec = CATALOG.field(relation, field)
    if value is None:
        raise ValueError("NULL comparison/IN is unproven; use IS_NULL")
    if spec.role == "id" and isinstance(value, str) and value.isdigit(): value = int(value)
    return canonical_value(spec,value,field)


def _sort(nodes):
    return tuple(sorted(set(nodes), key=lambda x: json.dumps(x, sort_keys=True)))


def _compound(op, nodes):
    flattened = []
    for node in nodes:
        flattened.extend(node[1] if node[0] == op else [node])
    children = _sort(flattened)
    if not children:
        raise ValueError("empty compound predicate")
    # All children must be same-field non-NULL equality/finite IN forms.
    # SQL NULL remains UNKNOWN in both encodings; mixed operators stay untouched.
    if op == "OR" and all(c[0] in {"EQ", "IN"} for c in children) and len({c[1] for c in children}) == 1:
        values = _sort([v for c in children for v in ([c[2]] if c[0] == "EQ" else c[2])])
        return ("EQ", children[0][1], values[0]) if len(values) == 1 else ("IN", children[0][1], values)
    return children[0] if len(children) == 1 else (op, children)


def canonical_predicate(value, relation="pitch"):
    if isinstance(value, dict) and set(value) - set(Condition.model_fields):
        raise ValueError("unsupported predicate attributes")
    c = value if isinstance(value, Condition) else Condition.model_validate(value)
    if c.op in {"AND", "OR", "NOT"}:
        children = [canonical_predicate(x, relation) for x in c.conditions or []]
        # Safe IR NOT negates the conjunction of its children, just as SQLCompiler.
        return ("NOT", _compound("AND", children)) if c.op == "NOT" else _compound(c.op, children)
    field = c.field
    if not field or "FILTER" not in CATALOG.field(relation, field).ops:
        raise ValueError("unsupported predicate field")
    if c.op in {"IS_NULL", "IS_NOT_NULL"}:
        return (c.op, field)
    if c.op == "COMPARE":
        if c.comparator not in {"=", "!=", ">", ">=", "<", "<="}:
            raise ValueError("unsupported comparator")
        return ("EQ" if c.comparator == "=" else c.comparator, field, _value(field, c.value, relation))
    if c.op == "IN":
        if not c.values:
            raise ValueError("empty IN predicate")
        values = _sort([_value(field, v, relation) for v in c.values])
        return ("EQ", field, values[0]) if len(values) == 1 else ("IN", field, values)
    if c.op == "BETWEEN":
        low, high = _value(field, c.low, relation), _value(field, c.high, relation)
        # Express only the finite inclusive-bound equivalence, without interval solving.
        return _compound("AND", [(">=", field, low), ("<=", field, high)])
    raise ValueError("unsupported predicate operator")


def conjunction_leaves(conditions):
    for value in conditions or ():
        if isinstance(value, dict) and set(value) - set(Condition.model_fields):
            raise ValueError("unsupported predicate attributes")
        c = value if isinstance(value, Condition) else Condition.model_validate(value)
        if c.op == "AND":
            if not c.conditions:
                raise ValueError("empty AND predicate")
            yield from conjunction_leaves(c.conditions)
        else:
            if c.op == "OR":
                node = canonical_predicate(c)
                if node[0] in {"EQ", "IN"}:
                    c = Condition(op="COMPARE",field=node[1],comparator="=",value=node[2]) if node[0] == "EQ" else Condition(op="IN",field=node[1],values=list(node[2]))
            yield c


def canonical_population(conditions, relation="pitch"):
    """Scope atoms are compared by their governed scope dimensions, not twice.

    Mixed OR/NOT scope predicates remain in this representation and are unproven
    by the scope extractor. Measure-local conditions are never supplied here.
    """
    nodes = []
    for c in conjunction_leaves(conditions):
        if c.field in SCOPE_FIELDS:
            continue
        node = canonical_predicate(c, relation)
        nodes.extend(node[1] if node[0] == "AND" else [node])
    return _sort(nodes)


def validate_predicates(conditions, relation="pitch"):
    for c in conjunction_leaves(conditions):
        # Request predicates need the same finite catalog/operator validation as IR.
        # Canonical IDs may be strings in requests, but actual IR stays typed.
        if c.field in {"batter_id", "pitcher_id"}:
            c = c.model_copy(update={"value": int(c.value) if isinstance(c.value, str) and c.value.isdigit() else c.value,
                                     "values": [int(v) if isinstance(v, str) and v.isdigit() else v for v in c.values] if c.values else c.values})
        _validate_condition(CATALOG, relation, c)
        canonical_predicate(c, relation)
