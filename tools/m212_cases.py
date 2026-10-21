"""Reproducible M2.12 invariant cases. No production import or expected-value injection."""
from tools.analytical_requirement_fixture import fixture_inputs
import copy
import datetime as dt
import random
from app.capabilities.analytics.catalog import HIT_EVENTS, NON_AT_BAT_EVENTS

DEV_SEEDS = range(21212000, 21212048)

def case(seed):
    r = random.Random(seed)
    year = r.choice([2018, 2020, 2021, 2022, 2024, 2025])
    role = r.choice(['BATTER', 'PITCHER']); identity = 'batter_id' if role == 'BATTER' else 'pitcher_id'
    ids = r.sample(range(730000, 940000), 4)
    start = dt.date(year,r.choice([4,5,7,8]),r.randint(2,8)); end = start + dt.timedelta(days=9)
    pop = r.choice(['REGULAR_SEASON','WORLD_SERIES','SPRING_TRAINING'])
    code = {'REGULAR_SEASON':'R','WORLD_SERIES':'W','SPRING_TRAINING':'S'}[pop]
    basis = r.choice(['PA','BBE','PITCHES','GAMES']); minimum=r.randint(1,3)
    aggregates = [dict(name='volume',function='COUNT_NON_NULL',field='events'),
                  dict(name='numerator',function='COUNT_IF',condition=dict(op='IN',field='events',values=list(HIT_EVENTS))),
                  dict(name='denominator',function='COUNT_IF',condition=dict(op='AND',conditions=[dict(op='IS_NOT_NULL',field='events'),dict(op='NOT',conditions=[dict(op='IN',field='events',values=list(NON_AT_BAT_EVENTS))])])),
                  dict(name='velocity',function='AVG',field='release_speed')]
    raw=dict(relation='pitch',entity_filters=[dict(role=role,ids=[ids[0]])],season=year,population=pop,
             date_range=dict(start=start.isoformat(),end=end.isoformat()),group_by=[identity,'p_throws'],aggregates=aggregates,
             derived=[dict(name='rate_value',op='SAFE_DIV',left=dict(kind='aggregate',name='numerator'),right=dict(kind='aggregate',name='denominator'))],
             sort=[dict(name='rate_value',direction='DESC'),dict(name=identity,direction='ASC'),dict(name='p_throws',direction='ASC')],limit=r.randint(2,5),qualification=dict(basis=basis,minimum=minimum))
    inputs=dict(intent=dict(objective='Report batting average and average release speed in grouped splits',season=year,population=pop,date_range=raw['date_range'],qualification=raw['qualification']),
                entity_sets=[dict(role=role,ids=[str(ids[0])],namespace='MLBAM')],entity_role=role,require_entities=True,
                requested_measures=[dict(key='avg'),dict(key='avg_release_speed')],
                constraints=dict(group_by=raw['group_by'],ordering=[dict(metric='avg',direction='DESC'),dict(field=identity,direction='ASC'),dict(field='p_throws',direction='ASC')],limit=raw['limit']))
    rows=[]
    events=[None,'walk','intent_walk','home_run','single','double','strikeout','field_out','hit_by_pitch','sac_fly']
    for j in range(180):
        rows.append(dict(batter_id=ids[0] if j%3 else ids[1],pitcher_id=ids[0] if j%4 else ids[2],
                         game_date=start+dt.timedelta(days=r.randint(-2,12)),game_type=code if j%4 else 'D',
                         events=r.choice(events),release_speed=r.choice([None,80.,89.5,95.,98.25]),
                         launch_speed=r.choice([None,85.,95.,102.]),p_throws=r.choice(['L','R',None]),game_pk=j//12))
    inputs = fixture_inputs(inputs)
    return dict(seed=seed,raw=raw,inputs=inputs,rows=rows,identity=identity,code=code)


def mutations(c):
    q=c['raw']; out={}
    def edit(label,fn):
        x=copy.deepcopy(q);fn(x);out[label]=x
    edit('wrong_entity',lambda x:x['entity_filters'][0].update(ids=[int(c['inputs']['entity_sets'][0]['ids'][0])+17]))
    edit('missing_entity',lambda x:x.update(entity_filters=[]))
    edit('additional_entity',lambda x:x['entity_filters'][0]['ids'].append(int(c['inputs']['entity_sets'][0]['ids'][0])+19))
    edit('wrong_role',lambda x:x['entity_filters'][0].update(role='PITCHER' if x['entity_filters'][0]['role']=='BATTER' else 'BATTER'))
    edit('population_expand',lambda x:x.update(game_types=['D']))
    edit('population_remove',lambda x:x.update(population=None))
    edit('time_shift',lambda x:x['date_range'].update(start=(dt.date.fromisoformat(x['date_range']['start'])+dt.timedelta(days=1)).isoformat()))
    edit('time_wider',lambda x:x['date_range'].update(end=(dt.date.fromisoformat(x['date_range']['end'])+dt.timedelta(days=2)).isoformat()))
    edit('omit_qualification',lambda x:x.update(qualification=None))
    edit('qualification_threshold',lambda x:x['qualification'].update(minimum=x['qualification']['minimum']+1))
    edit('qualification_basis',lambda x:x['qualification'].update(basis='GAMES' if x['qualification']['basis']!='GAMES' else 'PA'))
    edit('measure_unit',lambda x:x['derived'][0].update(op='PCT'))
    edit('additional_role',lambda x:x['entity_filters'].append(dict(role='PITCHER' if x['entity_filters'][0]['role']=='BATTER' else 'BATTER',ids=[x['entity_filters'][0]['ids'][0]])))
    edit('measure_base',lambda x:x['aggregates'][1].update(function='COUNT',field='*',condition=None))
    edit('measure_launder',lambda x:x['aggregates'][3].update(function='SUM',name='avg_release_speed'))
    # Update sort only; a different numeric metric is still valid and read-only.
    edit('ordering_metric',lambda x:x['sort'][0].update(name='velocity'))
    edit('grouping',lambda x:x.update(group_by=[c['identity']]))
    out['grouping']['sort']=[s for s in out['grouping']['sort'] if s['name']!='p_throws']
    return out


def reference(c):
    """Independent Python reduction of source rows, not Artifact normalization."""
    q=c['raw']; groups={}
    for row in c['rows']:
        if row[c['identity']]!=q['entity_filters'][0]['ids'][0] or row['game_type']!=c['code'] or not q['date_range']['start'] <= row['game_date'].isoformat() <= q['date_range']['end']:continue
        groups.setdefault((row[c['identity']],row['p_throws']),[]).append(row)
    result=[]
    for (identity,side),rows in groups.items():
        n={'PA':sum(x['events'] is not None for x in rows),'BBE':sum(x['launch_speed'] is not None for x in rows),'PITCHES':len(rows),'GAMES':len({x['game_pk'] for x in rows})}[q['qualification']['basis']]
        if n<q['qualification']['minimum']:continue
        hits=sum(x['events'] in {'single','double','triple','home_run'} for x in rows)
        ab=sum(x['events'] is not None and x['events'] not in {'walk','intent_walk','hit_by_pitch','catcher_interf','sac_fly','sac_bunt','sac_fly_double_play','sac_bunt_double_play','truncated_pa','game_advisory'} for x in rows)
        velocities=[x['release_speed'] for x in rows if x['release_speed'] is not None]
        result.append({c['identity']:identity,'p_throws':side,'volume':sum(x['events'] is not None for x in rows),'numerator':hits,'denominator':ab,'velocity':sum(velocities)/len(velocities) if velocities else None,'rate_value':hits/ab if ab else None})
    result.sort(key=lambda x:(x['rate_value'] is None,-(x['rate_value'] or 0),x[c['identity']],x['p_throws'] is None,x['p_throws'] or ''))
    return result[:q['limit']]
