"""F-01: inspect actual tool and Judge reviewer packets, not only final verdicts."""
import json

import pytest

from app.contracts.models import PlannerNeed
from app.verification.evidence import verify_span
from app.verification.judge import Judge
from tests.test_semantic_authority_wsa09 import (
    REVIEWER_MARKER, ScriptedLLM, _assessment, _grounding, _support_grounding, _verify,
)
from tests.test_web_evidence_semantics_wsa01_02 import (
    _document, _injury_current, _injury_current_goal, _span, _web_artifact,
)


class CapturingLLM(ScriptedLLM):
    def __init__(self, receipts):
        super().__init__(_assessment(grounding=receipts), review={"supported": True})
        self.packets = []

    def complete_json(self, **kwargs):
        if REVIEWER_MARKER in kwargs.get("system", ""):
            self.packets.append(json.loads(kwargs["user"]))
        return super().complete_json(**kwargs)


TEXT = "Shohei Ohtani injury status: an update was published about the roster."


@pytest.mark.parametrize("bad", [
    _grounding("RELATION", "fabricated duplicate injury quote"),
    _grounding("RELATION", "Another document says the player is healthy."),
    _grounding("RELATION", "Player B signed a contract for next season."),
    _grounding("RELATION", TEXT, locator="NONEXISTENT"),
    _grounding("RELATION", TEXT, basis="INVENTED"),
    _grounding("RELATION", TEXT, basis="DOCUMENT_METADATA"),
    _grounding("RELATION", "Model-created injury declaration with no acquired source."),
])
@pytest.mark.parametrize("bad_first", [False, True])
def test_invalid_receipts_do_not_change_actual_tool_reviewer_packet(bad, bad_first):
    clean = CapturingLLM(_support_grounding(TEXT))
    expected = _verify(_injury_current(), TEXT, "2026-10-01", clean)
    receipts = ([bad] + _support_grounding(TEXT) if bad_first
                else _support_grounding(TEXT) + [bad])
    mixed = CapturingLLM(receipts)
    observed = _verify(_injury_current(), TEXT, "2026-10-01", mixed)
    assert clean.packets and mixed.packets == clean.packets
    assert observed.status == expected.status
    assert observed.semantic_assessment["grounding"] == expected.semantic_assessment["grounding"]


def test_duplicate_valid_receipt_does_not_add_support():
    clean = CapturingLLM(_support_grounding(TEXT))
    _verify(_injury_current(), TEXT, "2026-10-01", clean)
    mixed = CapturingLLM(_support_grounding(TEXT) + [_grounding("RELATION", TEXT)])
    result = _verify(_injury_current(), TEXT, "2026-10-01", mixed)
    assert mixed.packets == clean.packets
    assert "RELATION: duplicate receipt" in result.checks["grounding_receipts"]["rejected"]


def test_judge_drilldown_uses_only_validated_receipts():
    goal, _ = _injury_current_goal()
    prop = next(p for p in goal.propositions if p.kind.value == "FACT")
    doc = _document(TEXT, "2026-10-01")
    span = _span(TEXT, doc)
    finding = verify_span(prop, span, doc, allow_llm=False)
    artifact = _web_artifact(goal, prop, doc, span, finding)
    llm = CapturingLLM(_support_grounding(TEXT) + [_grounding("RELATION", "fabricated private source")])
    Judge(llm=llm, max_inspections=2).evaluate(
        goal, [artifact], [PlannerNeed(id="n_web", objective="research", capability="web_research")])
    assert llm.packets
    assert all(r["source_quote"] == TEXT for p in llm.packets for r in p["receipts"])
    assert all(len(p["receipts"]) <= 2 for p in llm.packets)
