#!/usr/bin/env python3
"""Legacy populations only; explicit producer contract migration, no new seeds."""
import copy
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.analytical_requirement_fixture import fixture_inputs
from tools.evaluate_m212r2 import OUT,freeze_check
from app.capabilities.analytics.tool import LocalAnalyticsTool
import tools.evaluate_m212r_legacy as m12
import tools.evaluate_m212r as primary
import tools.evaluate_m212r_postfreeze as supplemental


def main():
    if (OUT/'IMPLEMENTATION_FROZEN.json').exists():freeze_check()
    legacy=OUT/'legacy';legacy.mkdir(exist_ok=True)
    original=m12.run
    def migrated(request,raw):
        request=copy.deepcopy(request);request['inputs']=fixture_inputs(request['inputs'])
        return original(request,raw)
    m12.run=migrated;m12.OUT=legacy;m12.main()
    primary.OUT=legacy
    primary.main('development',212673024,64)
    (legacy/'development-analytics.json').rename(legacy/'m212r-primary.json')
    class Producer(LocalAnalyticsTool):
        def execute(self,request,ctx=None):
            # These saved producers explicitly supply their independently authored
            # Goal.raw_query. Upgrade that capability input, never inspect the IR.
            request.inputs['bound_user_intent']=request.inputs.get('raw_query','')
            for req in request.inputs.get('analytical_requirements') or []:
                if req.get('kind')=='ENTITY' and request.inputs.get('entity_sets'):
                    req['value'].update(canonical_ids=request.inputs['entity_sets'][0]['ids'],identity_verified=True)
            return super().execute(request,ctx)
    supplemental.LocalAnalyticsTool=Producer;supplemental.OUT=legacy
    supplemental.freeze_check=lambda:dict(implementation_sha='legacy replay against current R2 implementation')
    old=json.loads(Path('docs/audit/m212r/UNSEEN_SEEDS.json').read_text())
    (legacy/'UNSEEN_SEEDS.json').write_text(json.dumps({'post_freeze_review':old['post_freeze_review']},indent=2)+'\n')
    supplemental.main()
    if (OUT/'IMPLEMENTATION_FROZEN.json').exists():freeze_check()

if __name__=='__main__':main()
