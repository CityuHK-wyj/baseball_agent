#!/usr/bin/env python3
"""Same-input semantic provider retries; original integration failure retained."""
import dataclasses
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import get_config
from app.llm.client import LLMClient
from app.runtime.conversation import ConversationManager
from app.semantic.interpreter import SemanticInterpreter
from tools.evaluate_m212r2 import OUT,freeze_check

def main():
    freeze_check();cfg=get_config();cfg=dataclasses.replace(cfg.llm,timeout_seconds=25)
    client=LLMClient(cfg);interpreter=SemanticInterpreter(client)
    prepared=ConversationManager().prepare('it_en','How did Shohei Ohtani hit in the 2024 regular season?')
    calls=[];complete=client.complete_json
    def captured(**kwargs):
        row=dict(role=kwargs['role'],system=kwargs['system'],user=kwargs['user'],max_tokens=kwargs['max_tokens'])
        try:
            result=complete(**kwargs);row['data']=result.data;calls.append(row);return result
        except Exception as error:
            row['error_type']=type(error).__name__;calls.append(row);raise
    client.complete_json=captured
    retries=[interpreter.interpret(prepared.effective_query,conversation_context=prepared.interpreter_context).model_dump(mode='json') for _ in range(3)]
    evidence=dict(frozen_sha=freeze_check()['implementation_sha'],semantic_model=cfg.semantic_model,runtime_model=cfg.runtime_model,provider_timeout_seconds=25,
                  original='Retained integration it_en trace has zero semantic entities/analysis, season-only Goal, and no analytics execute call; consistent with the unchanged fallback path.',
                  questions_and_context_identical=all(c['user']==calls[0]['user'] for c in calls),calls=calls,retries=retries)
    (OUT/'holdout-provider-retries.json').write_text(json.dumps(evidence,indent=2,default=str)+'\n')
    print([(len(r['entities']),r.get('analysis_requests',r.get('analysis',[])),r['structured_hints']) for r in retries])

if __name__=='__main__':main()
