"""Select a requested scalar input from authoritative finite semantic contracts.

Planner hints select work within the requested set; they cannot create or narrow scope.
This produces a requested contract, not execution evidence, approval or user reporting
obligations. Ordinary grouped/filtered analytical requests keep the existing pipeline.
"""
from app.capabilities.analytics.authority import resolve_authority
from app.capabilities.analytics.catalog import CATALOG
from app.capabilities.analytics.conformance import RequestedAnalyticalContract, EntityConstraint, digest
from app.capabilities.analytics.measures import MEASURES, resolve_texts
from app.verification.artifact_assessment import _identities


class ScalarMeasureUnsupported(ValueError):
    """Every typed scalar scope in this Need uses a Measure the source cannot derive."""


def scalar_input_contract(request, ctx, intent):
    if ctx is None or not getattr(ctx, 'goal', None):
        return None
    goal = ctx.goal
    ids = set(request.inputs.get('analytical_requirement_ids') or [])
    owners = [r for r in goal.requirements if r.id in ids
              and (r.operation_contract is not None or r.reporting_contract is not None)]
    if not owners:
        return None
    authoritative, _, _ = resolve_authority(
        [r.model_dump(mode='json') for r in goal.requirements], bound_user_message=goal.raw_query,
        clarification_deltas=[d.model_dump(mode='json') for d in goal.semantic_deltas],
        identity_evidence=[])
    authoritative_ids = {r['id'] for r in authoritative}
    if any(r.required and r.kind.value == 'CONSTRAINT' for r in goal.requirements):
        raise ValueError('scalar scope cannot discharge additional unresolved restrictions')
    from app.semantic.reporting import scalar_scope_conflict
    scopes = {}
    for owner in owners:
        if owner.id not in authoritative_ids:
            continue
        if owner.reporting_contract:
            for scope in owner.reporting_contract.cells:
                if not scalar_scope_conflict(owner, scope):
                    scopes[digest(scope.model_dump(mode='json'))] = scope
        if owner.operation_contract:
            for operand in owner.operation_contract.operands:
                if operand.scope is not None and not scalar_scope_conflict(owner, operand.scope):
                    scopes[digest(operand.scope.model_dump(mode='json'))] = operand.scope
    years = {r.value for r in goal.requirements if r.id in ids and r.kind.value == 'SEASON'
             and type(r.value) is int}
    # A hint is a selector, never a replacement for an owned scope.
    measure_hint = str(intent.get('measure') or '')
    canonical_keys = [key.strip() for key in measure_hint.split(',') if key.strip()]
    hinted_keys = (set(canonical_keys) if canonical_keys and all(key in MEASURES for key in canonical_keys)
                   else {m.key for m in resolve_texts([measure_hint]) if m.matched})
    identities = _identities(goal, ctx.artifacts)
    bound_ids = {int(i) for entity in intent.get('entities', []) for i in entity.get('ids', [])}
    selector = request.inputs.get('subjects')
    if selector is None:
        selector = (request.inputs.get('constraints') or {}).get('subjects')
    selected_ids = None
    if selector is not None:
        if not isinstance(selector, list) or not selector or any(type(n) is not str for n in selector):
            raise ValueError('subjects selector must reference requested scalar subject names')
        selected_ids = set()
        for name in selector:
            candidates = identities.get(name.strip().casefold(), set())
            if len(candidates) != 1:
                raise ValueError('scalar subject selector lacks a unique verified identity')
            selected_ids.update(int(i) for i in candidates)
    elif len(bound_ids) == 1:
        selected_ids = bound_ids
    selected = []
    blocked_measures: list[str] = []
    for scope in scopes.values():
        spec = MEASURES.get(scope.measure)
        if spec is None or not spec.derivable:
            blocked_measures.append(scope.measure)
            continue  # unavailable work stays a mandatory, unsupported reporting slot
        if hinted_keys and scope.measure not in hinted_keys:
            continue
        if len(years) == 1 and scope.season not in years:
            continue
        if intent.get('season') is not None and str(scope.season) != str(intent['season']):
            continue
        if intent.get('analytical_role') and scope.analytical_role != intent['analytical_role']:
            continue
        if intent.get('population') and sorted(CATALOG.resolve_population(intent['population']) or []) != (scope.game_types or []):
            continue
        if intent.get('date_range') and intent['date_range'] != (scope.date_range.model_dump() if scope.date_range else None):
            continue
        if intent.get('qualification') and intent['qualification'] != (scope.qualification.model_dump() if scope.qualification else None):
            continue
        if selected_ids is not None:
            scope_ids = set()
            for name in scope.subjects:
                scope_ids.update(int(i) for i in identities.get(name.strip().casefold(), set()))
            if scope_ids != selected_ids:
                continue  # a selector chooses an owned cell; it never creates one
        selected.append(scope)
    legacy_work = any(r.id in ids and r.id in authoritative_ids and r.kind.value == 'ANALYSIS'
        and r.reporting_contract is None and r.operation_contract is None for r in goal.requirements)
    if not selected and legacy_work:
        # A batch/Need may also serve an ordinary independent analysis. An unrelated
        # typed, unavailable output must not hijack that work. Its legacy conformance
        # still has to prove the result; this does not discharge the typed output.
        return None
    if not selected and blocked_measures and len(blocked_measures) == len(scopes):
        labels = []
        for key in sorted(set(blocked_measures)):
            spec = MEASURES.get(key)
            labels.append(f'{spec.label} ({spec.note})' if spec is not None and spec.note else key)
        raise ScalarMeasureUnsupported(
            'requested measure cannot be derived from the available data: ' + '; '.join(labels))
    populations = {digest({k: v for k, v in scope.model_dump(mode='json').items() if k != 'measure'})
                   for scope in selected}
    if not selected or len(populations) != 1:
        raise ValueError('scalar input scope is absent or ambiguous within the requested contracts; '
                         'select one requested subject scope with constraints.subjects')
    requested_keys = tuple(sorted({scope.measure for scope in selected}))
    scope = selected[0]
    subjects = set()
    for name in scope.subjects:
        candidates = identities.get(name.strip().casefold(), set())
        if len(candidates) != 1:
            raise ValueError('scalar subject lacks a unique verified identity')
        subjects.update(int(i) for i in candidates)
    if identities and not scope.subjects:
        raise ValueError('a named individual cannot silently become a league aggregate')
    known_ids = {int(i) for values in identities.values() for i in values}
    if bound_ids and (not subjects <= bound_ids or not bound_ids <= known_ids):
        raise ValueError('bound entity exports do not establish the requested scalar subject')
    if len(subjects) > 1:
        raise ValueError('individual scalar inputs require separate subject cells')
    requested = RequestedAnalyticalContract(
        analytical_role=scope.analytical_role,
        entities=(EntityConstraint(role=scope.analytical_role, ids=tuple(sorted(subjects))),) if subjects else (),
        measures=requested_keys, season=scope.season,
        date_range=scope.date_range.model_dump() if scope.date_range else None,
        game_types=tuple(scope.game_types) if scope.game_types is not None else None,
        qualification=scope.qualification.model_dump() if scope.qualification else None,
        group_by=(), filters=(), limit=None)
    finite_intent = dict(intent, analytical_role=scope.analytical_role, measure=', '.join(requested_keys),
        season=scope.season, population=None, game_types=scope.game_types,
        date_range=scope.date_range.model_dump() if scope.date_range else None,
        qualification=scope.qualification.model_dump() if scope.qualification else None,
        entities=[{'role': scope.analytical_role, 'ids': sorted(subjects), 'names': scope.subjects}] if subjects else [])
    return requested, finite_intent, {**scope.model_dump(mode='json'), 'requested_measures': list(requested_keys)}
