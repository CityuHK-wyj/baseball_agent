"""Typed scalar comparison support over independently admitted analytical receipts.

No user-language interpretation, Planner truth or output-column guessing occurs here.
Operand IDs refer to authoritative, complete Requirement subsets; each side is rebound
through the existing requested-vs-executed contract machinery. The only presently
supported result grain is one scalar row per side. Grouped-row selectors, arbitrary
computed outputs and web prose remain unproved.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.capabilities.analytics.conformance import (
    ExecutionAttestation, compare, contract_for_goal, receipt_requirement_ids,
    receipt_usable,
)
from app.capabilities.analytics.predicates import canonical_population
from app.contracts.models import (
    AnswerProposition, Artifact, ComparativeRelation, Goal, PlannerNeed,
    PropositionKind, RelationKind, RequirementAuthority, RequirementKind,
)


@dataclass(frozen=True)
class OperandObservation:
    artifact_id: str
    requirement_ids: tuple[str, ...]
    measure_key: str
    column: str                        # from IR-attested measure, not presentation text
    value: Decimal                     # actual scalar cell in the receipt-covered row
    requested: object                  # RequestedAnalyticalContract, rebound from Requirements


@dataclass(frozen=True)
class ComparisonProof:
    lhs: OperandObservation | None = None
    rhs: OperandObservation | None = None
    verified: bool = False
    contradicted: bool = False

    @property
    def artifact_ids(self) -> list[str]:
        return list(dict.fromkeys(o.artifact_id for o in (self.lhs, self.rhs) if o))


def _local_goal(goal, operand):
    by_id = {r.id: r for r in goal.requirements}
    ids = operand.requirement_ids
    if not ids or len(set(ids)) != len(ids) or any(i not in by_id for i in ids):
        return None
    selected = [by_id[i] for i in ids]
    if any(not r.required or r.source not in {"user", "clarification"}
           or r.authority_status != RequirementAuthority.AUTHORITATIVE for r in selected):
        return None
    analyses = [r for r in selected if r.kind == RequirementKind.ANALYSIS]
    measures = [r for r in selected if r.kind == RequirementKind.MEASURE]
    entities = [r for r in selected if r.kind == RequirementKind.ENTITY]
    if len(analyses) != 1 or len(measures) != 1 or len(entities) != 1:
        return None
    analysis = analyses[0]
    measure = measures[0]
    if analysis.comparison is not None or analysis.completion and analysis.completion.value == "EXPLANATION":
        return None
    if operand.measure_requirement_id != measure.id or not isinstance(measure.value, dict):
        return None
    key = measure.value.get("key")
    if not key or measure.value.get("derivable") is False:
        return None
    clause = analysis.bound_source_clause
    # The execution-bound clause has to be sourced in a real user/clarification turn,
    # rather than a Planner objective or a replacement assembled by this verifier.
    if not clause or clause not in (analysis.original_intent or goal.raw_query):
        return None
    local = goal.model_copy(update={"requirements": selected, "analytical_partition_clause": clause,
                                   "propositions": [], "analytical_resolution_state": {}})
    return local, analysis.id, key


def _observation(local, analysis_id, key, artifacts, needs_by_id, requirement_ids):
    found = []
    for artifact in artifacts:
        if artifact.kind != "DATABASE_RESULT" or artifact.status != "ACCEPTED":
            continue
        need = needs_by_id.get(artifact.need_id)
        if need is None or analysis_id not in need.requires:
            continue  # never borrow another Requirement's Artifact by a coincidental value
        if not receipt_usable(artifact):
            continue
        allowed = receipt_requirement_ids(artifact, local, artifacts)
        if not set(requirement_ids) <= allowed:
            continue
        receipt = artifact.structured_data["execution_receipt"]
        executed = ExecutionAttestation.model_validate(receipt["attestation"])
        requested = contract_for_goal(local, artifacts, executed)
        if (requested is None or requested.issues or key not in requested.measures
                or compare(requested, executed).status != "MATCH"
                or requested.group_by or executed.group_by or executed.limit is not None
                or executed.internal_row_ceiling is not None):
            continue
        measures = [m for m in executed.measures if m.get("key") == key and m.get("verified")]
        rows = artifact.structured_data.get("rows")
        if (len(measures) != 1 or not isinstance(rows, list) or len(rows) != 1
                or artifact.structured_data.get("row_count") != 1
                or artifact.structured_data.get("empty_population")
                or not isinstance(rows[0], dict)):
            continue
        column = measures[0]["column"]
        raw = rows[0].get(column)
        if isinstance(raw, bool) or not isinstance(raw, (int, float, Decimal)):
            continue
        try:
            value = Decimal(str(raw))
        except InvalidOperation:
            continue
        if not value.is_finite():
            continue
        found.append(OperandObservation(artifact.id, requirement_ids, key, column, value, requested))
    # Multiple plausible cells are ambiguous; neither order nor coincidence proves ownership.
    return found[0] if len(found) == 1 else None


def prove_comparison(goal: Goal, proposition: AnswerProposition, artifacts: list[Artifact],
                     needs_by_id: dict[str, PlannerNeed]) -> ComparisonProof:
    """Rebind each explicitly owned operand; never promote prose or a free-form proof."""
    if proposition.kind != PropositionKind.COMPARISON or not proposition.requirement_id:
        return ComparisonProof()
    owners = [r for r in goal.requirements if r.id == proposition.requirement_id]
    if len(owners) != 1:
        return ComparisonProof()
    owner = owners[0]
    contract = owner.comparison
    if (owner.kind != RequirementKind.ANALYSIS or owner.source not in {"user", "clarification"}
            or owner.authority_status != RequirementAuthority.AUTHORITATIVE
            or contract is None or proposition.comparison != contract
            or proposition.statement != owner.statement
            or proposition.required_information != owner.statement
            or proposition.relation_semantics != owner.relation_semantics
            or proposition.goal_id not in {"", goal.id}
            or sum(p.requirement_id == owner.id for p in goal.propositions) != 1):
        return ComparisonProof()
    if owner.relation_semantics and owner.relation_semantics.polarity != "AFFIRMED":
        return ComparisonProof()  # negated comparisons need a separately typed relation
    if owner.relation_semantics and (
            owner.relation_semantics.relation_kind not in {RelationKind.GENERAL, RelationKind.COMPARISON}
            or any(kind not in {RelationKind.GENERAL, RelationKind.COMPARISON}
                   for kind in owner.relation_semantics.relation_kinds)):
        return ComparisonProof()  # a scalar pair cannot prove a compound relation
    direction = owner.relation_semantics.comparison_direction if owner.relation_semantics else ""
    if direction and {"increase": ComparativeRelation.GT,
                      "decrease": ComparativeRelation.LT}.get(direction) != contract.relation:
        return ComparisonProof()  # a typed relation cannot override a conflicting bound direction
    sides = (contract.lhs, contract.rhs)
    if sides[0].requirement_ids == sides[1].requirement_ids:
        return ComparisonProof()
    # An unassigned shared restriction cannot silently disappear from both sides.
    referenced = set(sides[0].requirement_ids) | set(sides[1].requirement_ids)
    props_by_id = {p.id: p for p in goal.propositions}
    if any(dep not in props_by_id or props_by_id[dep].requirement_id not in referenced
           for dep in proposition.depends_on):
        return ComparisonProof()
    if any(r.required and r.id not in referenced and r.kind in {
        RequirementKind.ENTITY, RequirementKind.SEASON, RequirementKind.TIME,
        RequirementKind.GAME_POPULATION, RequirementKind.CONSTRAINT,
    } for r in goal.requirements):
        return ComparisonProof()
    local = [_local_goal(goal, operand) for operand in sides]
    if any(item is None for item in local):
        return ComparisonProof()
    (lhs_goal, lhs_analysis, lhs_key), (rhs_goal, rhs_analysis, rhs_key) = local
    # Same governed metric/role/population is the supported subset. Cross-metric unit
    # conversions, grouping and ranking require their own typed proof and stay closed.
    if lhs_key != rhs_key:
        return ComparisonProof()
    lhs = _observation(lhs_goal, lhs_analysis, lhs_key, artifacts, needs_by_id, sides[0].requirement_ids)
    rhs = _observation(rhs_goal, rhs_analysis, rhs_key, artifacts, needs_by_id, sides[1].requirement_ids)
    if not lhs or not rhs:
        return ComparisonProof(lhs, rhs)
    a, b = lhs.requested, rhs.requested
    try:
        compatible = (a.relation == b.relation and a.analytical_role == b.analytical_role
                      and a.game_types == b.game_types and a.qualification == b.qualification
                      and canonical_population(a.filters, a.relation) == canonical_population(b.filters, b.relation))
    except (ValueError, TypeError):
        compatible = False
    if not compatible or lhs.artifact_id == rhs.artifact_id:
        return ComparisonProof(lhs, rhs)
    # A different Requirement ID alone does not distinguish two identical operands.
    if a.entities == b.entities and a.season == b.season and a.date_range == b.date_range:
        return ComparisonProof(lhs, rhs)
    relation = contract.relation
    valid = (lhs.value > rhs.value if relation == ComparativeRelation.GT else
             lhs.value < rhs.value if relation == ComparativeRelation.LT else
             lhs.value == rhs.value if relation == ComparativeRelation.EQ else False)
    return ComparisonProof(lhs, rhs, verified=valid, contradicted=not valid)
