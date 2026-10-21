"""Real-LLM WSA-05 set-level interpretation and final-presentation utility.

RUN_INTEGRATION=1 python -m pytest -q tests/test_wsa05_live_e2e.py
No network source discovery: these are fresh, bounded retrieved-evidence packets.
"""
import pytest

from app.config import get_config
from app.llm.client import LLMClient
from app.contracts.evidence_origin import CorroborationState as CS, DependencyState as DS
from app.contracts.models import (
    AnswerProjection, Goal, GoalStatus, QualifiedEvidence, SemanticInterpretation,
)
from app.response.composer import ResponseComposer
from tests.test_evidence_origin_wsa05 import prop, evidence
from app.verification.evidence_origin import resolve_origins

pytestmark = pytest.mark.integration


def client():
    if not get_config().llm.configured:
        pytest.skip("LLM not configured")
    return LLMClient(get_config().llm)


@pytest.mark.parametrize("texts,state", [
    (["AP original: Kai Mori signing transaction is complete, the club announced.",
      "This shortened AP dispatch reports the Kai Mori signing transaction and adds local context.",
      "Reprinted from AP with edits: Kai Mori signing transaction is complete."], CS.DEPENDENT_MULTIPLE_DOCUMENTS),
    (["Dodgers official release: the Kai Mori signing transaction is complete.",
      "The Ledger reports that the Dodgers' official release announced the Kai Mori signing transaction.",
      "The Tribune summarizes the Dodgers' announcement of the Kai Mori signing transaction."], CS.DEPENDENT_MULTIPLE_DOCUMENTS),
    (["Reporter Noor: a league source tells me the Kai Mori signing transaction may happen.",
      "Reporter Hugo: according to Noor's reporting, the Kai Mori signing transaction may happen."], CS.DEPENDENT_MULTIPLE_DOCUMENTS),
    (["Marta: a league source tells me the Kai Mori signing transaction could happen.",
      "Kenji: several club sources say the Kai Mori signing transaction could happen."], CS.DEPENDENCY_UNKNOWN),
    (["通信社の原報：球団がカイ・モリとの契約成立を発表した。",
      "地方紙の記事：通信社の原報を引用し、球団がカイ・モリとの契約成立を発表したと伝えた。"], CS.DEPENDENT_MULTIPLE_DOCUMENTS),
])
def test_live_fresh_origin_interpretation(texts, state):
    p = prop()
    result = resolve_origins(p, *evidence(p, texts), llm=client())
    assert result.corroboration_state == state, result.model_dump()
    assert not any(r.dependency_state == DS.SEPARATE_EVIDENTIARY_BASIS for r in result.relations)


def test_live_positive_separate_firsthand_bases():
    p = prop("drills", "Did Kai Mori participate in drills?")
    texts = [
        "Observer Lina: I personally filmed Kai Mori participating in morning drills at field 1. My original video V-17 documents this firsthand observation, not a club release or another reporter's account.",
        "Observer Tomas: I personally photographed Kai Mori participating in afternoon drills at field 3. My original photographs P-29 document a distinct firsthand observation at a different session, not Lina's video or a club release.",
    ]
    result = resolve_origins(p, *evidence(p, texts), llm=client())
    assert result.corroboration_state == CS.MULTIPLE_SEPARATE_BASES, result.model_dump()


def test_live_unknown_origin_qualified_presentation_retained():
    llm = client()
    p = prop()
    texts = ["Nora: a league source says the Kai Mori signing transaction may happen.",
             "Amir: club sources say the Kai Mori signing transaction may happen."]
    fs, docs, spans = evidence(p, texts)
    origins = resolve_origins(p, fs, docs, spans, llm=llm)
    assert origins.corroboration_state == CS.DEPENDENCY_UNKNOWN
    goal = Goal(id="g", statement=p.statement, raw_query="What do these reports establish?", propositions=[p])
    projection = AnswerProjection(goal_id="g", goal_status=GoalStatus.LIMITED, propositions=[p],
        evidence_origins=[origins], qualified_evidence=[QualifiedEvidence(
            proposition_id=p.id, finding_id=f.id, span_text=spans[f.span_id].text,
            limitation="speculative reporting; no official confirmation") for f in fs],
        limitations=["the signing is not established"])
    answer = ResponseComposer(llm).compose(goal, SemanticInterpretation(raw_query=goal.raw_query, language="en"), projection)
    assert "could not establish" in answer.lower(), answer
    assert "may" in answer.lower() or "speculative" in answer.lower(), answer
    assert "no official confirmation" in answer.lower(), answer
    assert "kai mori" in answer.lower(), answer


def test_live_origin_conformance_rejects_invented_independence():
    from app.response.answer_contract import AnswerPackage
    from app.response.composition_contract import parse_composer_draft
    from app.verification.composition_conformance import request_conformance_assessment, conform_composition
    llm = client()
    p = prop()
    origins = resolve_origins(p, *evidence(p, ["A reports a possibility.", "B reports a possibility."]))
    package = AnswerPackage(goal_id="g", goal_status="LIMITED", language="en", evidence_origins=[origins])
    composed = parse_composer_draft({"statements": [{"kind": "FACTUAL",
        "text": "Two independent sources confirm that Kai Mori has signed."}]})
    verdict = request_conformance_assessment(llm, package, composed)
    assert verdict is not None
    answer = conform_composition(composed, package, verdict).text
    assert "Two independent sources confirm" not in answer, (answer, verdict.model_dump())
    assert "could not establish" in answer
