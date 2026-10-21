"""Judge-local admission of individual statistical output units.

A receipt attests execution, not that a pooled scalar answers every input entity.
This module checks the current Requirement contract AND exact subject row identity.
It returns inspectable candidates only; Judge owns Claims, coverage and status.
"""
from __future__ import annotations

import json
import math

from app.capabilities.analytics.conformance import (
    ExecutionAttestation, compare, requested_contract, receipt_usable,
)


def assess_subjects(goal, artifacts, propositions, authoritative_ids):
    from app.verification.artifact_assessment import _identities
    from app.capabilities.analytics.conformance import independent_analytical_partitions
    if len({a.id for a in artifacts}) != len(artifacts):
        return {}  # a witness locator must identify exactly one collected Artifact
    identities = _identities(goal, artifacts)
    reqs = {r.id: r for r in goal.requirements}
    partitions = independent_analytical_partitions(goal)
    result = {}
    for prop in propositions:
        if (prop.subject_granularity != 'PER_SUBJECT' or not prop.subject_requirement_ids or prop.depends_on
                or not set(prop.owner_requirement_ids + prop.subject_requirement_ids) <= authoritative_ids):
            continue
        subject_ids = set()
        for rid in prop.subject_requirement_ids:
            req = reqs.get(rid)
            value = req.value if req and isinstance(req.value, dict) else {}
            names = [value.get('mention'), value.get('canonical_name')]
            ids = set().union(*(identities.get(str(n).strip().casefold(), set()) for n in names if n))
            if len(ids) != 1:
                subject_ids.clear()
                break
            subject_ids.update(str(i) for i in ids)
        if len(subject_ids) != 1:
            continue
        local = next((g for g in partitions if {prop.requirement_id, *prop.subject_requirement_ids}
                      <= {r.id for r in g.requirements}), goal)
        from app.semantic.subject_granularity import subject_requirements
        owner = reqs[prop.requirement_id]
        permitted = {r['id'] for r in subject_requirements(owner, local.requirements)}
        if not set(prop.subject_requirement_ids) <= permitted:
            continue  # a persisted/proposed slot cannot change its owner's subjects
        contexts = [local]
        if local is not goal:
            # Joint acquisition may share the Goal's input identities, but never
            # another task's requested metrics or narrowing clauses. The existing
            # independent partition still owns the output unit and its meaning.
            population_view = local.model_copy(update={'requirements': [
                *[r for r in local.requirements if r.kind.value != 'ENTITY'],
                *[r for r in goal.requirements if r.kind.value == 'ENTITY']]})
            contexts.append(population_view)
        candidates = []
        for artifact in artifacts:
            for context in contexts:
                try:
                    candidate = _candidate(context, prop, artifact, identities, subject_ids)
                    if candidate:
                        candidates.append(candidate)
                        break  # one Artifact is still only one witness
                except (ValueError, TypeError, KeyError, IndexError):
                    continue  # collected, not admitted
        if not candidates:
            continue
        # Exact same governed cell must agree across ALL candidates. IDs/providers
        # are witnesses, not extra weight, corroboration or a majority vote.
        # Compare finite numbers by exact numeric equality, not JSON spelling:
        # 44 and 44.0 are one value; no rounding/tolerance or majority is used.
        if all(c['cells'] == candidates[0]['cells'] for c in candidates):
            result[prop.id] = min(candidates, key=lambda c: (not c['complete'], c['artifact_id']))
    return result


def _candidate(goal, prop, artifact, identities, subject_ids):
    if (artifact.kind != 'DATABASE_RESULT' or artifact.status not in {'ACCEPTED', 'PARTIAL'}
            or not receipt_usable(artifact)):
        return None
    data = artifact.structured_data
    executed = ExecutionAttestation.model_validate(data['execution_receipt']['attestation'])
    # Bind the actual partition to independently resolved current user subjects;
    # an omitted sibling remains an obligation, not an execution dependency of A.
    owner = next(r for r in goal.requirements if r.id == prop.requirement_id)
    known = set()
    for req in goal.requirements:
        # Input population belongs to this authoritative context. The selected
        # proposition below still owns exactly one output identity/row footprint.
        if req.kind.value != 'ENTITY' or not req.required:
            continue
        value = req.value if isinstance(req.value, dict) else {}
        for name in (value.get('mention'), value.get('canonical_name')):
            ids = identities.get(str(name or '').strip().casefold(), set())
            if len(ids) == 1:
                known.update(str(i) for i in ids)
    actual_ids = {str(i) for e in executed.entities for i in e.ids}
    if not subject_ids <= actual_ids or not actual_ids <= known:
        return None
    entities = [e.model_dump(mode='json') for e in executed.entities]
    inputs = dict(analytical_requirements=[r.model_dump(mode='json') for r in goal.requirements if r.required],
        raw_query=goal.raw_query, bound_user_intent=goal.raw_query,
        resolved_time_scope=goal.resolved_time_scope, _resolution_state=goal.analytical_resolution_state,
        _semantic_deltas=[d.model_dump(mode='json') for d in goal.semantic_deltas],
        _canonical_identity_exports=entities)
    bound = requested_contract(dict(entities=entities, season=executed.season,
        date_range=executed.date_range.model_dump(mode='json') if executed.date_range else None),
        inputs, [], source_review_text=goal.analytical_partition_clause or None)
    identity_column = {'BATTER': 'batter_id', 'PITCHER': 'pitcher_id'}.get(bound.analytical_role)
    if not identity_column or not actual_ids:
        return None
    # A single-identity scalar and a grouped row for that same identity are the
    # same individual output unit. Never remove any other requested dimension.
    if len(actual_ids) == 1 and identity_column not in executed.group_by:
        bound = bound.model_copy(update={'group_by': tuple(g for g in bound.group_by if g != identity_column)})
    proven = {m['key'] for m in executed.measures if m.get('verified') and m.get('key')}
    key = owner.value.get('key') if owner.kind.value == 'MEASURE' and isinstance(owner.value, dict) else None
    if key:
        if key not in proven or key not in bound.measures:
            return None
        bound = bound.model_copy(update={'measures': (key,)})
    elif set(bound.measures) - proven:
        return None  # an individual line cannot discard a sibling requested metric
    status = compare(bound, executed).status
    if status not in {'MATCH', 'PARTIAL'}:
        return None
    grouped = identity_column in executed.group_by
    if not grouped and len(actual_ids) != 1:
        return None
    rows = data.get('rows')
    if not isinstance(rows, list) or not rows or (not grouped and len(rows) != 1):
        return None
    exports = [e for e in artifact.exports if e.type in {'STATISTICAL_RESULT', 'RANKED_ENTITY_SET'}]
    if not exports or any(e.payload.get('rows') != rows for e in exports):
        return None
    indices = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            return None
        if grouped:
            value = row.get(identity_column)
            if type(value) not in {int, str} or str(value) not in actual_ids:
                return None
            if str(value) in subject_ids:
                indices.append(i)
        else:
            indices.append(i)
    if not indices:
        return None
    measures = [m for m in executed.measures if m.get('verified') and m.get('unit')
                and all(m.get('column') in rows[i] for i in indices)
                and (m.get('key') == key if key else not bound.measures or m.get('key') in bound.measures)]
    if not measures:
        return None
    cells = []
    group_keys = set()
    for i in indices:
        row = rows[i]
        dimensions = {g: row[g] for g in executed.group_by}
        group_key = json.dumps(dimensions, sort_keys=True)
        if group_key in group_keys:
            return None  # duplicate individual/group row, not two observations
        group_keys.add(group_key)
        for measure in measures:
            value = row[measure['column']]
            if type(value) not in {int, float} or not math.isfinite(value):
                return None
            cells.append(dict(subject_ids=sorted(subject_ids), dimensions=dimensions,
                metric=measure['key'], unit=measure['unit'], value=value))
    # Normalize the incidental identity GROUP BY and row ordering for equivalence.
    for cell in cells:
        cell['dimensions'].pop(identity_column, None)
    cells.sort(key=lambda c: json.dumps(c, sort_keys=True))
    return dict(proposition_id=prop.id, requirement_id=prop.requirement_id,
        subject=prop.subject, subject_ids=sorted(subject_ids), artifact_id=artifact.id,
        row_indices=indices, fields=sorted({m['column'] for m in measures}),
        identity_column=identity_column if grouped else None, cells=cells,
        requested=bound.model_dump(mode='json'), attestation_digest=data['execution_receipt']['attestation_digest'],
        result_digest=data['execution_receipt']['result_digest'],
        complete=status == 'MATCH' and artifact.status == 'ACCEPTED')
