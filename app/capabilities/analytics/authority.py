"""Requirement authority: which semantic obligations may define requested truth.

M2.12R2 demonstrated that an ``inferred`` Requirement with no authoritative parent and
no deterministic derivation could define filters, grouping, limits, qualification and
role, and that both the analytical Tool and the independent Judge would then treat it
as user-requested truth.

This module is deliberately small. It does not interpret language and it does not trust
labels such as ``inferred``/``analysis``/``constraint``/``derived`` as proof. It answers
one question:

    Given the current set of bound Requirements, which ones are authoritative and which
    are unproven planning/model proposals?

Rules
-----
* A Requirement directly grounded in the bound user message (``source=user``) needs
  backend-bound message/span ancestry. A source label or normalized statement is no proof.
* A user clarification needs a matching backend-validated semantic delta.
* An ``inferred`` Requirement is authoritative only when it:
    1. names at least one parent Requirement id,
    2. every named parent is present and itself authoritative (no cycles, no dangling
       ids, no cross-goal ids used as proof),
    3. declares a governed derivation kind, and
    4. carries a non-empty audit proof label, and
    5. passes the finite parent/kind/child semantic transformation validator.
       Neither parent IDs nor proof prose establish semantic entailment.
* A governed domain default may be authoritative without a parent only for the finite
  set of domain semantics that the analytical domain itself owns (full eligible row
  population, regular-season competition default). ``derivation_kind="domain_default"``
  with a governed default name AND its exact corresponding payload is accepted;
  contradictory/restrictive payloads remain unproven.
* Anything else (``planner``, ``need``, ``tool``, unknown source) is unproven.
"""

from __future__ import annotations

from typing import Any, Iterable

# Deterministic derivations that can transform authoritative parent semantics into a
# derived obligation without changing factual meaning.
GOVERNED_DERIVATIONS = frozenset(
    {
        "canonical_entity_identity",       # bound player -> canonical MLBAM id
        "resolved_temporal_scope",         # authoritative date phrase -> exact window
        "governed_measure_definition",     # governed Measure name -> finite definition
        "governed_population_codes",       # regular-season population -> game-type codes
        "interpreter_requirement_binding", # user span -> structured Requirement
        "user_requirement_partition",      # splitting one user clause into sub-clauses
        "synonym_normalization",           # finite Shared Knowledge synonym of a user term
        "schema_field_binding",            # user dimension -> existing catalog field
        "governed_semantic_candidate",     # untrusted interpreter candidate -> governed, span-proven obligation
    }
)

# Domain-owned defaults that need no parent Requirement: they are authoritative by
# construction of the analytical domain, never by planner choice.
GOVERNED_DOMAIN_DEFAULTS = frozenset(
    {
        "full_eligible_row_population",
        "regular_season_competition_default",
        "scalar_result_default",
        "no_qualification_default",
        "no_semantic_top_n_default",
    }
)


def is_authoritative(
    req: dict[str, Any],
    *,
    known_ids: set[str],
    authoritative_ids: set[str],
    parent_requirements: Iterable[dict[str, Any]] = (),
    identity_evidence=(),
    bound_user_message=None,
    clarification_deltas=(),
) -> tuple[bool, str]:
    """Return ``(authoritative, reason)`` for one Requirement dict.

    ``authoritative_ids`` is the set already proven authoritative in this resolution
    pass. Parents must be a subset of it, which makes the check cycle-safe.
    """
    source = str(req.get("source") or "user")
    req_id = str(req.get("id") or "")
    if source in {"user", "clarification"}:
        from app.semantic.provenance import provenance_valid
        if provenance_valid(req, bound_message=bound_user_message, clarification_deltas=clarification_deltas):
            return True, "backend-bound user ancestry" if source == "user" else "validated backend clarification delta"
        return False, "Requirement has no valid bound user ancestry"
    if source == "inferred":
        derivation = str(req.get("derivation_kind") or "")
        if derivation == "domain_default":
            default = str(req.get("derivation_proof") or "")
            from .derivations import default_valid
            if default in GOVERNED_DOMAIN_DEFAULTS and default_valid(req):
                return True, f"governed domain default with validated payload: {default}"
            return False, "domain_default derivation is not a governed default"
        if not proof_of_parents(req, known_ids, authoritative_ids):
            return False, "inferred Requirement has no proven authoritative parent"
        if derivation not in GOVERNED_DERIVATIONS:
            return False, f"inferred Requirement derivation '{derivation}' is not governed"
        if not str(req.get("derivation_proof") or ""):
            return False, "inferred Requirement has no derivation proof"
        from .derivations import transformation_valid
        if not transformation_valid(req, list(parent_requirements), identity_evidence=identity_evidence):
            return False, f"semantic transformation is unproved: {derivation}"
        return True, f"validated semantic transformation: {derivation}"
    return False, f"Requirement source '{source}' is not authoritative"


def proof_of_parents(
    req: dict[str, Any],
    known_ids: set[str],
    authoritative_ids: set[str],
) -> bool:
    parents = [str(p) for p in (req.get("parent_requirement_ids") or []) if str(p)]
    if not parents:
        return False
    if any(parent not in known_ids for parent in parents):
        return False
    return all(parent in authoritative_ids for parent in parents)


def resolve_authority(
    requirements: Iterable[dict[str, Any]],
    *, identity_evidence=(), bound_user_message=None, clarification_deltas=(),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Partition Requirements into authoritative and unproven, with issues.

    Authority is resolved to a fixed point so a chain of valid derivations is accepted
    while a dangling or non-authoritative parent is rejected. Resolution is bounded by
    the finite Requirement count.
    """
    reqs = [dict(r) for r in requirements]
    known_ids = {str(r.get("id")) for r in reqs if r.get("id")}
    authoritative: dict[str, dict[str, Any]] = {}
    unproven: dict[str, dict[str, Any]] = {}
    reasons: dict[str, str] = {}

    # Direct grounding first. Every non-inferred source is classified here (including
    # planner/need/tool sources, which ``is_authoritative`` rejects with a typed reason).
    for req in reqs:
        source = str(req.get("source") or "user")
        if source == "inferred":
            continue
        key = str(req.get("id") or id(req))
        ok, reason = is_authoritative(req, known_ids=known_ids, authoritative_ids=set(),
                                      bound_user_message=bound_user_message, clarification_deltas=clarification_deltas)
        (authoritative if ok else unproven)[key] = req
        reasons[key] = reason

    # Derived requirements to a fixed point.
    pending = [r for r in reqs if str(r.get("source") or "user") == "inferred"]
    for _ in range(len(pending) + 1):
        progressed = False
        for req in list(pending):
            ok, reason = is_authoritative(
                req,
                known_ids=known_ids,
                authoritative_ids=set(authoritative),
                parent_requirements=[authoritative[p] for p in req.get('parent_requirement_ids', []) if p in authoritative],
                identity_evidence=identity_evidence,
            )
            if ok:
                authoritative[str(req.get("id") or id(req))] = req
                reasons[str(req.get("id") or id(req))] = reason
                pending.remove(req)
                progressed = True
        if not pending or not progressed:
            break
    for req in pending:
        key = str(req.get("id") or id(req))
        _, reason = is_authoritative(req, known_ids=known_ids, authoritative_ids=set(authoritative),
                                     parent_requirements=[authoritative[p] for p in req.get('parent_requirement_ids', []) if p in authoritative],
                                     identity_evidence=identity_evidence)
        unproven[key] = req
        reasons[key] = reason

    issues = [
        f"unproved non-authoritative Requirement {req.get('id', '')}: {reasons.get(str(req.get('id')), '')}"
        for req in unproven.values()
    ]
    return list(authoritative.values()), list(unproven.values()), issues
