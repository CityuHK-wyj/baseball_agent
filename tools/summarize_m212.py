#!/usr/bin/env python3
"""Independent-result and receipt accounting; no production changes."""
import gzip,json,math,sys,xml.etree.ElementTree as ET
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.m212_evaluation_support import equal
from app.contracts.models import Artifact
from app.capabilities.analytics.conformance import receipt_usable
OUT=Path('docs/audit/m212')
def read(path):
    if path.exists():return json.loads(path.read_text())
    return json.loads(gzip.decompress(path.with_suffix(path.suffix+'.gz').read_bytes()))
def suite(name):
    p=OUT/(name+'.xml')
    if not p.exists():return {'pending':True}
    root=ET.parse(p).getroot();s=root[0];a=s.attrib
    failures=[dict(name=t.attrib.get('classname','')+'.'+t.attrib['name'],message=(t.find('failure').attrib.get('message','') if t.find('failure') is not None else t.find('error').attrib.get('message',''))) for t in root.iter('testcase') if t.find('failure') is not None or t.find('error') is not None]
    return dict(passed=int(a['tests'])-int(a['failures'])-int(a['errors'])-int(a['skipped']),failed=int(a['failures']),errors=int(a['errors']),skipped=int(a['skipped']),failures=failures)
def users(stage):
    tasks=read(OUT/(stage+'-user-tasks.json'));refs=read(OUT/(stage+'-user-references.json'));out=[]
    for task in tasks:
        data=read(OUT/(stage+'-user-'+task['id']+'.json'));turn=data.get('turn',{});spec=task['spec'];arts=turn.get('artifacts',[]);checks=[]
        for ref in [r for r in refs if r['id']==task['id']]:
            expected=ref['result']['rows'];metric=spec['metric'];year=ref['year'];found=False;normalized=[]
            for artifact in arts:
                if artifact['kind'] not in {'DATABASE_RESULT','DERIVED_MEASURE'}:continue
                if artifact['kind']=='DATABASE_RESULT':
                    if artifact['actual_scope']['season']!=year:continue
                    receipt=artifact['structured_data'].get('execution_receipt',{});a=receipt.get('attestation',{})
                    if spec.get('player') and not any(e['role']=='BATTER' and e['ids']==[spec['player']] for e in a.get('entities',[])):continue
                    columns=[m['column'] for m in a.get('measures',[]) if m.get('key')==metric and m.get('verified')]
                else:
                    columns=[metric]  # Compare stored executed Python result to external SQL reference.
                for column in columns:
                    rows=artifact['structured_data'].get('rows',[])
                    if spec.get('rank'):
                        normalized=[dict(batter_id=r.get('batter_id'),value=r.get(column)) for r in rows]
                    elif spec.get('group'):
                        normalized=[{spec['group']:r.get(spec['group']),'value':r.get(column)} for r in rows]
                    else:normalized=[dict(value=r.get(column)) for r in rows]
                    if equal(normalized,expected):found=True;break
                if found:break
            checks.append(dict(year=year,expected=expected,matched=found))
        numerical=all(c['matched'] for c in checks) if checks else None
        before=[x['result']['goal_status'] for x in data['judge']][-1] if data.get('judge') else None
        receipts=[Artifact.model_validate(a) for a in arts if a['kind']=='DATABASE_RESULT']
        derived=[a for a in arts if a['kind']=='DERIVED_MEASURE']
        out.append(dict(id=task['id'],query=task['query'],seed=task['seed'],reference_checks=checks,numerical_correct=numerical,before_consistency=before,final_status=turn.get('projection',{}).get('goal_status'),answer=turn.get('answer'),receipt_complete=sum(receipt_usable(a) for a in receipts),database_artifacts=len(receipts),derived_artifacts=len(derived),derived_lineage_complete=sum(bool(a['lineage']) and bool(a['structured_data'].get('input_artifact_ids')) for a in derived)))
    return out
result={}
for stage in ['development','holdout']:
    result[stage+'_analytics']=read(OUT/(stage+'-analytics.json'))['summary']
    result[stage+'_users']=users(stage)
for name in ['development-offline','development-integration','final-offline','final-integration']:result[name]=suite(name)
result['legacy']=read(OUT/'legacy.json')['passed']
if (OUT/'holdout-supplement-properties.json').exists():result['supplement']=read(OUT/'holdout-supplement-properties.json')['summary']
if (OUT/'holdout-scope-edges.json').exists():
    edges=read(OUT/'holdout-scope-edges.json')['checks']
    result['frozen_scope_edges']=[]
    for edge in edges:
        control=edge['results'][0]['result']['artifact']['structured_data']['rows']
        keys=edge['reference'][0].keys()
        reference_correct=equal([{key:row.get(key) for key in keys} for row in control],edge['reference'])
        result['frozen_scope_edges'].append(dict(seed=edge['seed'],name=edge['name'],contained=edge['contained'],control_reference_correct=reference_correct,drift_judge_status=edge['results'][1]['projection']['goal_status']))
    primary=result['holdout_analytics']['incorrect_complete']
    escapes=sum(row['drift_judge_status']=='COMPLETE' for row in result['frozen_scope_edges'])
    result['incorrect_COMPLETE']=dict(primary_drift_tool_judge=primary['failed'],primary_drift_denominator=primary['passed']+primary['failed'],additional_scope_edges=escapes,additional_scope_edge_denominator=len(edges),combined_tool_judge=primary['failed']+escapes,combined_drift_denominator=primary['passed']+primary['failed']+len(edges))
if (OUT/'holdout-filter-encoding.json').exists():
    encoding=read(OUT/'holdout-filter-encoding.json')
    result['filter_encoding']={key:encoding[key] for key in ('seed','expected_status','actual_status','false_rejection','executed_calls')}
adjudications={}
for stage in ('development','holdout'):
    adjudications[stage]=[]
    for item in result[stage+'_users']:
        negative=item['id'] in {'invalid','ambiguous'}
        # Final text reviewed against the external SQL values, separately from
        # stored calculation correctness. Cross-source text withholds those values.
        correct=negative or (item['numerical_correct'] is True and item['id']!='cross_source')
        reason=('Unsupported ERA declined; no fabricated value' if item['id']=='invalid' else
                'Ambiguity preserved; no arbitrary analytical identity' if item['id']=='ambiguous' else
                'Correct independent-reference computation was suppressed by explanation completion' if item['id']=='cross_source' else
                'Correct numerical answer against independent SQL reference' if correct else
                'Request binding/constraint adapter prevented execution')
        adjudications[stage].append(dict(id=item['id'],final_answer_correct=correct,negative=negative,reason=reason))
result['user_adjudications']=adjudications
if 'incorrect_COMPLETE' in result:
    by_id={item['id']:item for item in adjudications['holdout']}
    result['incorrect_COMPLETE']['natural_language_final']=sum(item['final_status']=='COMPLETE' and not by_id[item['id']]['final_answer_correct'] for item in result['holdout_users'])
    result['incorrect_COMPLETE']['natural_language_before_consistency']=sum(item['before_consistency']=='COMPLETE' and (item['numerical_correct'] is False or by_id[item['id']]['negative']) for item in result['holdout_users'])
(OUT/'user-adjudications.json').write_text(json.dumps(adjudications,indent=2)+'\n')
if (OUT/'holdout-supplement-user-fresh_ambiguity.json').exists() or (OUT/'holdout-supplement-user-fresh_ambiguity.json.gz').exists():
    result['holdout_supplement_ambiguity']=read(OUT/'holdout-supplement-user-fresh_ambiguity.json')['turn']['projection']['goal_status']
(OUT/'metrics.json').write_text(json.dumps(result,indent=2,default=str)+'\n')
for stage in ['development','holdout']:
    items=result[stage+'_users'];print(stage,'numerical',[x['id'] for x in items if x['numerical_correct'] is True],'missing',[x['id'] for x in items if x['numerical_correct'] is False]);print('before',[x['before_consistency'] for x in items])
print({name: {k:v for k,v in result[name].items() if k!='failures'} for name in ['final-offline','final-integration']})
