"""Ground finite observation scopes; never interpret user wording or approve facts."""
from copy import deepcopy
from app.capabilities.analytics.catalog import CATALOG
from app.contracts.reporting import ReportingContract


def ground_reporting_contract(raw):
    proposed = deepcopy(raw)
    if not isinstance(proposed, dict):
        raise ValueError('reporting contract must be an object')
    from app.capabilities.analytics.measures import normalize_measure_key
    for scope in proposed.get('cells', []):
        if not isinstance(scope, dict):
            raise ValueError('reporting cell must declare a complete scalar scope')
        key = normalize_measure_key(scope.get('measure'))
        if key is None:
            raise ValueError('reported measure is outside the governed Measure vocabulary')
        scope['measure'] = key
        if scope.get('game_types') is not None:
            if not isinstance(scope['game_types'], list) or any(type(c) is not str for c in scope['game_types']):
                raise ValueError('competition symbols must be a list of strings')
            scope['game_types'] = sorted({c for symbol in scope['game_types']
                for c in (CATALOG.resolve_population(symbol) or [symbol])})
    return ReportingContract.model_validate(proposed)


def scalar_scope_conflict(owner, scope):
    """Retain independently groundable source facets; a typed shape cannot erase them.

    The existing domain scope resolver is a contradiction check, not a new vocabulary
    or a declaration that arbitrary remaining language is semantically understood.
    Unknown/unsupported facets proposed by interpretation remain CONSTRAINT owners.
    """
    from app.capabilities.analytics.conformance import constraint_coverage
    value = owner.value if isinstance(owner.value, dict) else {}
    source = owner.bound_source_clause or value.get('source_text') or owner.original_intent or ''
    if owner.reporting_contract:
        from app.capabilities.analytics.measures import resolve_texts
        known_measures = {m.key for m in resolve_texts([source]) if m.matched}
        if known_measures and scope.measure not in known_measures:
            return 'reported measure contradicts the independently grounded source metric'
    coverage = constraint_coverage(source, role=scope.analytical_role)
    observed = coverage.get('obligations') or {}
    if coverage.get('unsupported'):
        return 'source contains an unsupported analytical restriction'
    if any(observed.get(key) for key in ('filters', 'group_by', 'ordering', 'limit')):
        return 'source requires a filtered/grouped/ordered/limited scope extension'
    if observed.get('qualification') and observed['qualification'] != (
            scope.qualification.model_dump() if scope.qualification else None):
        return 'source qualification is not represented by the scalar scope'
    if observed.get('analytical_role') and observed['analytical_role'] != scope.analytical_role:
        return 'source role differs from the scalar scope'
    resolved = owner.resolved_scope
    if (owner.reporting_contract and resolved
            and resolved.confirmed and resolved.start and resolved.end and resolved.original_text in source
            and (scope.date_range is None or scope.date_range.model_dump() !=
                 {'start': resolved.start, 'end': resolved.end})):
        return 'source date interval is not represented by the scalar scope'
    return ''


def ground_goal_reporting_populations(goal):
    from app.capabilities.analytics.authority import resolve_authority
    authoritative, _, _ = resolve_authority(
        [r.model_dump(mode='json') for r in goal.requirements], bound_user_message=goal.raw_query,
        clarification_deltas=[d.model_dump(mode='json') for d in goal.semantic_deltas], identity_evidence=[])
    ids = {r['id'] for r in authoritative}
    populations = {tuple(sorted(codes)) for r in goal.requirements
        if r.id in ids and r.required and r.kind.value == 'GAME_POPULATION'
        for codes in [CATALOG.resolve_population(str(r.value))] if codes}
    if len(populations) != 1:
        return
    for owner in goal.requirements:
        if owner.reporting_contract is None:
            continue
        raw = owner.reporting_contract.model_dump(mode='json')
        for scope in raw['cells']:
            if scope['game_types'] is None:
                scope['game_types'] = list(next(iter(populations)))
        owner.reporting_contract = ReportingContract.model_validate(raw)
