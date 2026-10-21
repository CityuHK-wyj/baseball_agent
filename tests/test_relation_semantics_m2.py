"""M2 architecture correction: Requirement RelationSemantics ownership and safety.

The correction moves the relationship vocabulary (CAUSE/EFFECT/QUOTE/STATUS/COMPARISON/...)
into the Requirement semantic contract (``app/semantic/relations.py``) and has downstream
capabilities consume that one definition. These tests lock the ownership boundary and the
verifier safety invariants that must not move with it.
"""

from __future__ import annotations

from app.capabilities.web_search import FetchedPage
from app.contracts.models import (
    AnalysisRequest,
    AnswerProposition,
    Constraint,
    EntityMention,
    EntityReference,
    EvidenceRequest,
    EvidenceSource,
    EvidenceSpan,
    Goal,
    PropositionKind,
    RelationKind,
    RetrievedDocument,
    SemanticInterpretation,
    ToolRequest,
    VerificationStatus,
)
from app.semantic.goals import RequirementBuilder
from app.semantic.relations import build_relation_semantics, relation_cue_tokens
from app.verification.evidence import verify_span
from app.verification.propositions import build_propositions, required_tokens
from app.web.span_selector import select_span_dicts


def _strike_semantic() -> SemanticInterpretation:
    return SemanticInterpretation(
        raw_query="What caused the 1994 Major League Baseball strike?",
        language="en",
        entities=[EntityMention(mention="Major League Baseball", type_hint="team")],
        entity_references=[EntityReference(surface="Major League Baseball", type_hint="team")],
        analysis_requests=[
            AnalysisRequest(text="causes of the 1994 MLB strike", objective="causes of the 1994 MLB strike")
        ],
        constraints=[Constraint(kind="season", value=1994)],
    )


def test_requirement_builder_attaches_canonical_relation_semantics():
    goal = RequirementBuilder().build(_strike_semantic())
    analysis = next(r for r in goal.requirements if r.kind.value == "ANALYSIS")
    assert analysis.relation_semantics is not None
    assert analysis.relation_semantics.relation_kind == RelationKind.CAUSE
    assert analysis.original_intent == "causes of the 1994 MLB strike"
    # The normalized statement and the immutable original intent are distinct fields.
    assert analysis.statement == "causes of the 1994 MLB strike"
    time_relation = next(r for r in goal.requirements if r.kind.value == "SEASON")
    assert time_relation.relation_semantics.relation_kind == RelationKind.SCOPE


def test_original_intent_is_not_overwritten_by_relation_semantics():
    goal = RequirementBuilder().build(_strike_semantic())
    analysis = next(r for r in goal.requirements if r.kind.value == "ANALYSIS")
    before = analysis.original_intent
    # Re-attaching an interpretation must never mutate the original request.
    analysis.relation_semantics = build_relation_semantics(text="effects of the strike", kind=RelationKind.EFFECT)
    assert analysis.original_intent == before


def test_propositions_carry_relation_semantics_and_relation_cues():
    goal = RequirementBuilder().build(_strike_semantic())
    propositions = build_propositions(goal)
    explanation = next(p for p in propositions if p.kind == PropositionKind.EXPLANATION)
    assert explanation.relation_semantics is not None
    assert explanation.relation_semantics.relation_kind == RelationKind.CAUSE
    tokens = required_tokens(explanation)
    # A specific causal passage can match without the generic summary word "cause(s)".
    assert {"proposed", "response"} <= tokens


def test_relation_cue_tokens_are_empty_for_non_relational_kinds():
    semantics = build_relation_semantics(text="identify the player", kind=RelationKind.IDENTITY)
    assert relation_cue_tokens(semantics) == set()


def test_status_relation_does_not_expand_required_tokens():
    """A current-status fact must not inherit the broad roster/trade vocabulary as support."""
    semantic = SemanticInterpretation(
        raw_query="Is there a recent injury report about Aaron Judge?",
        language="en",
        entities=[EntityMention(mention="Aaron Judge", type_hint="player")],
        entity_references=[EntityReference(surface="Aaron Judge", type_hint="player")],
        evidence_requests=[
            EvidenceRequest(
                text="find recent injury reports about Aaron Judge",
                objective="find recent injury reports about Aaron Judge",
            )
        ],
    )
    goal = RequirementBuilder().build(semantic)
    fact = next(p for p in build_propositions(goal) if p.kind == PropositionKind.FACT)
    tokens = required_tokens(fact)
    assert "injury" in tokens
    assert "roster" not in tokens
    assert "trade" not in tokens


def test_scope_window_reaches_bounded_distance():
    proposition = _cause_proposition().model_dump(exclude={"coverage"})
    body = (
        "Major League Baseball owners proposed a salary cap in response to financial losses. "
        "The union pushed back. "
        "Talks continued for weeks. "
        "The 1994 season ended prematurely."
    )
    spans = select_span_dicts(body, propositions=[proposition])
    assert spans
    assert any("1994" in span["text"] for span in spans)
    for span in spans:
        assert body[span["start"] : span["end"]] == span["text"]


def _document(body: str) -> RetrievedDocument:
    return RetrievedDocument(
        id="doc_1",
        url="https://example.com/strike",
        retrieved_at="2026-09-01T00:00:00Z",
        content_hash="hash",
        body_text=body,
        retrieval_status="OK",
    )


def _cause_proposition() -> AnswerProposition:
    goal = RequirementBuilder().build(_strike_semantic())
    return next(p for p in build_propositions(goal) if p.kind == PropositionKind.EXPLANATION)


def test_relation_aware_verifier_accepts_specific_causal_prose():
    proposition = _cause_proposition()
    body = (
        "In 1994, Major League Baseball owners proposed a salary cap in response to a "
        "worsening financial situation."
    )
    span = EvidenceSpan(
        document_id="doc_1", text=body, start=0, end=len(body),
        source_type=EvidenceSource.DOCUMENT_BODY, retrieval_status="OK",
    )
    from tests.assessment_fixtures import FixtureAssessorLLM

    # The causal relation is carried by relation cues ("in response to"), not by the
    # required-information nouns, so the assessor fixture is used without the token guard.
    finding = verify_span(
        proposition, span, _document(body),
        llm=FixtureAssessorLLM(strict_tokens=False), allow_llm=True,
    )
    assert finding.status == VerificationStatus.SUPPORTED


def test_scope_gate_still_blocks_a_causal_passage_without_the_requested_year():
    proposition = _cause_proposition()
    body = "Major League Baseball owners proposed a salary cap in response to financial losses."
    span = EvidenceSpan(
        document_id="doc_1", text=body, start=0, end=len(body),
        source_type=EvidenceSource.DOCUMENT_BODY, retrieval_status="OK",
    )
    finding = verify_span(proposition, span, _document(body), llm=None, allow_llm=False)
    assert finding.status == VerificationStatus.INSUFFICIENT
    assert finding.checks["time_state"] == "UNKNOWN"


def test_non_admissible_snippet_is_never_supported_even_with_relation_cues():
    proposition = _cause_proposition()
    body = "In 1994, Major League Baseball owners proposed a salary cap in response."
    span = EvidenceSpan(
        document_id="doc_1", text=body, start=0, end=len(body),
        source_type=EvidenceSource.SEARCH_SNIPPET, retrieval_status="OK",
    )
    finding = verify_span(proposition, span, _document(body), llm=None, allow_llm=False)
    assert finding.status == VerificationStatus.INSUFFICIENT
    assert finding.admissible is False


def test_span_selector_recovers_requested_scope_from_adjacent_sentence():
    proposition = _cause_proposition().model_dump(exclude={"coverage"})
    body = (
        "Major League Baseball owners proposed a salary cap in response to financial losses. "
        "The 1994 season ended prematurely."
    )
    spans = select_span_dicts(body, propositions=[proposition])
    assert spans
    # The relation sentence lacks the year; the bounded scope window recovers it.
    assert any("1994" in span["text"] for span in spans)
    for span in spans:
        assert body[span["start"] : span["end"]] == span["text"]


class _Hit:
    def __init__(self, url, title, engine="fixture"):
        self.url = url
        self.title = title
        self.snippet = ""
        self.engine = engine


class _Backend:
    name = "fixture"

    def __init__(self, hits):
        self._hits = list(hits)
        self.last_error = ""

    def search(self, query, *, limit=5, language="en"):
        return list(self._hits)


def test_web_evidence_findings_resolve_only_to_this_artifacts_fetched_spans(monkeypatch):
    """Relation-aware support still comes only from this capability's fetched body spans."""
    from app.capabilities.web_research import WebResearchTool
    from app.config import WebConfig
    from app.capabilities.web_research import WebResearchTool
    from app.config import WebConfig

    from tests.assessment_fixtures import FixtureAssessorLLM

    proposition = _cause_proposition()
    tool = WebResearchTool(WebConfig(broad_search=False), llm=FixtureAssessorLLM())
    tool.backends = [_Backend([_Hit("https://example.com/strike", "1994 Major League Baseball strike")])]

    def fake_fetch(url, config):
        return FetchedPage(
            url=url, final_url=url, title="1994 Major League Baseball strike",
            text="In 1994, Major League Baseball owners proposed a salary cap, causing the strike.",
            retrieved_at="2026-09-01T00:00:00Z", ok=True, content_hash="hash",
        )

    monkeypatch.setattr("app.capabilities.web_research.fetch_page", fake_fetch)
    request = ToolRequest(
        id="tr", need_id="n_web", capability="web_research", objective="causes of the strike",
        inputs={
            "entity_names": ["Major League Baseball"],
            "objective": "causes of the 1994 MLB strike",
            "user_wording": "What caused the 1994 Major League Baseball strike?",
            "propositions": [proposition.model_dump(exclude={"coverage"})],
        },
    )
    result = tool.execute(request)
    findings = result.artifact.structured_data["evidence_findings"]
    assert any(f["status"] == "SUPPORTED" for f in findings)
    # Every accepted finding resolves to a fetched document-body span of this artifact only.
    span_ids = {s["id"] for s in result.artifact.structured_data["evidence_spans"]}
    for finding in findings:
        assert finding["span_id"] in span_ids
