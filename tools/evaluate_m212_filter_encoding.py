#!/usr/bin/env python3
"""Frozen positive control for equivalent minimal/bound Condition encodings."""
import copy,hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.m212_cases import case,reference
from tools.m212_evaluation_support import encode
from app.capabilities.analytics.conformance import requested_contract,attest,compare
from app.capabilities.analytics.compiler import SQLCompiler
from app.capabilities.analytics.ir import AnalyticalQuery,validate_query
from app.capabilities.analytics.tool import LocalAnalyticsTool
from app.contracts.models import ToolRequest
from tests.test_analytical_conformance_m212 import FixedIR,Source
out=Path('docs/audit/m212');frozen=json.loads((out/'IMPLEMENTATION_FROZEN.json').read_text())
for p,h in frozen['production_hashes'].items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==h
c=case(21299002);predicate=dict(op='COMPARE',field='p_throws',comparator='=',value='L');c['raw']['filters']=[predicate];c['inputs']['constraints']['filters']=[predicate]
request=ToolRequest(need_id='a',capability='local_analytics',objective='Report batting average',inputs=c['inputs']);source=Source(c);tool=LocalAnalyticsTool(source,FixedIR(c['raw']))
intent=tool._intent(request);want=requested_contract(intent,request.inputs,tool._requested_measures(request,intent));q=validate_query(AnalyticalQuery.model_validate(c['raw']));actual=attest(q,SQLCompiler().compile(q,'POSTGRES'))
result=tool.execute(request);comparison=compare(want,actual,check_availability=False)
# Independent reference: same exact predicate applied to the input records.
ref=copy.deepcopy(c);ref['rows']=[r for r in ref['rows'] if r['p_throws']=='L'];expected=reference(ref)
record=dict(seed=21299002,expected_status='MATCH',actual_status=comparison.status,false_rejection=comparison.status!='MATCH',request=encode(request),query=encode(q),requested_filters=want.filters,executed_filters=actual.filters,result=encode(result),reference=expected,executed_calls=len(source.calls))
(out/'holdout-filter-encoding.json').write_text(json.dumps(record,indent=2,default=str)+'\n');print(record['actual_status'],record['false_rejection'])
