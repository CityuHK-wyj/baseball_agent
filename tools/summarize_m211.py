#!/usr/bin/env python3
"""Summarize captured M2.11 boundaries; contains no production imports or mutations."""
import collections
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/audit/m211'

def read(path):return json.loads(path.read_text())
def write(name,data):(OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str)+'\n')

def main():
    summary={}; resource=[]
    for layer in ['diagnostic-live','analytics-live','holdout']:
        tasks=[]; docs=[]; findings=[]
        for path in sorted(OUT.glob(layer+'-*.json')):
            x=read(path)
            if not isinstance(x,dict) or 'turn' not in x:continue
            t=x['turn']; p=t['projection']['propositions']; mandatory=[q for q in p if q['mandatory']]
            d=[d for a in t['artifacts'] for d in a.get('structured_data',{}).get('documents',[])]
            f=[f for a in t['artifacts'] for f in a.get('structured_data',{}).get('evidence_findings',[]) if f['status']=='SUPPORTED']
            docs+=d;findings+=f
            tasks.append(dict(id=x['task']['id'],question=x['task']['query'],status=t['projection']['goal_status'],mandatory_satisfied=sum(q['coverage']=='SATISFIED' for q in mandatory),mandatory_total=len(mandatory),supported_finding_ids=[e['id'] for e in f],executed_capabilities=sorted({q['request']['capability']for q in x['tool_requests']}),consistency=t['projection'].get('consistency'),duration_s=x['duration_s']))
            if layer=='diagnostic-live':
                discovered={}
                for call in x['web_searches']:
                    for h in call['result'][0]:discovered.setdefault(h['url'],h)
                selections=[]
                for sel in x['web_selection']:
                    documents=sel['retrieval_result'][4]
                    attempted={v['url'] for v in documents}
                    selections.append(dict(objective=sel['objective'],queries=sel['queries'],budget=sel['budget'],ranked=[dict(rank=i+1,**h,attempted=h['url']in attempted)for i,h in enumerate(sel['ranked_hits'])],documents=[dict(id=v['id'],url=v['url'],status=v['retrieval_status'],method=v['retrieval_method'],fallback_steps=v['fallback_steps'],resolution_traces=v['resolution_traces'])for v in documents]))
                resource.append(dict(task_id=x['task']['id'],discovered=list(discovered.values()),selections=selections))
        traces=[r for d in docs for r in d.get('resolution_traces',[])]
        summary[layer]=dict(tasks=tasks,mandatory_satisfied=sum(t['mandatory_satisfied']for t in tasks),mandatory_total=sum(t['mandatory_total']for t in tasks),supported_findings=len(findings),unique_supported_passages=len({(f['proposition_id'],f['span_hash'])for f in findings}),documents=len(docs),document_statuses=dict(collections.Counter(d['retrieval_status']for d in docs)),http_first_outcomes=dict(collections.Counter(d['fallback_steps'][0]for d in docs)),fallback_entered=sum(len(d['fallback_steps'])>1 for d in docs),fallback_last_outcomes=dict(collections.Counter(d['fallback_steps'][-1]for d in docs if len(d['fallback_steps'])>1)),resolver_traces=traces)
    old=read(OUT/'historical-traces.json')
    false=[f for c in old for f in c['false_findings']]
    summary['historical']=dict(tasks=len(old),false_findings=len(false),unique_false_passages=len({(f['proposition']['id'],f['duplicate_text_hash'])for f in false}),mandatory_satisfied=sum(c['historical_run']['mandatory_satisfied']for c in old),mandatory_total=sum(c['historical_run']['mandatory_total']for c in old),controlled_admission_supported=sum(any(e['status']=='SUPPORTED'for e in f['controlled_current_admission'])for f in false))
    write('metrics.json',summary);write('resource-index.json',resource)
    for layer,data in summary.items():print(layer,{k:v for k,v in data.items()if k not in ['tasks','resolver_traces']})
    # Source evidence inventory, reproducible hashes; runtime data is public task content.
    files=[]
    for path in sorted(OUT.glob('*')):
        if path.is_file() and path.name!='manifest.json':files.append(dict(file=str(path.relative_to(ROOT)),bytes=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    write('manifest.json',files)

if __name__=='__main__':main()
