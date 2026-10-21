"""Judge-local admission of requested original scalar observations.

Never infer reports from operation operands, Need success, source_scopes or Python.
The existing independent scalar-root receipt validator is reused unchanged. Each
reporting slot owns its requested scope and is proved from an exact exported cell.
"""
import math
from app.capabilities.analytics.catalog import CATALOG
from app.capabilities.analytics.conformance import digest
from app.contracts.operations import OperandCell
from app.verification.artifact_assessment import _identities
from app.verification.governed_operations import OperationAdmission, select_equivalent_root


def assess_reporting(goal, artifacts, propositions, authoritative_ids):
    identities = _identities(goal, artifacts)
    admission = OperationAdmission(artifacts, goal, authoritative_ids, identities)
    owners = {r.id: r for r in goal.requirements if r.id in authoritative_ids}
    result = {}
    inspected = 0
    # The current scope vocabulary cannot discharge arbitrary extra restrictions.
    # Preserve those obligations instead of approving an unrestricted substitute.
    extra_scope = any(r.required and r.kind.value == 'CONSTRAINT' for r in goal.requirements)
    years = {r.value for r in goal.requirements if r.required and r.kind.value == 'SEASON'
             and type(r.value) is int}
    populations = {tuple(sorted(codes)) for r in goal.requirements
        if r.required and r.kind.value == 'GAME_POPULATION'
        for codes in [CATALOG.resolve_population(str(r.value))] if codes}
    for prop in propositions:
        owner = owners.get(prop.requirement_id)
        contract = owner.reporting_contract if owner else None
        index = prop.reporting_index
        if (contract is None or index is None or index >= len(contract.cells)
                or owner.operation_contract is not None or admission.duplicate_ids or extra_scope
                or (owner.evidence_intent and owner.evidence_intent.mode != 'CONFIRMED_STATE')):
            continue
        scope = contract.cells[index]
        from app.semantic.reporting import scalar_scope_conflict
        if scalar_scope_conflict(owner, scope):
            continue
        if identities and not scope.subjects:
            continue  # a named individual cannot silently become a league total
        if years and (scope.season not in years and scope.date_range is None):
            continue
        if populations and tuple(scope.game_types or []) not in populations:
            continue
        found = []
        for artifact in artifacts:
            if artifact.kind != 'DATABASE_RESULT' or artifact.status != 'ACCEPTED':
                continue
            rows = artifact.structured_data.get('rows')
            exports = [e for e in artifact.exports if e.payload.get('rows') == rows]
            if not isinstance(rows, list) or len(rows) != 1 or len(exports) != 1 or not isinstance(rows[0], dict):
                continue
            if sum(e.id == exports[0].id for e in artifact.exports) != 1:
                continue
            # Columns are proposals only: root() reconstructs the execution receipt and
            # requires the field to be the requested, independently verified measure.
            for field, value in rows[0].items():
                inspected += 1
                if inspected > 1024:
                    return {}  # no order-dependent approval from a truncated inspection
                if type(value) not in (int, float) or not math.isfinite(value):
                    continue
                cell = OperandCell(artifact_id=artifact.id, export_id=exports[0].id,
                                   row_index=0, field=field)
                try:
                    root = admission.root(artifact, cell, scope)
                except (ValueError, TypeError, KeyError, IndexError):
                    continue
                proof = {'requirement_id': owner.id, 'proposition_id': prop.id,
                    'reporting_index': index, 'requested': scope.model_dump(mode='json'),
                    'contract_digest': digest(contract.model_dump(mode='json')),
                    'cell': cell.model_dump(), 'value': value, **root}
                proof['proof_digest'] = digest(proof)
                found.append(proof)
        # Equivalent approved roots are alternative witnesses, not extra operands
        # or independent corroboration. Conflicting normalized values stay unresolved.
        selected = select_equivalent_root(found)
        if selected is not None:
            result[prop.id] = selected
    return result


def reporting_scope_ids(goal, proof):
    """Shared scope support from an admitted original cell, never sibling report support."""
    scope = proof['requested']
    return {r.id for r in goal.requirements if r.required and (
        (r.kind.value == 'SEASON' and r.value == scope['season']) or
        (r.kind.value == 'GAME_POPULATION' and scope['game_types'] is not None
         and sorted(CATALOG.resolve_population(str(r.value)) or []) == scope['game_types'])
    )}
