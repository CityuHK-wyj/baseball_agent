"""Trace NL -> objective -> frozen requirement -> both physical query dialects.

Exits nonzero while the remaining demonstrated release blockers persist.
"""
import json
import sys
import tempfile
from pathlib import Path
from datetime import date
sys.path.insert(0, str(Path.cwd()))
from app.pipeline import AnalysisPipeline
from app.semantic.requirement_decomposer import RuleBasedRequirementDecomposer
from app.models.planning import AgentTask
from app.semantic.field_mapping import FieldMappingRegistry
from app.tools.statcast import ParquetStatcastTool,PostgresStatcastTool
from app.tools.results import ToolResult

cases=[
 ('qualification_velocity_collision','top 5 by maximum exit velocity on fastballs at least 95 mph, at least 100 BBE in 2023',100,'exit_velocity','MAX',(95,),None,'REGULAR_SEASON'),
 ('symbolic_qualification','top 5 by maximum exit velocity with >= 20 BBE in 2023',20,'exit_velocity','MAX',(),None,'REGULAR_SEASON'),
 ('ranking_metric_collision','top 5 hitters facing pitch velocity >= 95 mph ranked by maximum exit velocity in 2023',3,'exit_velocity','MAX',(95,),None,'REGULAR_SEASON'),
 ('exhibition','top 5 by maximum exit velocity in exhibition games in 2023',3,'exit_velocity','MAX',(),None,'EXHIBITION'),
 ('mixed_counts','top 5 by maximum exit velocity on 0-2 or 1-1 counts in 2023',3,'exit_velocity','MAX',(), 'mixed','REGULAR_SEASON'),
 ('both_metrics','top 5 by maximum exit velocity on fastballs at least 95 mph with exit velocity at least 100 mph in 2023',3,'exit_velocity','MAX',(95,100),None,'REGULAR_SEASON'),
 ('exact_count','top 5 by average exit velocity on 0-2 fastballs at least 95 mph in 2023',3,'exit_velocity','AVG',(95,), (0,),'REGULAR_SEASON'),
 ('generic_count','top 5 by average exit velocity on fastballs at least 95 mph after two strikes in 2023',3,'exit_velocity','AVG',(95,), (0,1,2,3),'REGULAR_SEASON'),
]
for agg in ('maximum','average'):
 for n in (3,20,100):
  cases.append((f'{agg}_{n}',f'top 5 by {agg} exit velocity minimum {n} BBE in 2023',n,'exit_velocity','MAX' if agg=='maximum' else 'AVG',(),None,'REGULAR_SEASON'))
class Capture:
 def __init__(self): self.sql=[]
 def execute_with_rows(self,sql):
  self.sql.append(sql)
  rows=[(date(2023,3,15),date(2023,11,1))] if 'MIN(' in sql else ([] if 'player_dictionary' in sql else [(1,100,90,100)])
  return ToolResult.ok(len(rows)),rows
failed=[]
with tempfile.TemporaryDirectory() as directory:
 p=AnalysisPipeline.default(runtime_dir=Path(directory))
 for label,query,n,metric,agg,values,balls,game in cases:
  semantic=p._semantic.normalize(query)
  objective=semantic.objectives[0]
  req=RuleBasedRequirementDecomposer().decompose(objective)[0]
  cs=req.descriptor.constraints
  rank=next((c for c in cs if c.kind=='RANKING'),None)
  nums=[c for c in cs if c.kind=='NUMERIC']
  count=next((c for c in cs if c.kind=='COUNT'),None)
  pop=next((c for c in cs if c.kind=='POPULATION'),None)
  valid=bool(rank and rank.metric_key==metric and rank.aggregation==agg and req.qualification_rule.min_batted_balls==n and sorted(c.value for c in nums)==sorted(values) and pop.game_types==(game,))
  if balls=='mixed': valid=bool(semantic.clarifications) # No supported union: must not silently discard.
  elif balls is not None: valid=valid and count is not None and count.balls==balls
  if label=='both_metrics': valid=valid and {c.key:c.value for c in nums}=={'pitch_velocity':95,'exit_velocity':100}
  queries={}
  for tool_class in (ParquetStatcastTool,PostgresStatcastTool):
   capture=Capture();tool=tool_class([req],FieldMappingRegistry(),capture)
   task=AgentTask(task_id='trace',objective_ref=objective.objective_id,requirement_refs=(req.requirement_id,),description=query)
   tool.execute(task);queries[tool.source_kind]=capture.sql
  if not valid: failed.append(label)
  print(json.dumps({'case':label,'passed':valid,'query':query,'objective':objective.model_dump(mode='json'),'requirement':req.model_dump(mode='json'),'sql':queries}),flush=True)
 p.close()
print(json.dumps({'unresolved_blockers':failed}))
raise SystemExit(bool(failed))
