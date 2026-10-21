"""Deterministic, Requirement-local derived admission. No program/AST is proof.

Every output is replayed from exact exported cells through recursively admitted
parents. The trace contains the semantic contract, ordered cell identities, scope,
receipt digests, output identity and replay digest; tools cannot bless that trace.
"""
import math
import statistics
from app.contracts.models import ArtifactAssessment
from app.contracts.operations import GovernedOperation
from app.capabilities.analytics.conformance import (
    ExecutionAttestation, RequestedAnalyticalContract, EntityConstraint,
    compare, digest, receipt_usable,
)


def evaluate_operation(kind, values, parameters):
    if not values or any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
        raise ValueError('operands must be finite factual numbers')
    if kind == 'SUBTRACT':
        result = values[0] - values[1]
    elif kind == 'RATIO':
        result = values[0] / values[1]
    elif kind == 'PERCENT_CHANGE':
        result = (values[0] - values[1]) / values[1] * 100
    elif kind == 'RANK':
        # Stable ordinal ranking. Ties preserve the governed operand order.
        result = [{'operand': i, 'value': values[i], 'rank': rank+1} for rank, i in enumerate(
            sorted(range(len(values)), key=lambda i: values[i], reverse=parameters.direction == 'DESC'))]
    else:
        reducer = parameters.reducer if kind == 'AGGREGATE' else kind
        result = {'SUM': sum, 'MEAN': statistics.mean, 'MIN': min, 'MAX': max,
                  'MEDIAN': statistics.median, 'COUNT': len}[reducer](values)
    if isinstance(result, (int, float)) and not math.isfinite(result):
        raise ValueError('nonfinite output')
    return result


def select_equivalent_root(proofs):
    """One stable witness, not a source vote or a claim of independence.

    Callers supply ONLY independently replayed roots for the same obligation.
    Scope includes canonical entity IDs, measure, role, time, competition and
    qualification. Units come from the reconstructed compiler attestation. No
    rounding, conversion, receipt count or provenance overlap resolves conflict.
    """
    if not proofs:
        return None
    first = proofs[0]
    if any(p['scope'] != first['scope'] or p['unit'] != first['unit']
           or p['value'] != first['value'] for p in proofs):
        return None
    return min(proofs, key=lambda p: (p['cell']['artifact_id'], p['cell']['export_id'],
                                     p['cell']['row_index'], p['cell']['field']))


class OperationAdmission:
    def __init__(self, artifacts, goal, authoritative_ids, identities):
        self.by_id = {a.id: a for a in artifacts}
        self.duplicate_ids = len(self.by_id) != len(artifacts)
        self.owners = {r.id: r for r in goal.requirements if r.id in authoritative_ids}
        self.keys = {}
        for owner in self.owners.values():
            if owner.operation_contract:
                self.keys.setdefault(owner.operation_contract.key, []).append(owner.id)
        self.identities = identities
        self.cache = {}
        self.visiting = set()
        self.inspected_cells = 0
        self.proof_bytes = 0

    def root(self, artifact, cell, scope):
        if (artifact.kind != 'DATABASE_RESULT' or artifact.status not in {'ACCEPTED', 'PARTIAL'}
                or not receipt_usable(artifact)):
            raise ValueError('root lacks approved database execution ancestry')
        data = artifact.structured_data
        executed = ExecutionAttestation.model_validate(data['execution_receipt']['attestation'])
        # Scope is independently owned by the Requirement, not copied from the result.
        subjects = set()
        for name in scope.subjects:
            ids = self.identities.get(name.strip().casefold(), set())
            if len(ids) != 1:
                raise ValueError('unresolved/ambiguous governed operand subject')
            subjects.update(int(i) for i in ids)
        # Named individual output units cannot be pooled into one scalar root.
        # Explicit cross-subject SUM/MEAN/etc. use separate governed operands;
        # multiple surface aliases for the SAME canonical subject remain valid.
        if len(subjects) > 1:
            raise ValueError('multiple individual subjects require separate governed root cells')
        expected_entities = (EntityConstraint(role=scope.analytical_role, ids=tuple(sorted(subjects))),) if subjects else ()
        requested = RequestedAnalyticalContract(
            analytical_role=scope.analytical_role, entities=expected_entities,
            season=scope.season, date_range=scope.date_range.model_dump() if scope.date_range else None,
            game_types=tuple(scope.game_types) if scope.game_types is not None else None,
            qualification=scope.qualification.model_dump() if scope.qualification else None,
            measures=(scope.measure,), group_by=(), filters=(), limit=None)
        if compare(requested, executed).status != 'MATCH':
            raise ValueError('operand does not match exact governed subject/time/population/qualification scope')
        if executed.group_by or executed.filters or executed.ordering:
            raise ValueError('grouped/filtered/ordered roots need a typed scope extension')
        ir = data['execution_receipt']['validated_ir']
        # Reconstructed Safe IR compiles aggregates without GROUP BY as one global
        # SQL result row. A positive outer result ceiling cannot restrict its input
        # population. Never generalize this proof to grouped or raw-row selections.
        scalar_aggregate = bool(ir.get('aggregates')) and not ir.get('group_by')
        if not scalar_aggregate or (executed.limit is not None and
                (type(executed.limit) is not int or executed.limit < 1)):
            raise ValueError('root does not prove a complete scalar aggregate')
        rows = data.get('rows')
        if not isinstance(rows, list) or len(rows) != 1 or cell.row_index != 0:
            raise ValueError('scalar operation requires an exact singleton operand row')
        measures = [m for m in executed.measures if m.get('verified')
                    and m.get('column') == cell.field and m.get('key') == scope.measure]
        if len(measures) != 1 or not measures[0].get('unit'):
            raise ValueError('operand field is not the requested verified measure/unit')
        exports = [e for e in artifact.exports if e.id == cell.export_id]
        if (cell.artifact_id != artifact.id or len(exports) != 1
                or exports[0].payload.get('rows') != rows):
            raise ValueError('operand export differs from inspected factual result')
        value = rows[0][cell.field]
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('operand must be a finite factual number')
        return {'scope': requested.model_dump(mode='json'), 'unit': measures[0]['unit'],
                'scalar_shape': {'global_aggregate': True,
                                 'nonrestricting_result_ceiling': executed.limit},
                'attestation_digest': data['execution_receipt']['attestation_digest'],
                'result_digest': data['execution_receipt']['result_digest']}

    def assess(self, artifact_id):
        if artifact_id in self.cache:
            return self.cache[artifact_id]
        if artifact_id in self.visiting or len(self.visiting) >= 64:
            return self.rejected(artifact_id, 'cyclic or over-depth operation ancestry')
        self.visiting.add(artifact_id)
        try:
            result = self._assess(artifact_id)
        except (ValueError, KeyError, TypeError, IndexError, ZeroDivisionError, OverflowError) as exc:
            result = self.rejected(artifact_id, str(exc))
        finally:
            self.visiting.remove(artifact_id)
        self.cache[artifact_id] = result
        return result

    @staticmethod
    def rejected(artifact_id, reason):
        return ArtifactAssessment(artifact_id=artifact_id, source_kind='DERIVED', status='REJECTED', reasons=[reason])

    def _assess(self, artifact_id):
        if self.duplicate_ids:
            raise ValueError('duplicate artifact identity')
        artifact = self.by_id[artifact_id]
        if artifact.status not in {'ACCEPTED', 'PARTIAL'}:
            raise ValueError('unapproved parent/result status')
        raw = artifact.structured_data.get('governed_operations')
        if not isinstance(raw, list) or not 0 < len(raw) <= 32:
            raise ValueError('no typed governed operation; free Python is non-authoritative')
        operations = [GovernedOperation.model_validate(op) for op in raw]
        fields = [op.output_field for op in operations]
        if len(set(fields)) != len(fields):
            raise ValueError('ambiguous output field identity')
        proofs, expected = [], {}
        for op in operations:
            owner = self.owners.get(op.requirement_id)
            contract = owner.operation_contract if owner else None
            if contract is None or self.keys.get(contract.key) != [owner.id]:
                raise ValueError('operation has no unique authoritative Requirement contract')
            if (op.kind != contract.kind or op.output_field != contract.output_field
                    or op.parameters != contract.parameters or len(op.operands) != len(contract.operands)):
                raise ValueError('executed operation/output/parameters differ from requested contract')
            traces, values, roots = [], [], {}
            for cell, obligation in zip(op.operands, contract.operands):
                self.inspected_cells += 1
                if self.inspected_cells > 1024:
                    raise ValueError('governed ancestry inspection budget exceeded')
                operand = self.by_id.get(cell.artifact_id)
                if operand is None or operand.status not in {'ACCEPTED','PARTIAL'}:
                    raise ValueError('missing or unapproved operand artifact')
                exports = [e for e in operand.exports if e.id == cell.export_id]
                if len(exports) != 1:
                    raise ValueError('operand export identity is missing/ambiguous')
                rows = operand.structured_data.get('rows')
                if exports[0].payload.get('rows') != rows:
                    raise ValueError('export rows differ from inspected factual result')
                if obligation.scope is not None:
                    trace = self.root(operand, cell, obligation.scope)
                else:
                    if operand.kind != 'DERIVED_MEASURE':
                        raise ValueError('governed parent is not a derived result')
                    parent = self.assess(operand.id)
                    if parent.status != 'ELIGIBLE':
                        raise ValueError('derived parent was not independently admitted')
                    matches = [p for p in parent.derived_proofs
                               if p['contract']['key'] == obligation.parent_key
                               and p['output_field'] == obligation.parent_output == cell.field]
                    if len(matches) != 1 or cell.row_index != 0:
                        raise ValueError('wrong governed parent output/row')
                    parent_proof = matches[0]
                    trace = {'parent': {'artifact_id':operand.id, 'requirement_id':parent_proof['requirement_id'],
                        'output_field':cell.field, 'replay_digest':parent_proof['replay_digest']}}
                    for root in parent_proof['roots']:
                        roots[digest(root)] = root
                value = rows[cell.row_index][cell.field]
                values.append(value)
                entry = {'cell':cell.model_dump(), 'value':value, **trace}
                traces.append(entry)
                if obligation.scope is not None:
                    roots[digest(entry)] = entry
                if len(roots) > 256:
                    raise ValueError('governed root ancestry budget exceeded')
            value = evaluate_operation(op.kind, values, op.parameters)
            expected[op.output_field] = value
            proof = {'requirement_id': owner.id, 'contract': contract.model_dump(),
                     'operation': op.model_dump(), 'operands': traces, 'roots':list(roots.values()),
                     'output_field': op.output_field, 'value': value}
            import json
            self.proof_bytes += len(json.dumps(proof,sort_keys=True))
            if self.proof_bytes > 1_000_000:
                raise ValueError('governed proof retention budget exceeded')
            proof['replay_digest'] = digest(proof)
            proofs.append(proof)
        # Whole artifact equality prevents valid-field + fabricated-sibling laundering.
        if artifact.structured_data.get('rows') != [expected]:
            raise ValueError('output cells do not exactly equal trusted per-field evaluation')
        if any(e.payload.get('rows') != [expected] for e in artifact.exports):
            raise ValueError('derived export differs from admitted output')
        declared = artifact.structured_data.get('input_artifact_ids')
        used = {cell.artifact_id for op in operations for cell in op.operands}
        if not isinstance(declared, list) or set(declared) != used:
            raise ValueError('declared ancestry differs from governed operands')
        return ArtifactAssessment(artifact_id=artifact_id, source_kind='DERIVED', status='ELIGIBLE',
            derived_operations=sorted({op.kind for op in operations}),
            derived_operand_fields=sorted({c.field for op in operations for c in op.operands}),
            derived_requirement_ids=sorted({op.requirement_id for op in operations}),
            derived_proofs=proofs, reasons=['trusted per-output evaluation and recursive approved ancestry'])
