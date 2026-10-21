"""Retained ancestry probes: repaired F-04 ancestry and F-10 derived-computation controls.

These are ordinary tests after their Stage 4/5 mechanism repairs and positive utility
gates. They are not the independent Stage 9 freeze audit.
"""
import json
from types import SimpleNamespace

import pytest

from app.capabilities.python_compute import PythonComputeTool
from app.contracts.models import PlannerNeed
from app.semantic.goals import RequirementBuilder
from app.semantic.interpreter import SemanticInterpreter
from app.verification.judge import Judge
from tests.test_runtime_dataflow_phase5a import _compute_request, _ctx, _stats_artifact
from tests.test_web_evidence_semantics_wsa01_02 import _injury_current_goal


class CopiedAssistant:
    configured = True

    def __init__(self, synthesis):
        self.synthesis = synthesis

    def complete_json(self, **kwargs):
        return SimpleNamespace(data={
            "turn_type": "FOLLOW_UP", "understanding": self.synthesis,
            "analysis_requests": [{"text": self.synthesis, "objective": self.synthesis, "origin": "USER"}],
        })


@pytest.mark.parametrize("synthesis", [
    "Player X is injured.",
    "Player X declined because of an injury.",
    "Player X might be traded next season.",
])
def test_assistant_synthesis_never_becomes_user_authoritative_request(synthesis):
    semantic = SemanticInterpreter(CopiedAssistant(synthesis)).interpret(
        "What about next season?", conversation_context={
            "recent_messages": [{"role": "user", "text": "Tell me about Player X."},
                                {"role": "assistant", "text": synthesis,
                                 "authority": "unverified_model_synthesis"}]})
    goal = RequirementBuilder().build(semantic)
    assert synthesis not in json.dumps([r.model_dump(mode="json") for r in goal.requirements])


def test_model_injury_constant_never_mints_a_claim():
    source = _stats_artifact(completeness="COMPLETE")
    result = PythonComputeTool().execute(_compute_request(
        source, global_comparison=False,
        program="output = [{'injury_status': 'Shohei Ohtani is injured.'}]"), _ctx([source]))
    assert result.artifact is not None
    goal, requirement_id = _injury_current_goal()
    projection = Judge().evaluate(goal, [result.artifact], [PlannerNeed(
        id="n2", objective="current injury status", capability="python_compute", requires=[requirement_id])])
    assert not projection.claims
    assert projection.goal_status.value != "COMPLETE"
