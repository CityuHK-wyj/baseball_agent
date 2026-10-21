#!/usr/bin/env python3
"""Archive actual frozen runtime diagnostics and reproduce contract boundaries."""
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.capabilities.analytics.conformance import requested_contract,contract_for_goal
from app.contracts.models import Goal,Artifact
from tools.evaluate_m212r2 import OUT,freeze_check

def main():
    frozen=freeze_check();source=Path('/tmp/m212r2-frozen-integration');archive=OUT/'integration-runtime';archive.mkdir(exist_ok=True)
    for f in (source/'diagnostics').glob('*.json'):shutil.copyfile(f,archive/f.name)
    conn=sqlite3.connect('file:'+str(source/'conversations.db')+'?mode=ro',uri=True)
    conversations={sid:json.loads(payload) for sid,payload in conn.execute('SELECT session_id,payload FROM conversations')};conn.close()
    (archive/'conversations.json').write_text(json.dumps(conversations,indent=2)+'\n')
    records=[]
    for sid in ['it_follow','it_split','it_rank','it_xsrc']:
        if sid not in conversations:continue
        state=conversations[sid];g=Goal.model_validate(state['goals'][-1]);entity=any(r.kind.value=='ENTITY' for r in g.requirements)
        # Explicitly labeled independent compatibility fixture: known MLBAM identity
        # of Ohtani, never an inference from the candidate being checked.
        identities=[dict(role='BATTER',ids=[660271])] if entity else []
        inputs=dict(analytical_requirements=[r.model_dump(mode='json') for r in g.requirements],bound_user_intent=g.raw_query,_canonical_identity_exports=identities)
        season=2024 if sid in ['it_follow','it_xsrc'] else 2025
        bound=requested_contract(dict(entities=identities,season=season),inputs,[])
        identity=Artifact(kind='ENTITY_RESOLUTION',structured_data={'identity':{'大谷翔平':{'id':660271,'level':'CANONICAL'}}})
        verified=contract_for_goal(g,[identity],SimpleNamespace(season=season,date_range=None))
        records.append(dict(session_id=sid,origin='Actual frozen conversation Goal; independently declared canonical identity fixture, no model/database rerun',goal=g.model_dump(mode='json'),answer=state['messages'][-1]['text'],producer_contract=bound.model_dump(mode='json'),verifier_contract=verified.model_dump(mode='json') if verified else None))
    (OUT/'holdout-integration-contract-diagnostics.json').write_text(json.dumps(dict(frozen_sha=frozen['implementation_sha'],records=records),indent=2,default=str)+'\n')
    freeze_check();print([(r['session_id'],r['producer_contract']['issues'],r['verifier_contract'] and r['verifier_contract']['issues']) for r in records])

if __name__=='__main__':main()
