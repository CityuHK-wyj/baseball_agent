"""Ground typed operation proposals against existing governed baseball symbols.

This interprets no user wording. REGULAR_SEASON/POSTSEASON are established catalog
sets, not new aliases or permission inferred from a program/Planner objective.
"""
from copy import deepcopy
from app.capabilities.analytics.catalog import CATALOG
from app.contracts.operations import OperationContract


def ground_operation_contract(raw):
    proposed = deepcopy(raw)
    if not isinstance(proposed,dict):
        raise ValueError('operation proposal must be an object')
    from app.capabilities.analytics.measures import normalize_measure_key
    for operand in proposed.get('operands',[]):
        if not isinstance(operand,dict):
            raise ValueError('invalid operand proposal')
        scope = operand.get('scope')
        if not isinstance(scope,dict):
            continue
        if scope.get('measure') is not None:
            key = normalize_measure_key(scope.get('measure'))
            if key is None:
                raise ValueError('operand measure is outside the governed Measure vocabulary')
            scope['measure'] = key
        if scope.get('game_types') is None:
            continue
        if not isinstance(scope['game_types'],list):
            raise ValueError('competition codes must be a bounded list')
        codes=[]
        for symbol in scope['game_types']:
            if not isinstance(symbol,str):
                raise ValueError('competition symbol must be a string')
            codes.extend(CATALOG.resolve_population(symbol) or [symbol])
        scope['game_types']=sorted(set(codes))
    return OperationContract.model_validate(proposed)


def ground_goal_operation_populations(goal):
    """Fill omitted competition from one independently authoritative Goal scope.

    Explicit operand codes are never changed. Multiple distinct governed populations
    are ambiguous here and do not license an inherited restriction.
    """
    from app.capabilities.analytics.authority import resolve_authority
    authoritative, _, _ = resolve_authority(
        [r.model_dump(mode='json') for r in goal.requirements],
        bound_user_message=goal.raw_query,
        clarification_deltas=[d.model_dump(mode='json') for d in goal.semantic_deltas],
        identity_evidence=[])
    authoritative_ids = {r['id'] for r in authoritative}
    populations = {tuple(sorted(codes)) for r in goal.requirements
        if r.id in authoritative_ids and r.required and r.kind.value == 'GAME_POPULATION'
        for codes in [CATALOG.resolve_population(str(r.value))] if codes}
    if len(populations) != 1:
        return
    codes = list(next(iter(populations)))
    for requirement in goal.requirements:
        if requirement.operation_contract is None:
            continue
        raw = requirement.operation_contract.model_dump(mode='json')
        for operand in raw['operands']:
            if operand['scope'] is not None and operand['scope']['game_types'] is None:
                operand['scope']['game_types'] = codes
        requirement.operation_contract = OperationContract.model_validate(raw)
