#!/usr/bin/env python3
"""Postfreeze independent analytical controls and live answer references (read only)."""
import copy
import dataclasses
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.audit_m211 import OUT, Recorder, dump, encode
from app.config import get_config
from app.capabilities.analytics.catalog import CATALOG, SchemaCatalog, SOURCE_POSTGRES, SOURCE_PARQUET
from app.capabilities.analytics.compiler import SQLCompiler
from app.capabilities.analytics.executor import AnalyticalExecutor, DuckDBExecutor
from app.capabilities.analytics.ir import AnalyticalQuery, validate_query


def main():
    assert (OUT / 'DIAGNOSIS_FROZEN.md').exists()
    cfg = get_config()
    executor = AnalyticalExecutor(cfg.postgres, cfg.parquet.archive_path)
    compiler = SQLCompiler(parquet_archive=cfg.parquet.archive_path)
    rec = Recorder()
    ids = json.loads((OUT/'generated-manifest.json').read_text())['entity_ids']
    # New seeds and requests, generated only after diagnosis was frozen.
    for seed in range(21450, 21462):
        rng = random.Random(seed)
        year = rng.choice([2023, 2024, 2025]); player = rng.choice(ids)
        month = rng.choice([4, 5, 6, 7, 8, 9]); side = rng.choice(['L', 'R'])
        source = SOURCE_PARQUET if year == 2023 else SOURCE_POSTGRES
        q = dict(season=year, population='REGULAR_SEASON', entity_filters=[dict(role='BATTER', ids=[player])],
                 date_range=dict(start=f'{year}-{month:02}-01', end=f'{year}-{month:02}-25'),
                 filters=[dict(op='COMPARE', field='stand', comparator='=', value=side)],
                 aggregates=[dict(name='pa', function='COUNT_NON_NULL', field='events'),
                             dict(name='hr', function='COUNT_IF', condition=dict(op='COMPARE', field='events', comparator='=', value='home_run'))],
                 derived=[dict(name='hr_per_pa', op='SAFE_DIV', left=dict(kind='aggregate', name='hr'), right=dict(kind='aggregate', name='pa'))])
        compiled = compiler.compile(validate_query(AnalyticalQuery.model_validate(q)), source)
        table = 'public.statcast_pitches' if source == SOURCE_POSTGRES else f"read_parquet('{cfg.parquet.archive_path}/mlb_statcast_{year}.parquet')"
        identity = 'batter_id' if source == SOURCE_POSTGRES else 'batter'
        reference_sql = f"SELECT COUNT(events) pa, SUM(CASE WHEN events='home_run' THEN 1 ELSE 0 END) hr, 1.0*SUM(CASE WHEN events='home_run' THEN 1 ELSE 0 END)/NULLIF(COUNT(events),0) hr_per_pa FROM {table} WHERE {identity}={player} AND game_type='R' AND stand='{side}' AND game_date BETWEEN '{year}-{month:02}-01' AND '{year}-{month:02}-25'"
        actual = executor.run(compiled.sql, source); expected = executor.run(reference_sql, source)
        if actual.error or expected.error:
            raise RuntimeError(f'{seed}: execution unavailable: {actual.error or expected.error}')
        rec.check(f'holdout_ir_{seed}', actual.rows, expected.rows, 'postfreeze_independent_'+source,
                  seed=seed, ir=q, compiled_sql=compiled.sql, reference_sql=reference_sql, execution_error=actual.error)
    # Fixed question references use independently specified identities, not model SQL.
    for name, year, player, event in [('smith_hr_2024',2024,669257,'hr'), ('harper_hits_2023',2023,547180,'hits'),
                                     ('harper_hr_2023',2023,547180,'hr'), ('harper_hr_2024',2024,547180,'hr'),
                                     ('ohtani_hr_2023',2023,660271,'hr'), ('ohtani_hr_2024',2024,660271,'hr')]:
        source = SOURCE_PARQUET if year == 2023 else SOURCE_POSTGRES
        table = 'public.statcast_pitches' if source == SOURCE_POSTGRES else f"read_parquet('{cfg.parquet.archive_path}/mlb_statcast_{year}.parquet')"
        identity = 'batter_id' if source == SOURCE_POSTGRES else 'batter'
        predicate = "events='home_run'" if event == 'hr' else "events IN ('single','double','triple','home_run')"
        sql = f"SELECT COUNT(*) AS value FROM {table} WHERE {identity}={player} AND game_type='R' AND EXTRACT(YEAR FROM game_date)={year} AND {predicate}"
        result = executor.run(sql,source)
        rec.rows.append(dict(name=name,category='live_reference',ok=not bool(result.error),sql=sql,result=encode(result)))
    for name, sql in [
        ('smith_recent', "SELECT COUNT(*) AS value FROM public.statcast_pitches WHERE batter_id=669257 AND game_date BETWEEN '2026-08-31' AND '2026-09-14'"),
        ('ohtani_splits', "SELECT p_throws,COUNT(*) FILTER(WHERE events IN ('single','double','triple','home_run')) AS hits,COUNT(*) FILTER(WHERE events IN ('single','double','triple','home_run','strikeout','field_out','force_out','grounded_into_double_play','strikeout_double_play','fielders_choice','fielders_choice_out','double_play','triple_play','field_error')) AS ab FROM public.statcast_pitches WHERE batter_id=660271 AND game_type='R' AND EXTRACT(YEAR FROM game_date)=2025 GROUP BY p_throws ORDER BY p_throws"),
        ('qualified_hr_ranking', "SELECT batter_id,COUNT(*) FILTER(WHERE events='home_run') AS hr,COUNT(events) AS pa FROM public.statcast_pitches WHERE game_type='R' AND EXTRACT(YEAR FROM game_date)=2025 GROUP BY batter_id HAVING COUNT(events)>=300 ORDER BY hr DESC,batter_id ASC LIMIT 3")]:
        result=executor.run(sql,SOURCE_POSTGRES)
        rec.rows.append(dict(name=name,category='live_reference',ok=not bool(result.error),sql=sql,result=encode(result)))
    # Extend mirrored-data engine parity to dates, ordering, qualification and rates.
    mirror='/tmp/m211-parity/mlb_statcast_mirror.parquet'
    spec=CATALOG.table('pitch')
    catalog=SchemaCatalog({'pitch':dataclasses.replace(spec,physical={**spec.physical,SOURCE_PARQUET:f"read_parquet('{mirror}')"},key_map={**spec.key_map,SOURCE_PARQUET:spec.key_map[SOURCE_POSTGRES]})})
    duck_compiler=SQLCompiler(catalog,parquet_archive='/tmp/m211-parity'); duck=DuckDBExecutor('/tmp/m211-parity')
    base=dict(date_range=dict(start='2024-06-01',end='2024-06-03'),population='REGULAR_SEASON',group_by=['batter_id'],
              aggregates=[dict(name='pa',function='COUNT_NON_NULL',field='events'),dict(name='hr',function='COUNT_IF',condition=dict(op='COMPARE',field='events',comparator='=',value='home_run'))],
              sort=[dict(name='pa',direction='DESC'),dict(name='batter_id',direction='ASC')],limit=10,qualification=dict(basis='PA',minimum=5))
    cases=[('qualified_rank',base),('window',dict(base,date_range=dict(start='2024-06-02',end='2024-06-03'))),
           ('rate',dict(base,derived=[dict(name='hr_rate',op='SAFE_DIV',left=dict(kind='aggregate',name='hr'),right=dict(kind='aggregate',name='pa'))]))]
    for name, raw in cases:
        q=validate_query(AnalyticalQuery.model_validate(raw));p=compiler.compile(q,SOURCE_POSTGRES);d=duck_compiler.compile(q,SOURCE_PARQUET)
        a=executor.run(p.sql,SOURCE_POSTGRES);b=duck.run(d.sql)
        if a.error or b.error:
            raise RuntimeError(f'{name}: parity execution unavailable: {a.error or b.error}')
        rec.check(name,a.rows,b.rows,'postfreeze_extended_parity',pg_sql=p.sql,duck_sql=d.sql)
    # NULL ordering is explicitly observed; don't assert identical engine defaults.
    pg=executor.run('SELECT v FROM (VALUES (NULL::numeric),(1::numeric),(2::numeric)) AS t(v) ORDER BY v DESC',SOURCE_POSTGRES)
    dq=duck.run('SELECT v FROM (VALUES (NULL::DOUBLE),(1::DOUBLE),(2::DOUBLE)) AS t(v) ORDER BY v DESC')
    rec.rows.append(dict(name='null_sort_defaults',category='engine_difference',ok=True,
                         postgres=encode(pg),duckdb=encode(dq),intentional_engine_default=True,
                         note='Compiler emits no NULLS FIRST/LAST; null-valued rankings can differ. Holdout validation observation.'))
    # Catalog includes a player dictionary as well as pitch. Inspect relation safety,
    # explicit postseason population, categorical pitches and non-PA qualification.
    edges=[
        ('player_dictionary',dict(relation='player_dictionary',aggregates=[dict(name='n',function='COUNT',field='*')]),True),
        ('player_dictionary_season',dict(relation='player_dictionary',season=2024,aggregates=[dict(name='n',function='COUNT',field='*')]),False),
        ('unknown_table',dict(relation='invented_table',aggregates=[dict(name='n',function='COUNT',field='*')]),False),
        ('cross_table_field',dict(relation='pitch',group_by=['player_id'],aggregates=[dict(name='n',function='COUNT',field='*')]),False),
        ('postseason',dict(season=2024,population='POSTSEASON',aggregates=[dict(name='n',function='COUNT',field='*')]),True),
        ('pitch_category',dict(season=2024,filters=[dict(op='COMPARE',field='pitch_type',comparator='=',value='FF')],aggregates=[dict(name='n',function='COUNT',field='*')]),True),
    ]
    for name,raw,want in edges:
        try:
            q=validate_query(AnalyticalQuery.model_validate(raw));sql=compiler.compile(q,SOURCE_POSTGRES).sql
            result=executor.run(sql,SOURCE_POSTGRES)
            observed=dict(validated=True,sql=sql,execution_error=result.error,rows=result.rows)
            safe_success=not bool(result.error)
        except Exception as error:
            observed=dict(validated=False,error=type(error).__name__+': '+str(error));safe_success=False
        if name=='player_dictionary_season':
            # Invalid relation-specific scope must be rejected BEFORE emitting invalid SQL.
            rec.check(name,observed['validated'],False,'postfreeze_schema_boundary',observed=observed)
        else:rec.check(name,safe_success,want,'postfreeze_schema_boundary',observed=observed)
    for basis,reference in [('BBE','COUNT(launch_speed)'),('GAMES','COUNT(DISTINCT game_pk)'),('PITCHES','COUNT(*)')]:
        raw=dict(season=2024,population='REGULAR_SEASON',group_by=['batter_id'],qualification=dict(basis=basis,minimum=50),aggregates=[dict(name='n',function='COUNT',field='*')],sort=[dict(name='batter_id',direction='ASC')],limit=8)
        q=validate_query(AnalyticalQuery.model_validate(raw));sql=compiler.compile(q,SOURCE_POSTGRES).sql
        direct=f"SELECT batter_id,COUNT(*) AS n FROM public.statcast_pitches WHERE game_type='R' AND EXTRACT(YEAR FROM game_date)=2024 GROUP BY batter_id HAVING {reference}>=50 ORDER BY batter_id ASC LIMIT 8"
        a=executor.run(sql,SOURCE_POSTGRES);b=executor.run(direct,SOURCE_POSTGRES)
        if a.error or b.error:raise RuntimeError(a.error or b.error)
        rec.check('qualification_'+basis,a.rows,b.rows,'postfreeze_qualification_bases',compiled_sql=sql,reference_sql=direct)
    rec.save('holdout-reference.json')


if __name__=='__main__':main()
