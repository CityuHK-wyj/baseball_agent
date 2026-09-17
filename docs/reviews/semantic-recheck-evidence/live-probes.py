"""Read-only re-review evidence. Run from repository root with runtime credentials."""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path.cwd()))
from app.pipeline import AnalysisPipeline
from app.models.clarification import ClarificationAnswer
from app.tools.execution import DuckDBReadOnlyExecutor, PostgresReadOnlyExecutor

queries = [
 ('2023', 'top 5 by maximum exit velocity on fastballs at least 95 mph after two strikes near the upper edge in the regular season in 2023, minimum 20 BBE'),
 ('2025', 'top 5 by average exit velocity on fastballs at least 95 mph after two strikes near the upper edge in the regular season in 2025, minimum 20 BBE'),
 ('cross', 'top 5 by maximum exit velocity on fastballs at least 95 mph after two strikes near the upper edge in the regular season in 2023 vs 2024, minimum 20 BBE'),
 ('historical-wide', 'top 5 by maximum exit velocity on fastballs at least 95 mph after two strikes near the upper edge in the regular season from 2015-04-05 to 2023-11-01, minimum 20 BBE'),
 ('recent-wide', 'top 5 by average exit velocity on fastballs at least 95 mph after two strikes near the upper edge in the regular season from 2024-03-15 to 2026-09-14, minimum 20 BBE'),
 ('cross-accepted', 'top 5 by maximum exit velocity in the regular season in 2023 vs 2024, minimum 20 BBE'),
]
for label,query in queries:
 sql=[]
 def recorder(method,source):
  def run(self,statement):
   result,rows=method(self,statement)
   sql.append({'source':source,'sql':statement,'status':result.status,'rows':rows})
   return result,rows
  return run
 with tempfile.TemporaryDirectory() as directory:
  with patch.object(DuckDBReadOnlyExecutor,'execute_with_rows',recorder(DuckDBReadOnlyExecutor.execute_with_rows,'PARQUET')), patch.object(PostgresReadOnlyExecutor,'execute_with_rows',recorder(PostgresReadOnlyExecutor.execute_with_rows,'POSTGRES')):
   p=AnalysisPipeline.default(runtime_dir=Path(directory))
   r=p.analyze(query,run_id=label)
   if r.needs_clarification:
    request=r.clarifications[0]
    option=next(o for o in request.options if o.value=='BATTER_RELATIVE_UPPER_EDGE')
    r=p.resume_clarification(label,ClarificationAnswer(clarification_ref=request.clarification_id,chosen_option_id=option.option_id))
   print(json.dumps({'case':label,'query':query,'objectives':[o.model_dump(mode='json') for o in r.objectives], 'statuses':r.objective_statuses,'packages':[p.model_dump(mode='json') for p in r.response_packages],'responses':r.responses,'sql':sql},default=str),flush=True)
   # Real restart must reuse products without further source SQL and preserve requirements.
   p.close()
   p=AnalysisPipeline.default(runtime_dir=Path(directory))
   before=len(sql)
   recovered=p.resume_run(label)
   print(json.dumps({'case':label,'restart':{'additional_sql':len(sql)-before,'same_objectives':recovered.objectives==r.objectives,'statuses':recovered.objective_statuses}}),flush=True)
   p.close()
