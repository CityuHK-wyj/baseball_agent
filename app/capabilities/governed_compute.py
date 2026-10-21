"""Candidate execution for the finite operation IR; admission remains Judge-owned."""
from app.contracts.operations import GovernedOperation, OperandCell
from app.verification.governed_operations import OperationAdmission, evaluate_operation, select_equivalent_root


def propose_bound_operations(goal, artifacts, *, context_artifacts=()):
    from app.verification.artifact_assessment import _identities
    unique = {a.id:a for a in artifacts}
    collected = {a.id:a for a in [*context_artifacts,*artifacts]}
    # Numeric candidate cells stay restricted to explicit bindings. Identity and
    # recursive parent inspection need the full Goal-local evidence context.
    admission = OperationAdmission(list(collected.values()), goal, {r.id for r in goal.requirements},
                                   _identities(goal, list(collected.values())))
    proposed = []
    for owner in goal.requirements:
        contract = owner.operation_contract
        if contract is None:
            continue
        cells = []
        for obligation in contract.operands:
            matches = []
            for artifact in unique.values():
                rows = artifact.structured_data.get('rows')
                if not isinstance(rows,list) or len(rows) != 1 or not isinstance(rows[0],dict):
                    continue
                for export in artifact.exports:
                    if export.payload.get('rows') != rows:
                        continue
                    for field in rows[0]:
                        cell = OperandCell(artifact_id=artifact.id, export_id=export.id, row_index=0, field=field)
                        if obligation.scope is not None:
                            try:
                                root = admission.root(artifact,cell,obligation.scope)
                            except (ValueError,KeyError,TypeError):
                                continue
                        else:
                            parent = admission.assess(artifact.id)
                            if parent.status != 'ELIGIBLE' or not any(
                                p['contract']['key'] == obligation.parent_key
                                and p['output_field'] == obligation.parent_output == field
                                for p in parent.derived_proofs):
                                continue
                        matches.append({'cell': cell.model_dump(), 'value': rows[0][field], **root}
                                       if obligation.scope is not None else {'cell': cell.model_dump()})
            # Only exact scalar roots have an equivalence proof. Derived candidates
            # retain their existing unique-parent gate and recursive Judge admission.
            selected = (select_equivalent_root(matches) if obligation.scope is not None
                        else matches[0] if len(matches) == 1 else None)
            if selected is None:
                break
            cells.append(OperandCell.model_validate(selected['cell']))
        if len(cells) == len(contract.operands):
            proposed.append(GovernedOperation(requirement_id=owner.id,kind=contract.kind,
                operands=cells, output_field=contract.output_field, parameters=contract.parameters))
    return proposed


def execute_operations(operations, artifacts):
    by_id = {a.id:a for a in artifacts}
    result = {}
    for op in operations:
        if op.output_field in result:
            raise ValueError('duplicate governed output')
        values = []
        for cell in op.operands:
            artifact = by_id[cell.artifact_id]
            exports = [e for e in artifact.exports if e.id == cell.export_id]
            if len(exports) != 1:
                raise ValueError('missing/ambiguous export')
            values.append(exports[0].payload['rows'][cell.row_index][cell.field])
        result[op.output_field] = evaluate_operation(op.kind, values, op.parameters)
    return [result]
