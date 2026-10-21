#!/usr/bin/env python3
"""Frozen supplemental properties/negative task; never retunes production or primary holdout."""
import copy,hashlib,json,random,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.capabilities.analytics.ir import AnalyticalQuery,validate_query
from app.capabilities.analytics.measures import declare_measures
OUT=Path('docs/audit/m212');frozen=json.loads((OUT/'IMPLEMENTATION_FROZEN.json').read_text())
for path,expected in frozen['production_hashes'].items():assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==expected
records=[]
for seed in range(21298000,21298048):
 rng=random.Random(seed);key=rng.choice(['bb_pct','k_pct','hard_hit_pct']);names=[f'column_{rng.randrange(100000)}' for _ in range(3)]
 condition={'op':'IN','field':'events','values':['walk','intent_walk'] if key=='bb_pct' else ['strikeout','strikeout_double_play']}
 if key=='hard_hit_pct':condition={'op':'COMPARE','field':'launch_speed','comparator':'>=','value':95}
 base=dict(aggregates=[dict(name=names[0],function='COUNT_IF',condition=condition),dict(name=names[1],function='COUNT_NON_NULL',field='launch_speed' if key=='hard_hit_pct' else 'events')],derived=[dict(name=names[2],op='PCT',left=dict(kind='aggregate',name=names[0]),right=dict(kind='aggregate',name=names[1]))])
 def identity(raw):return next(m.key for m in declare_measures(validate_query(AnalyticalQuery.model_validate(raw))) if m.kind=='derived')
 records.append(dict(seed=seed,property='valid_measure',ok=identity(base)==key,raw=base,actual=identity(base),expected=key))
 alias=copy.deepcopy(base);alias['derived'][0]['name']='different_label_'+str(rng.randrange(100000));records.append(dict(seed=seed,property='alias',ok=identity(alias)==key))
 unit=copy.deepcopy(base);unit['derived'][0]['op']='SAFE_DIV';records.append(dict(seed=seed,property='unit',ok=identity(unit) is None))
 wrong=copy.deepcopy(base);wrong['aggregates'][0].update(function='COUNT',field='*',condition=None);records.append(dict(seed=seed,property='base',ok=identity(wrong) is None))
 if key=='hard_hit_pct':
  threshold=copy.deepcopy(base);threshold['aggregates'][0]['condition']['value']=rng.choice([87,92,96,97,101,108]);records.append(dict(seed=seed,property='threshold',ok=identity(threshold) is None,raw=threshold))
summary={}
for row in records:
 c=summary.setdefault(row['property'],dict(passed=0,failed=0));c['passed' if row['ok'] else 'failed']+=1
(OUT/'holdout-supplement-properties.json').write_text(json.dumps(dict(seeds=list(range(21298000,21298048)),summary=summary,checks=records),indent=2)+'\n')
print(summary,flush=True)
# The primary population had one repeated ambiguity control. Retain it, and add a
# separately labelled fresh negative, rather than replacing or regenerating the holdout.
from tools import evaluate_m212_live as live
rng=random.Random(21296010);surname=rng.choice(['Hernandez','Perez','Rodriguez'])
task=dict(id='fresh_ambiguity',query=f'In the 2024 regular season, how many doubles did {surname} hit?',spec={'ambiguous':True},seed=21296010)
live.tasks=lambda _: [task]
live.main('holdout-supplement')
