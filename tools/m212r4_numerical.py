"""Fresh mirrored numerical references. Oracle is a separate Python reduction.
Read-only PostgreSQL VALUES; historical-format Parquet consumed directly by DuckDB.
"""
import argparse
import copy
import dataclasses
import datetime as dt
import hashlib
import json
import random
from pathlib import Path

from tools.m212_independent_acceptance import (enc,lit,pg_connection,CandidateProvider,
    FixtureExecutor,exercise,fixed_case)


def run(output,seed):
    import duckdb
    import pyarrow as pa
    import pyarrow.parquet as pq
    from app.capabilities.analytics.catalog import CATALOG, SchemaCatalog
    from app.capabilities.analytics.compiler import SQLCompiler
    from app.capabilities.analytics.conformance import SourceAvailability
    from app.capabilities.analytics.ir import AnalyticalQuery, validate_query
    from app.capabilities.analytics.executor import QueryResult
    rng=random.Random(seed)
    rows=[dict(game_date=dt.date(2025,6,1)+dt.timedelta(days=j%19),game_type=rng.choice(['R','R','S']),
        batter_id=6300100+j%6,pitcher_id=6400100+j%5,game_pk=130000+j//15,
        events=rng.choice([None,'walk','intent_walk','strikeout','strikeout_double_play','double','field_out']),
        stand=rng.choice(['L','R',None]),p_throws=rng.choice(['R','L']),
        pitch_type=rng.choice(['FC','CH','FF',None]),inning=rng.randint(1,11),balls=rng.randint(0,3),strikes=rng.randint(0,2),
        release_speed=rng.choice([None,88.0,92.5,97.25])) for j in range(321)]
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    file=output/'mirror.parquet'
    historical=[{('batter' if k=='batter_id' else 'pitcher' if k=='pitcher_id' else k):v for k,v in r.items()} for r in rows]
    pq.write_table(pa.Table.from_pylist(historical),file)
    cols=list(rows[0]);table='(VALUES '+','.join('('+','.join(('DATE '+lit(v)) if isinstance(v,dt.date) else lit(v) for v in r.values())+')' for r in rows)+') AS r4_fixture('+','.join(cols)+')'
    spec=dataclasses.replace(CATALOG.table('pitch'),physical={'POSTGRES':table,'PARQUET':f"read_parquet({lit(str(file.resolve()))})"})
    catalog=SchemaCatalog({'pitch':spec})
    class Executor:
        def run(self,sql,source):
            if 'player_dictionary' in sql:return QueryResult([],[],0,source,0,'display fixture')
            con=pg_connection() if source=='POSTGRES' else duckdb.connect(':memory:')
            try:
                if source=='POSTGRES':
                    cur=con.cursor();cur.execute(sql);description=cur.description;rs=cur.fetchall()
                else:
                    cur=con.execute(sql);description=cur.description;rs=cur.fetchall()
                names=[d[0] for d in description]
                result=enc([dict(zip(names,r)) for r in rs])
                return QueryResult([dict(name=n,type='fixture') for n in names],result,len(result),source,0,hashlib.sha256(sql.encode()).hexdigest())
            finally:con.close()
        def availability(self,source,relation):
            return SourceAvailability(source=source,relation=relation,known=True,start=dt.date(2025,1,1),end=dt.date(2025,12,31),seasons=(2025,),note='generated fixture-owned availability')
    cases=[dict(role='BATTER',metric='pa'),dict(role='PITCHER',metric='pitches'),
        dict(role='PITCHER',metric='avg_release_speed'),dict(role='BATTER',metric='bb_pct'),
        dict(role='BATTER',metric='pa',group=['stand']),dict(role='BATTER',metric='walks',group=['p_throws']),
        dict(role='PITCHER',metric='pitches',filters=[['strikes','=',1],['inning','>=',6]]),
        dict(role='BATTER',metric='pa',start='2025-06-05',end='2025-06-13'),
        dict(role='BATTER',metric='walks',group=['batter_id'],qualification={'basis':'PA','minimum':17},order=[('walks',True),('batter_id',False)],limit=4),
        dict(role='PITCHER',metric='strikeouts',group=['pitcher_id'],qualification={'basis':'PITCHES','minimum':23},order=[('strikeouts',True),('pitcher_id',False)],limit=3),
        dict(role='PITCHER',metric='avg_release_speed',group=['pitch_type'],qualification={'basis':'GAMES','minimum':4}),
        dict(role='BATTER',metric='pa',filters=[['p_throws','=','L'],['strikes','=',2],['inning','=',8]])]
    results=[]
    for i,s in enumerate(cases):
        selected=[r for r in rows if r['game_type']=='R' and s.get('start','2025-01-01')<=str(r['game_date'])<=s.get('end','2025-12-31')]
        for f,op,v in s.get('filters',[]):selected=[r for r in selected if r[f] is not None and (r[f]==v if op=='=' else r[f]>=v)]
        groups={}
        for r in selected:groups.setdefault(tuple(r[g] for g in s.get('group',[])),[]).append(r)
        if not s.get('group'):groups.setdefault((),[])
        expected=[]
        for keys,rs in groups.items():
            pa_count=sum(r['events'] is not None for r in rs);walks=sum(r['events'] in {'walk','intent_walk'} for r in rs)
            speeds=[r['release_speed'] for r in rs if r['release_speed'] is not None]
            values={'pa':pa_count,'walks':walks,'pitches':len(rs),'strikeouts':sum(r['events'] in {'strikeout','strikeout_double_play'} for r in rs),
                    'bb_pct':100*walks/pa_count if pa_count else None,'avg_release_speed':sum(speeds)/len(speeds) if speeds else None}
            q=s.get('qualification')
            basis={'PA':pa_count,'PITCHES':len(rs),'GAMES':len({r['game_pk'] for r in rs})}
            if q and basis[q['basis']]<q['minimum']:continue
            expected.append({**dict(zip(s.get('group',[]),keys)),s['metric']:values[s['metric']]})
        for f,desc in reversed(s.get('order',[])):
            expected=sorted(expected,key=lambda r:(r[f] is None,-r[f] if desc and r[f] is not None else r[f] or 0))
        if s.get('limit'):expected=expected[:s['limit']]
        c=fixed_case(**s);query=validate_query(AnalyticalQuery(**c['raw']))
        observed={}
        for source in ['POSTGRES','PARQUET']:
            plan=SQLCompiler(catalog).compile(query,source)
            observed[source]=Executor().run(plan.sql,source).rows
        from tools.m212_independent_acceptance import same_rows
        matches={k:same_rows(v,expected,ordered=bool(s.get('order'))) for k,v in observed.items()}
        tool=exercise(c,catalog,Executor())
        results.append(dict(id=i,spec=s,reference=expected,observed=observed,matches=matches,
            tool_correct=same_rows(tool['rows'],expected,ordered=bool(s.get('order'))),judge=tool['judge']['goal_status']))
    result=dict(seed=seed,rows=len(rows),file_hash=hashlib.sha256(file.read_bytes()).hexdigest(),
        cases=len(results),pg_correct=sum(r['matches']['POSTGRES'] for r in results),
        parquet_correct=sum(r['matches']['PARQUET'] for r in results),
        parity=sum(r['matches']['POSTGRES'] and r['matches']['PARQUET'] for r in results),
        tool_correct=sum(r['tool_correct'] for r in results),results=results)
    (output/'results.json').write_text(json.dumps(result,indent=2,ensure_ascii=False,default=str)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='results'}))
    assert all(r['tool_correct'] and all(r['matches'].values()) for r in results)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('output');p.add_argument('--seed',type=int,default=420901);a=p.parse_args();run(a.output,a.seed)
