"""Explicit migration of old Judge fixtures to the M2.12 execution contract.

These are synthetic executed receipts, for Judge unit tests only; no live calculation
claim. Operations come from validated IR, never from a guessed output alias.
"""
import datetime as dt
from app.capabilities.analytics.canonical import build_canonical_query
from app.capabilities.analytics.ir import Aggregate, AnalyticalQuery, validate_query
from app.capabilities.analytics.compiler import SQLCompiler
from app.capabilities.analytics.conformance import (attest, compare, digest, RequestedAnalyticalContract, SourceAvailability, scope_from_execution, scope_from_request)


def with_receipt(artifact):
    scope=artifact.actual_scope
    intent=dict(objective='overall batting performance',season=scope.season,population=scope.game_population,
                entities=[dict(role='BATTER',ids=[int(i) for i in scope.entities])] if scope.entities else [dict(role='BATTER',ids=[999001])])
    query=build_canonical_query(intent)
    if not scope.entities:
        query.entity_filters=[]
    if scope.date_range:
        from app.capabilities.analytics.ir import DateWindow
        query.date_range=DateWindow(**scope.date_range.model_dump())
    keys = set(scope.measures or [m.get('key') for m in artifact.structured_data.get('actual_measures', [])] or list((artifact.structured_data.get('rows') or [{}])[0]))
    needed = set(keys)
    by_name = {d.name:d for d in query.derived}
    def dependencies(name):
        if name not in by_name: return
        for operand in (by_name[name].left,by_name[name].right):
            if operand.kind in {'aggregate','derived'} and operand.name not in needed:
                needed.add(operand.name);dependencies(operand.name)
    for name in list(needed):dependencies(name)
    query.aggregates=[a for a in query.aggregates if a.name in needed]
    query.derived=[d for d in query.derived if d.name in needed]
    if not query.aggregates:
        query.aggregates=[Aggregate(name='pitches',function='COUNT',field='*')]
    query=validate_query(query);compiled=SQLCompiler().compile(query,'POSTGRES')
    available=SourceAvailability(source='POSTGRES',relation='pitch',known=True,start=dt.date(1990,1,1),end=dt.date(2030,12,31),seasons=tuple(range(1990,2031)),note='synthetic fixture availability')
    a=attest(query,compiled,availability=available,executed=True)
    requested=RequestedAnalyticalContract(analytical_role=a.analytical_role,entities=a.entities,season=a.season,date_range=a.date_range,game_types=a.game_types,measures=tuple(scope.measures))
    artifact.actual_scope=scope_from_execution(a)
    artifact.requested_scope=scope_from_request(requested)
    artifact.structured_data.update(sql=compiled.sql,actual_measures=list(a.measures),execution_receipt=dict(validated_ir=query.model_dump(mode="json"),requested=requested.model_dump(mode='json'),attestation=a.model_dump(mode='json'),attestation_digest=digest(a),result_digest=digest(artifact.structured_data.get('rows',[])),conformance=compare(requested,a).model_dump(mode='json')))
    return artifact
