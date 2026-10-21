#!/usr/bin/env python3
"""Read-only M2.12 development/frozen reference and mirrored parity evaluation."""
import copy, dataclasses, datetime as dt, hashlib, json, random, subprocess, sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.m212_cases import case, mutations, reference
from tools.m212_evaluation_support import equal, encode
from app.config import get_config
from app.capabilities.analytics.catalog import CATALOG, SchemaCatalog
from app.capabilities.analytics.compiler import SQLCompiler, render_literal
from app.capabilities.analytics.executor import AnalyticalExecutor, DuckDBExecutor, QueryResult
from app.capabilities.analytics.ir import AnalyticalQuery, validate_query
from app.capabilities.analytics.tool import LocalAnalyticsTool
from app.capabilities.analytics.conformance import attest, compare, requested_contract, SourceAvailability, receipt_usable
from app.contracts.models import ToolRequest, Goal, UserRequirement, RequirementKind, Artifact, Scope
from app.verification.judge import Judge
from app.semantic.goals import RequirementBuilder
from app.contracts.models import SemanticInterpretation, EntityMention, AnalysisRequest, Constraint

OUT=Path('docs/audit/m212');OUT.mkdir(exist_ok=True,parents=True)
class IR:
    configured=True
    def __init__(self,q):self.q=q
    def complete_json(self,**kw):return SimpleNamespace(data=copy.deepcopy(self.q))

def main(stage):
    cfg=get_config();executor=AnalyticalExecutor(cfg.postgres,cfg.parquet.archive_path)
    assert not executor.run('SELECT 1 AS ok','POSTGRES').error, 'PostgreSQL unavailable'
    frozen=json.loads((OUT/'IMPLEMENTATION_FROZEN.json').read_text()) if stage=='holdout' else None
    sha=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    if frozen:
        for path,expected in frozen['production_hashes'].items():assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==expected,'production changed after freeze'
    start,count=(21213000,32) if stage=='development' else (21294000,40)
    records=[]
    def check(category,name,actual,expected,**detail):
        records.append(dict(category=category,name=name,ok=equal(actual,expected),actual=encode(actual),expected=encode(expected),**encode(detail)))
    # Independently seeded invariant/mutation population. Synthetic source is mirrored in
    # a PostgreSQL VALUES relation and an identical Parquet file, without any DB writes.
    mirror=Path('/tmp/m212-mirror-'+stage);mirror.mkdir(exist_ok=True)
    for seed in range(start,start+count):
        c=case(seed);columns=list(c['rows'][0]);values=', '.join('('+', '.join(('DATE '+render_literal(row[k])) if isinstance(row[k],dt.date) else render_literal(row[k]) for k in columns)+')' for row in c['rows'])
        physical=f'(VALUES {values}) AS fixture('+', '.join(columns)+')'
        import duckdb
        db=duckdb.connect(':memory:');db.execute('CREATE TABLE fixture AS SELECT * FROM '+physical)
        path=mirror/f'mlb_statcast_{c["raw"]["season"]}.parquet';db.execute(f"COPY fixture TO '{path}' (FORMAT PARQUET)");db.close()
        spec=dataclasses.replace(CATALOG.table('pitch'),physical={'POSTGRES':physical,'PARQUET':f"read_parquet('{path}')"},key_map={'POSTGRES':CATALOG.table('pitch').key_map['POSTGRES'],'PARQUET':CATALOG.table('pitch').key_map['POSTGRES']})
        catalog=SchemaCatalog({'pitch':spec});compiler=SQLCompiler(catalog);query=validate_query(AnalyticalQuery.model_validate(c['raw']))
        request=ToolRequest(need_id='analytic',capability='local_analytics',objective='Report batting average',inputs=c['inputs'])
        tool=LocalAnalyticsTool(executor,IR(c['raw']));tool.catalog=catalog;tool.compiler=compiler
        wanted=requested_contract(tool._intent(request),request.inputs,tool._requested_measures(request,tool._intent(request)))
        results={}
        for source in ['POSTGRES','PARQUET']:
            compiled=compiler.compile(query,source)
            a=attest(query,compiled,availability=SourceAvailability(source=source,relation='pitch',known=True,start=dt.date(query.season,1,1),end=dt.date(query.season,12,31),seasons=(query.season,)),executed=True)
            check('correct_match',f'{seed}-{source}',compare(wanted,a).status,'MATCH',seed=seed)
            result=executor.postgres.run(compiled.sql) if source=='POSTGRES' else DuckDBExecutor(str(mirror)).run(compiled.sql)
            check('compile_execution',f'{seed}-{source}',result.error,'',seed=seed)
            check('mirror_reference_'+source,f'{seed}-{source}',result.rows,reference(c),seed=seed,ir=c['raw'],reference='independent Python source-row reduction',sql=compiled.sql)
            results[source]=result.rows
        check('parity',str(seed),bool(results['POSTGRES']) and equal(results['POSTGRES'],results['PARQUET']),True,seed=seed,source_digest=hashlib.sha256(path.read_bytes()).hexdigest())
        for name,raw in mutations(c).items():
            q=validate_query(AnalyticalQuery.model_validate(raw));a=attest(q,compiler.compile(q,'POSTGRES'))
            check('drift_'+name,str(seed),compare(wanted,a,check_availability=False).status,'MISMATCH',seed=seed,ir=raw)
            tool.llm=IR(raw);r=tool.execute(request)
            check('drift_tool_'+name,str(seed),r.outcome.code,'ANALYTICAL_NONCONFORMANCE',seed=seed)
            projection=Judge().evaluate(Goal(statement='AVG',raw_query='AVG',requirements=[UserRequirement(kind=RequirementKind.MEASURE,statement='AVG',value={'key':'avg','derivable':True})]),[r.artifact] if r.artifact else [],[])
            check('incorrect_complete',f'{seed}-{name}',projection.goal_status.value=='COMPLETE',False,seed=seed)
        # Actual query prefix with tie breaker, nulls last, a rate, and qualification.
        for n in [1,2]:
            q=query.model_copy(update={'limit':n});rr=executor.postgres.run(compiler.compile(q,'POSTGRES').sql)
            check('top_n_prefix',f'{seed}-{n}',rr.rows,results['POSTGRES'][:n],seed=seed)
        # A shorter observed availability must remain partial.
        a=attest(query,compiler.compile(query,'POSTGRES'),availability=SourceAvailability(source='POSTGRES',relation='pitch',known=True,start=query.date_range.start+dt.timedelta(days=1),end=query.date_range.end,seasons=(query.season,)),executed=True)
        check('partial_coverage',str(seed),compare(wanted,a).status,'PARTIAL',seed=seed)
        print(stage,'mirror',seed,flush=True)
    # Fresh real-source queries, with separate directly authored SQL references.
    excluded=set(json.loads(Path('docs/audit/m212/legacy-inputs/generated-manifest.json').read_text())['entity_ids'])
    sample=executor.run("SELECT batter_id,COUNT(events) n FROM public.statcast_pitches WHERE game_date BETWEEN '2024-05-01' AND '2024-08-31' GROUP BY batter_id ORDER BY n DESC,batter_id ASC LIMIT 80",'POSTGRES')
    ids=[x['batter_id'] for x in sample.rows if x['batter_id'] not in excluded][20:32]
    for seed in range(start,start+count):
        rng=random.Random(seed);year=rng.choice([2018,2021,2022,2024,2025]);player=rng.choice(ids);month=rng.choice([4,5,7,8]);day=rng.randint(3,8)
        source='PARQUET' if year<2024 else 'POSTGRES';a=f'{year}-{month:02}-{day:02}';b=f'{year}-{month:02}-{day+17:02}';group=bool(seed%2);minimum=rng.choice([0,3,7])
        raw=dict(season=year,population='REGULAR_SEASON',entity_filters=[dict(role='BATTER',ids=[player])],date_range=dict(start=a,end=b),group_by=['p_throws'] if group else [],qualification=dict(basis='PA',minimum=minimum),
                 aggregates=[dict(name='appearances',function='COUNT_NON_NULL',field='events'),dict(name='contacts',function='COUNT_IF',condition=dict(op='IN',field='events',values=['single','double','triple','home_run'])),dict(name='speed',function='AVG',field='release_speed')],
                 derived=[dict(name='contact_rate',op='SAFE_DIV',left=dict(kind='aggregate',name='contacts'),right=dict(kind='aggregate',name='appearances'))],sort=[dict(name='p_throws',direction='ASC')] if group else [])
        inputs=dict(intent=dict(objective='Report plate appearances and average release speed',season=year,population='REGULAR_SEASON',date_range=raw['date_range'],qualification=raw['qualification']),entity_sets=[dict(role='BATTER',ids=[player])],require_entities=True)
        tool=LocalAnalyticsTool(executor,IR(raw),parquet_archive=cfg.parquet.archive_path)
        result=tool.execute(ToolRequest(need_id='a',capability='local_analytics',objective='Report plate appearances and average release speed',inputs=inputs))
        table='public.statcast_pitches' if source=='POSTGRES' else f"read_parquet('{cfg.parquet.archive_path}/mlb_statcast_{year}.parquet')";key='batter_id' if source=='POSTGRES' else 'batter'
        ref_sql=f"SELECT {'p_throws,' if group else ''}COUNT(events) appearances,SUM(CASE WHEN events IN ('single','double','triple','home_run') THEN 1 ELSE 0 END) contacts,AVG(release_speed) speed,1.0*SUM(CASE WHEN events IN ('single','double','triple','home_run') THEN 1 ELSE 0 END)/NULLIF(COUNT(events),0) contact_rate FROM {table} WHERE {key}={player} AND game_type='R' AND game_date BETWEEN '{a}' AND '{b}'"+(' GROUP BY p_throws' if group else '')+f' HAVING COUNT(events)>={minimum}'+(' ORDER BY p_throws ASC NULLS LAST' if group else '')
        ref=executor.run(ref_sql,source)
        check('real_source_'+source,str(seed),result.artifact.structured_data['rows'] if result.artifact else None,ref.rows,seed=seed,ir=raw,reference_sql=ref_sql,reference_error=ref.error,outcome=encode(result.outcome))
        check('valid_request_execution',str(seed),result.artifact is not None,True,seed=seed)
        check('lineage',str(seed),receipt_usable(result.artifact) if result.artifact else False,True,seed=seed)
        print(stage,'real',seed,flush=True)
    summary={}
    for row in records:
        counter=summary.setdefault(row['category'],dict(passed=0,failed=0));counter['passed' if row['ok'] else 'failed']+=1
    (OUT/(stage+'-analytics.json')).write_text(json.dumps(dict(sha=sha,seeds=list(range(start,start+count)),summary=summary,checks=records),indent=2,default=str)+'\n')
    print(json.dumps(summary),flush=True)

if __name__=='__main__':main(sys.argv[1])
