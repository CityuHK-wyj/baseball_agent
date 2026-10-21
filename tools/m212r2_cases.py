"""New R2 populations: Requirement facts are sampled before Needs and candidate IR.

No legacy seeds or named frozen strings. Reference reductions below use independently
specified event sets and arithmetic; no production Artifact is ground truth.
"""
import copy
import datetime as dt
import random

DEV_PROVENANCE=range(2128401000,2128401024)
DEV_COMPOUND=range(2128402000,2128402024)
DEV_EQUIVALENCE=range(2128403000,2128403024)


def case(seed):
    rng=random.Random(seed)
    role=rng.choice(['BATTER','PITCHER']);year=rng.choice([2017,2018,2020,2022,2024,2025])
    field='batter_id' if role=='BATTER' else 'pitcher_id'
    start=dt.date(year,rng.choice([5,7,8]),rng.randint(3,13));end=start+dt.timedelta(days=rng.randint(8,16))
    population,codes=rng.choice([('REGULAR_SEASON',['R']),('WORLD_SERIES',['W']),('WILDCARD',['F'])])
    metric,column,operation=rng.choice([('walks','walk_total',dict(function='COUNT_IF',condition=dict(op='IN',field='events',values=['intent_walk','walk']))),('strikeouts','strike_total',dict(function='COUNT_IF',condition=dict(op='IN',field='events',values=['strikeout_double_play','strikeout']))),('avg_release_speed','velocity',dict(function='AVG',field='release_speed'))])
    shape=seed%4;group=[field] if shape==2 else [rng.choice(['pitch_type','stand','inning'])] if shape==1 else []
    limit=rng.randint(2,4) if shape==2 else None
    direction=rng.choice(['ASC','DESC']);qual=dict(basis=rng.choice(['PA','PITCHES','BBE']),minimum=rng.randint(1,4)) if shape==3 else None
    predicate=dict(op='IN',field=rng.choice(['pitch_type','stand','p_throws']),values=rng.sample(['FC','ST','CU'],2))
    if predicate['field']!='pitch_type':predicate['values']=['R']
    filters=[predicate] if shape==3 else []
    reqs=[]
    def req(kind,value,statement):
        reqs.append(dict(id=f'r2_{seed}_{len(reqs)}',kind=kind,value=copy.deepcopy(value),statement=statement,required=True,source='user'))
    req('SEASON',year,'Bound season');req('TIME',dict(start=start.isoformat(),end=end.isoformat()),'Exact inclusive window');req('GAME_POPULATION',population,'Bound competition')
    req('MEASURE',dict(key=metric,derivable=True),metric);req('MEASURE',dict(key='pa',derivable=True),'Plate appearances')
    req('ANALYSIS',dict(objective='Calculate pitching metrics' if role=='PITCHER' else 'Calculate batting metrics'),'Calculate requested metrics')
    req('CONSTRAINT',dict(analytical_role=role,group_by=group,filters=filters,**({'limit':limit,'ordering':[dict(metric=metric,direction=direction)]} if limit else {}),**({'qualification':qual} if qual else {})),'Mandatory analytical dimensions')
    # Only after fixing Requirements, build execution instructions and the candidate.
    raw=dict(analytical_role=role,season=year,date_range=dict(start=start.isoformat(),end=end.isoformat()),population=population,
             filters=copy.deepcopy(filters),group_by=group,aggregates=[dict(name=column,**operation),dict(name='sample',function='COUNT_NON_NULL',field='events')],
             sort=[dict(name=column,direction=direction),dict(name=field,direction='ASC')] if limit else [dict(name=group[0],direction='ASC')] if group else [],limit=limit,qualification=qual)
    inputs=dict(analytical_requirements=copy.deepcopy(reqs),raw_query='Calculate pitching metrics' if role=='PITCHER' else 'Calculate batting metrics',
                intent=dict(objective='Calculate the metric',season=year,population=population,date_range=raw['date_range']),entity_role=role,
                requested_measures=[dict(key=metric),dict(key='pa')],constraints={})
    ids=rng.sample(range(2100001,3900000),6)
    rows=[]
    for j in range(240):
        rows.append(dict(game_date=start+dt.timedelta(days=rng.randint(-2,(end-start).days+2)),game_type=rng.choice([*codes,'S']),
                         batter_id=rng.choice(ids[:3]),pitcher_id=rng.choice(ids[3:]),events=rng.choice([None,'intent_walk','walk','single','field_out','strikeout_double_play','strikeout']),
                         release_speed=rng.choice([None,78.5,89.75,97.25]),launch_speed=rng.choice([None,90.,101.]),
                         pitch_type=rng.choice(['FC','ST','CU','FF']),stand=rng.choice(['L','R',None]),p_throws=rng.choice(['R','L',None]),inning=rng.randint(1,12),strikes=rng.randint(0,2),balls=rng.randint(0,3),game_pk=j//9))
    return dict(seed=seed,requirements=reqs,raw=raw,inputs=inputs,rows=rows,codes=codes,identity=field,metric=metric,column=column)


def reference(c):
    q=c['raw'];groups={}
    for row in c['rows']:
        if row['game_type'] not in c['codes'] or not q['date_range']['start']<=row['game_date'].isoformat()<=q['date_range']['end']:continue
        if any(row[p['field']] is None or row[p['field']] not in p['values'] for p in q['filters']):continue
        key=tuple(row[g] for g in q['group_by']);groups.setdefault(key,[]).append(row)
    if not q['group_by'] and not groups:groups[()]=[]
    result=[]
    for key,rows in groups.items():
        pa=sum(x['events'] is not None for x in rows)
        counts={'PA':pa,'PITCHES':len(rows),'BBE':sum(x['launch_speed'] is not None for x in rows)}
        if q['qualification'] and counts[q['qualification']['basis']]<q['qualification']['minimum']:continue
        values=[x['release_speed'] for x in rows if x['release_speed'] is not None]
        value=sum(x['events'] in {'walk','intent_walk'} for x in rows) if c['metric']=='walks' else sum(x['events'] in {'strikeout','strikeout_double_play'} for x in rows) if c['metric']=='strikeouts' else sum(values)/len(values) if values else None
        result.append({**dict(zip(q['group_by'],key)),c['column']:value,'sample':pa})
    for sort in reversed(q['sort']):
        k=sort['name'];nonnull=[r for r in result if r[k] is not None];null=[r for r in result if r[k] is None]
        result=sorted(nonnull,key=lambda x:x[k],reverse=sort['direction']=='DESC')+null
    return result[:q['limit']] if q['limit'] else result


def need_attacks(c):
    q=c['raw'];identity=c['identity'];other='PITCHER' if q['analytical_role']=='BATTER' else 'BATTER'
    def item(name,metadata,patch):
        ins=copy.deepcopy(c['inputs']);ins['constraints'].update(metadata);ins['intent'].update(metadata)
        raw=copy.deepcopy(q);raw.update(patch)
        return dict(name=name,inputs=ins,ir=raw)
    ordering=[dict(metric='pa',direction='ASC')];group=[identity] if q['group_by']!=[identity] else ['pitch_type']
    group_raw=dict(group_by=group,sort=[],limit=None)
    filt=[*q['filters'],dict(op='COMPARE',field='balls',comparator='>=',value=2)]
    date_range={**q['date_range'],'start':(dt.date.fromisoformat(q['date_range']['start'])+dt.timedelta(days=2)).isoformat()}
    attacks=[item('Need_injected_filter',dict(filters=filt),dict(filters=filt)),item('Need_injected_grouping',dict(group_by=group),group_raw),
             item('Need_injected_qualification',dict(qualification=dict(basis='GAMES',minimum=9)),dict(qualification=dict(basis='GAMES',minimum=9))),
             item('Need_injected_role',dict(analytical_role=other,entity_role=other),dict(analytical_role=other)),
             item('Need_injected_temporal',dict(date_range=date_range),dict(date_range=date_range)),
             item('Need_injected_population',dict(game_types=['S'],population='SPRING_TRAINING'),dict(population='SPRING_TRAINING')),
             item('Need_injected_split',dict(group_by=[*q['group_by'],'p_throws']),dict(group_by=[*q['group_by'],'p_throws'])),
             item('Need_injected_measure',dict(measure='pitches'),dict(aggregates=[dict(name='pitches',function='COUNT',field='*')],sort=[])),
             item('Need_injected_ordering',dict(ordering=ordering),dict(sort=[dict(name='sample',direction='ASC')]))]
    if q['group_by']:
        attacks.append(item('Need_injected_limit',dict(limit=(q['limit'] or 0)+1),dict(limit=(q['limit'] or 0)+1)))
    return attacks
