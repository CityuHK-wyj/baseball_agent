"""Phase 5B: evidence verification (F4) and completion soundness (F3).

These are general invariants, not named holdout questions. Web pages are deterministic
replayable fixtures; no network is used. Real DB/LLM/Web coverage lives in
``tests/test_integration_live.py`` (marked integration).
"""

from __future__ import annotations
from tests.m212_receipt_fixture import with_receipt

import dataclasses
import json

import pytest

from app.capabilities.web_research import WebResearchTool
from app.capabilities.web_search import FetchedPage
from app.config import WebConfig, get_config
from tools.requirement_ancestry_fixture import Goal, root_goal
from app.contracts.models import (
    AnalysisRequest,
    AnswerProjection,
    AnswerProposition,
    Artifact,
    Claim,
    ConsistencyStatus,
    ConversationState,
    CoverageState,
    EntityMention,
    EvidenceSpan,
    EvidenceFinding,
    Export,
    GoalStatus,
    PlannerNeed,
    PropositionKind,
    RequirementCompletion,
    RequirementKind,
    RetrievedDocument,
    Scope,
    SemanticInterpretation,
    ToolRequest,
    UserRequirement,
    VerificationStatus,
)
from app.observe.diagnostics import DiagnosticRecorder
from app.semantic.goals import RequirementBuilder
from app.verification.consistency import check_status_consistency
from app.verification.evidence import verify_span
from app.verification.judge import Judge
from app.verification.propositions import build_propositions
from tests.assessment_fixtures import FixtureAssessorLLM, support_llm

# --------------------------------------------------------------------------- helpers


def _prop(
    statement: str,
    required: str,
    *,
    subject: str = "Shohei Ohtani",
    kind: PropositionKind = PropositionKind.EVENT,
    time_scope: dict | None = None,
    population: str | None = None,
) -> AnswerProposition:
    return AnswerProposition(
        statement=statement,
        kind=kind,
        subject=subject,
        required_information=required,
        time_scope=time_scope or {},
        population=population,
    )


def _doc(title: str = "Ohtani news", url: str = "https://example.com/a", **kwargs) -> RetrievedDocument:
    return RetrievedDocument(
        id="doc-1",
        url=url, final_url=url, title=title, retrieved_at="2026-09-01T00:00:00Z",
        content_hash="hash-abc", **kwargs,
    )


def _span(text: str, doc_id: str = "doc-1") -> EvidenceSpan:
    return EvidenceSpan(document_id=doc_id, text=text, start=0, end=len(text), location="offset 0")


def _analysis_goal(objective: str, *, completion=RequirementCompletion.GROUNDED, background_ok=False) -> tuple[Goal, UserRequirement]:
    requirement = UserRequirement(
        id="r_analysis",
        kind=RequirementKind.ANALYSIS,
        statement=objective,
        value={"objective": objective, "background_ok": background_ok},
        completion=completion,
        background_ok=background_ok,
    )
    goal = Goal(statement=objective, raw_query=objective, requirements=[requirement])
    goal.propositions = build_propositions(goal)
    return goal, requirement


def _needs_for(requirement_id: str, need_id: str = "n_web", capability: str = "web_research") -> list[PlannerNeed]:
    return [PlannerNeed(id=need_id, objective="research", capability=capability, requires=[requirement_id])]


class _Hit:
    def __init__(self, url: str, title: str, engine: str = "fixture"):
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


def _run_web(monkeypatch, pages, props, *, objective="trade and performance change", subjects=("Shohei Ohtani",), need_id="n_web", llm=None):
    """pages: {url: (title, text, ok)} — deterministic replayable fixture."""
    hits = [_Hit(url, pages[url][0]) for url in pages]
    tool = WebResearchTool(WebConfig(broad_search=False), llm=llm)
    tool.backends = [_Backend(hits)]

    def fake_fetch(url, config):
        title, text, ok = pages[url]
        return FetchedPage(
            url=url, final_url=url, title=title, text=text,
            retrieved_at="2026-09-01T00:00:00Z", ok=ok,
            error="" if ok else "boom", error_code="" if ok else "FETCH_FAILED",
            content_hash=f"hash-{abs(hash(url)) % 100000}",
        )

    monkeypatch.setattr("app.capabilities.web_research.fetch_page", fake_fetch)
    request = ToolRequest(
        id=f"tr_{need_id}",
        need_id=need_id,
        capability="web_research",
        objective=objective,
        inputs={
            "entity_names": list(subjects),
            "objective": objective,
            "user_wording": "",
            "propositions": [p.model_dump(exclude={"coverage"}) for p in props],
        },
    )
    return tool.execute(request)


def test_search_does_not_starve_a_later_source():
    """A later, more appropriate source must not be starved by the first provider.

    M1 produced this with round-robin interleaving. M2-3 replaces round-robin with
    deterministic relationship-aware ranking; the invariant that matters is that every
    provider's candidates are present in the discovery set and that ordering is stable, not
    the particular interleave.
    """

    class _Backend:
        def __init__(self, name, urls):
            self.name = name
            self._urls = urls
            self.last_error = ""

        def search(self, query, *, limit=5, language="en"):
            return [_Hit(url, f"{self.name} result") for url in self._urls]

    from app.web.query_plan import build_query_plan

    tool = WebResearchTool(WebConfig(broad_search=False), llm=None)
    tool.backends = [
        _Backend("publisher_feeds", ["https://feed/1", "https://feed/2"]),
        _Backend("wikipedia", ["https://wiki/1", "https://wiki/2"]),
    ]
    plan = build_query_plan(objective="q", subjects=())
    hits, _errors, _attempted = tool._search(plan, "en")
    urls = [hit.url for hit in hits]
    assert set(urls) == {"https://feed/1", "https://feed/2", "https://wiki/1", "https://wiki/2"}
    # Deterministic across repeated discovery.
    again, _e, _a = tool._search(plan, "en")
    assert [hit.url for hit in again] == urls


# ===================================================== F4: deterministic verification


def test_relevance_is_not_support_subject_but_not_event():
    finding = verify_span(_prop("the player was traded", "trade"), _span("Shohei Ohtani had a strong 2024 season."), _doc())
    assert finding.status == VerificationStatus.INSUFFICIENT


def test_correct_event_wrong_year_is_not_support():
    proposition = _prop("the player was traded", "trade", time_scope={"season": 2024})
    finding = verify_span(proposition, _span("Shohei Ohtani was traded in 2019."), _doc())
    assert finding.status == VerificationStatus.INSUFFICIENT
    assert finding.checks["time_conflict"] is True


def test_correct_value_wrong_entity_is_irrelevant():
    proposition = _prop("Ohtani hit 54 home runs", "home runs", kind=PropositionKind.VALUE)
    finding = verify_span(proposition, _span("Aaron Judge hit 54 home runs in 2024."), _doc(body_text="Aaron Judge hit 54 home runs in 2024."))
    assert finding.status == VerificationStatus.IRRELEVANT


def test_correct_population_required():
    proposition = _prop("postseason home runs", "home runs", population="POSTSEASON")
    finding = verify_span(proposition, _span("Shohei Ohtani hit home runs in the regular season."), _doc())
    assert finding.status == VerificationStatus.INSUFFICIENT
    assert finding.checks["population_conflict"] is True


def test_matching_passage_is_supported_and_keeps_context():
    proposition = _prop("the player was traded", "trade", time_scope={"season": 2024})
    document = _doc(title="Ohtani trade", url="https://example.com/trade", body_text="Shohei Ohtani was traded in 2024.")
    finding = verify_span(
        proposition, _span("Shohei Ohtani was traded in 2024."), document,
        llm=support_llm(metric="trade"),
    )
    assert finding.status == VerificationStatus.SUPPORTED
    assert finding.source_url == "https://example.com/trade"
    assert finding.content_hash == "hash-abc"


def test_explicit_contradiction_is_recorded_not_dropped():
    proposition = _prop("the player was traded", "trade")
    finding = verify_span(proposition, _span("Shohei Ohtani did not get traded in 2024."), _doc(body_text="Shohei Ohtani did not get traded in 2024."))
    assert finding.status == VerificationStatus.CONTRADICTED


# ================================================= F4: web tool + Judge integration


def test_subject_only_page_leaves_proposition_unsupported(monkeypatch):
    goal, requirement = _analysis_goal("trade and performance change")
    props = goal.propositions
    pages = {"https://example.com/a": ("Ohtani 2024 season", "Shohei Ohtani had a strong 2024 season. " * 6, True)}
    result = _run_web(monkeypatch, pages, props)

    assert result.outcome.status == "NO_DATA"
    assert result.outcome.code == "INSUFFICIENT_EVIDENCE"
    projection = Judge().evaluate(goal, [result.artifact], _needs_for(requirement.id))
    proposition = next(p for p in projection.propositions if p.requirement_id == requirement.id)
    assert proposition.coverage == CoverageState.UNSUPPORTED
    assert projection.goal_status != GoalStatus.COMPLETE


def test_wrong_year_page_does_not_support_a_dated_proposition(monkeypatch):
    goal, requirement = _analysis_goal("trade")
    goal.propositions[0].time_scope = {"season": 2024}
    props = goal.propositions
    pages = {"https://example.com/a": ("Ohtani trade", "Shohei Ohtani was traded in 2019. " * 6, True)}
    result = _run_web(monkeypatch, pages, props, objective="trade")

    projection = Judge().evaluate(goal, [result.artifact], _needs_for(requirement.id))
    proposition = next(p for p in projection.propositions if p.requirement_id == requirement.id)
    assert proposition.coverage in {CoverageState.UNSUPPORTED, CoverageState.PARTIAL}
    assert projection.goal_status != GoalStatus.COMPLETE


def test_supported_page_preserves_url_passage_and_claim_support(monkeypatch):
    goal, requirement = _analysis_goal("trade and performance change")
    goal.propositions[0].time_scope = {"season": 2024}
    props = goal.propositions
    pages = {
        "https://example.com/trade": (
            "Ohtani trade analysis",
            "Shohei Ohtani was traded in 2024 and his performance change was documented. " * 6,
            True,
        )
    }
    result = _run_web(
        monkeypatch, pages, props, objective="trade and performance change",
        llm=FixtureAssessorLLM(),
    )

    assert result.outcome.status == "SUCCESS"
    finding = result.artifact.structured_data["findings"][0]
    assert finding["url"] == "https://example.com/trade"
    assert finding["content_hash"]
    assert finding["evidence_spans"] and finding["evidence_spans"][0]["start"] == 0
    document = result.artifact.structured_data["documents"][0]
    assert document["retrieval_status"] == "OK"
    assert document["url"] == "https://example.com/trade"

    projection = Judge().evaluate(goal, [result.artifact], _needs_for(requirement.id))
    proposition = next(p for p in projection.propositions if p.requirement_id == requirement.id)
    # Tool success/inspectable provenance does not establish comparative entailment:
    # this passage says only that a change was documented, with no operands or relation.
    assert proposition.kind == PropositionKind.COMPARISON
    assert proposition.coverage == CoverageState.PARTIAL
    assert not projection.claims


def test_unavailable_full_text_does_not_invent_document_evidence(monkeypatch):
    goal, requirement = _analysis_goal("trade and performance change")
    props = goal.propositions
    pages = {"https://example.com/a": ("Ohtani trade explained", "", False)}
    result = _run_web(monkeypatch, pages, props)

    assert result.outcome.status == "FAILED"
    assert result.outcome.code == "SOURCE_UNAVAILABLE"
    # No fabricated span or claim is produced; the failed fetch is recorded for inspection.
    assert result.artifact is not None
    assert result.artifact.structured_data["findings"] == []
    document = result.artifact.structured_data["documents"][0]
    assert document["retrieval_status"] == "FETCH_FAILED"
    assert not any(f.get("evidence_spans") for f in result.artifact.structured_data["findings"])
    projection = Judge().evaluate(goal, [result.artifact], _needs_for(requirement.id))
    assert projection.goal_status != GoalStatus.COMPLETE


def test_conflicting_sources_retain_the_conflict(monkeypatch):
    goal, requirement = _analysis_goal("trade")
    goal.propositions[0].time_scope = {"season": 2024}
    props = goal.propositions
    pages = {
        "https://example.com/a": ("Trade reported", "Shohei Ohtani was traded in 2024. " * 6, True),
        "https://example.com/b": ("Trade denied", "Shohei Ohtani did not get traded in 2024. " * 6, True),
    }
    result = _run_web(monkeypatch, pages, props, objective="trade", llm=FixtureAssessorLLM())

    assert result.outcome.status == "SUCCESS"
    assert result.outcome.code == "CONFLICTING_EVIDENCE"
    assert result.artifact.status == "PARTIAL"
    statuses = {f["status"] for f in result.artifact.structured_data["evidence_findings"]}
    assert VerificationStatus.SUPPORTED.value in statuses
    assert VerificationStatus.CONTRADICTED.value in statuses
    projection = Judge().evaluate(goal, [result.artifact], _needs_for(requirement.id))
    proposition = next(p for p in projection.propositions if p.requirement_id == requirement.id)
    assert proposition.coverage == CoverageState.PARTIAL
    assert projection.goal_status != GoalStatus.COMPLETE


# ============================================================= F3: completion soundness


def test_explanatory_question_completed_by_web_without_a_statistical_artifact():
    goal, requirement = _analysis_goal(
        "explain the mechanical adjustment behind the home run increase",
        completion=RequirementCompletion.EXPLANATION,
    )
    proposition = goal.propositions[0]
    web = Artifact(
        id="art_web", kind="WEB_EVIDENCE",
        structured_data={
            "proposition_verification": True,
            "findings": [{
                "document_id": "doc1", "url": "https://example.com/i", "title": "Interview",
                "grounded": True, "relevant": True,
                "evidence_spans": [{"text": "The mechanical adjustment behind the home run increase was described."}],
                "proposition_ids": [proposition.id],
                "proposition_findings": [{"proposition_id": proposition.id, "status": VerificationStatus.SUPPORTED.value}],
            }],
            "evidence_findings": [{
                "id": "ef1", "proposition_id": proposition.id, "document_id": "doc1", "span_id": "s1",
                "status": VerificationStatus.SUPPORTED.value, "verifier": "deterministic",
            }],
        },
        actual_scope=Scope(source="WEB"),
        need_id="n_web",
    )
    _persist_fixture_chain(web, proposition, "The mechanical adjustment behind the home run increase was described.")
    projection = Judge().evaluate(goal, [web], _needs_for(requirement.id))
    assert projection.goal_status == GoalStatus.COMPLETE
    assert any(proposition.id in claim.proposition_ids for claim in projection.claims)


def test_missing_second_metric_is_partial_not_false_complete():
    goal = Goal(
        statement="two measures",
        raw_query="HR and RBI",
        requirements=[
            UserRequirement(id="r_hr", kind=RequirementKind.MEASURE, statement="HR", value={"key": "home_runs", "label": "Home runs", "derivable": True}, completion=RequirementCompletion.VALUE),
            UserRequirement(id="r_rbi", kind=RequirementKind.MEASURE, statement="RBI", value={"key": "rbi", "label": "RBI", "derivable": False}, completion=RequirementCompletion.VALUE),
        ],
    )
    goal.propositions = build_propositions(goal)
    db = Artifact(
        id="art_db", kind="DATABASE_RESULT",
        structured_data={"rows": [{"home_runs": 10}], "row_count": 1, "actual_measures": [{"column": "home_runs", "key": "home_runs", "verified": True}]},
        actual_scope=Scope(season=2024, measures=["home_runs"], source="POSTGRES"),
        need_id="n_db",
    )
    needs = [PlannerNeed(id="n_db", objective="stats", capability="local_analytics", requires=["r_hr", "r_rbi"])]
    goal.requirements.append(UserRequirement(id="r_bound_season",kind=RequirementKind.SEASON,statement="2024",value=2024))
    root_goal(goal)
    goal.propositions = build_propositions(goal)
    db = with_receipt(db)
    projection = Judge().evaluate(goal, [db], needs)
    assert projection.goal_status == GoalStatus.LIMITED
    # The supported HR result survives; nothing is invented for RBI.
    assert projection.claims
    rbi = next(p for p in projection.propositions if p.requirement_id == "r_rbi")
    assert rbi.coverage in {CoverageState.UNSUPPORTED, CoverageState.BLOCKED}


def test_irrelevant_pages_cannot_make_a_multi_part_research_complete(monkeypatch):
    goal, requirement = _analysis_goal("trade and performance change")
    props = goal.propositions
    first = _run_web(
        monkeypatch,
        {"https://example.com/a": ("Ohtani biography", "Shohei Ohtani was born in Japan and plays baseball. " * 6, True)},
        props, need_id="n_web_a",
    )
    second = _run_web(
        monkeypatch,
        {"https://example.com/b": ("Ohtani awards", "Shohei Ohtani won several awards in his career. " * 6, True)},
        props, need_id="n_web_b",
    )
    assert first.artifact is not None and second.artifact is not None
    projection = Judge().evaluate(goal, [first.artifact, second.artifact], _needs_for(requirement.id))
    assert projection.goal_status != GoalStatus.COMPLETE
    proposition = next(p for p in projection.propositions if p.requirement_id == requirement.id)
    assert proposition.coverage != CoverageState.SATISFIED


def test_generic_knowledge_candidate_cannot_upgrade_a_partial_analysis():
    """A retrieved generic definition must not satisfy an on-topic analysis proposition.

    Regression for a live defect: an ERA definition artifact (a PARTIAL candidate) was
    upgraded to SATISFIED merely because it was a KNOWLEDGE_ITEM, while the real question
    about a player's 2025 performance was unanswered.
    """
    goal = Goal(
        statement="perf",
        raw_query="大谷翔平2025赛季打击表现如何？",
        requirements=[
            UserRequirement(id="r_e", kind=RequirementKind.ENTITY, statement="identify", value={"mention": "大谷翔平"}),
            UserRequirement(id="r_a", kind=RequirementKind.ANALYSIS, statement="overall batting performance", value={"objective": "overall batting performance"}, completion=RequirementCompletion.GROUNDED),
        ],
    )
    goal.propositions = build_propositions(goal)
    entity = Artifact(
        id="art_e", kind="ENTITY_RESOLUTION",
        structured_data={"resolved": [{"mention": "大谷翔平", "id": 660271, "name": "Shohei Ohtani"}]},
        actual_scope=Scope(entities=["660271"], entity_names=["Shohei Ohtani"]), need_id="n1",
    )
    knowledge = Artifact(
        id="art_k", kind="KNOWLEDGE_ITEM",
        structured_data={"items": [{"id": "GLOSSARY:ERA", "title": "Earned Run Average (ERA)", "summary": "Earned runs per nine innings.", "aliases": ["era"]}]},
        text_content="Earned Run Average (ERA): Earned runs per nine innings.",
        actual_scope=Scope(source="SHARED_KNOWLEDGE"), need_id="n5",
    )
    web = Artifact(
        id="art_w", kind="WEB_EVIDENCE",
        structured_data={
            "proposition_verification": True,
            "findings": [{"grounded": True, "relevant": True, "evidence_spans": [{"text": "大谷翔平 2025 awards"}], "proposition_ids": [], "proposition_findings": []}],
            "evidence_findings": [],
        },
        actual_scope=Scope(source="WEB"), need_id="n3",
    )
    needs = [
        PlannerNeed(id="n1", objective="resolve", capability="entity_resolution", requires=["r_e"]),
        PlannerNeed(id="n5", objective="knowledge", capability="shared_knowledge", requires=["r_a"]),
        PlannerNeed(id="n3", objective="web", capability="web_research", requires=["r_a"]),
    ]
    projection = Judge().evaluate(goal, [entity, knowledge, web], needs)
    analysis_prop = next(p for p in projection.propositions if p.requirement_id == "r_a")
    assert analysis_prop.coverage != CoverageState.SATISFIED
    assert projection.goal_status != GoalStatus.COMPLETE


def test_league_candidate_identity_completes_history_but_never_a_db_claim():
    """A league-level historical explanation completes on candidate identity; a database
    claim still requires a canonical id (completion != DB execution gate)."""
    goal = Goal(
        statement="1994 strike",
        raw_query="解释一下1994年美国职棒大联盟罢工的原因和影响。",
        requirements=[
            UserRequirement(id="r_e", kind=RequirementKind.ENTITY, statement="identify MLB", value={"mention": "美国职棒大联盟", "type_hint": "league"}),
            UserRequirement(id="r_a", kind=RequirementKind.ANALYSIS, statement="explain the causes and consequences of the 1994 MLB strike", value={"objective": "explain the causes and consequences of the 1994 MLB strike"}, completion=RequirementCompletion.EXPLANATION),
        ],
    )
    goal.propositions = build_propositions(goal)
    explanation = next(p for p in goal.propositions if p.requirement_id == "r_a")
    entity = Artifact(
        id="art_e", kind="ENTITY_RESOLUTION",
        structured_data={
            "identity": {"美国职棒大联盟": {"level": "CANDIDATE", "id": None, "name": "Major League Baseball"}},
            "candidates": [{"mention": "美国职棒大联盟", "name": "Major League Baseball"}],
            "resolved": [],
        },
        actual_scope=Scope(source="ENTITY"), need_id="n1",
    )
    web = Artifact(
        id="art_w", kind="WEB_EVIDENCE",
        structured_data={
            "proposition_verification": True,
            "findings": [{
                "grounded": True, "relevant": True,
                "evidence_spans": [{"text": "The 1994 strike cancelled the World Series."}],
                "proposition_ids": [explanation.id],
                "proposition_findings": [{"proposition_id": explanation.id, "status": VerificationStatus.SUPPORTED.value}],
            }],
            "evidence_findings": [{"id": "ef1", "proposition_id": explanation.id, "status": VerificationStatus.SUPPORTED.value}],
        },
        actual_scope=Scope(source="WEB", season=1994), need_id="n2",
    )
    db = Artifact(
        id="art_db", kind="DATABASE_RESULT",
        structured_data={"rows": [{"home_runs": 1}], "row_count": 1},
        actual_scope=Scope(season=1994, source="POSTGRES"), need_id="n3",
    )
    needs = [
        PlannerNeed(id="n1", objective="resolve", capability="entity_resolution", requires=["r_e"]),
        PlannerNeed(id="n2", objective="web", capability="web_research", requires=["r_a"]),
        PlannerNeed(id="n3", objective="db", capability="local_analytics", requires=["r_a"]),
    ]
    _persist_fixture_chain(web, explanation, "The 1994 strike cancelled the World Series.")
    projection = Judge().evaluate(goal, [entity, web, db], needs)
    assert projection.goal_status == GoalStatus.COMPLETE
    assert not any(ref.source == "artifact" for claim in projection.claims for ref in claim.support_refs)


def test_supported_analysis_survives_unsupported_causal_explanation():
    analysis = UserRequirement(id="r_stats", kind=RequirementKind.ANALYSIS, statement="Report batter batting average", value={"objective": "Report batter batting average"}, completion=RequirementCompletion.GROUNDED)
    cause = UserRequirement(id="r_cause", kind=RequirementKind.ANALYSIS, statement="explain why", value={"objective": "explain the cause"}, completion=RequirementCompletion.EXPLANATION, background_ok=False)
    goal = Goal(statement="g", raw_query="Report batter batting average in 2024; Explain the cause.", requirements=[analysis, cause])
    goal.propositions = build_propositions(goal)
    db = Artifact(
        id="art_db", kind="DATABASE_RESULT",
        structured_data={"rows": [{"avg": 0.3}], "row_count": 1},
        text_content="avg\n0.3",
        actual_scope=Scope(season=2024, source="POSTGRES"),
        need_id="n_db",
    )
    needs = [
        PlannerNeed(id="n_db", objective="stats", capability="local_analytics", requires=["r_stats"]),
        PlannerNeed(id="n_web", objective="cause", capability="web_research", requires=["r_cause"]),
    ]
    goal.requirements.append(UserRequirement(id="r_bound_season",kind=RequirementKind.SEASON,statement="2024",value=2024))
    root_goal(goal)
    goal.propositions = build_propositions(goal)
    db = with_receipt(db)
    projection = Judge().evaluate(goal, [db], needs)
    assert projection.claims, "the supported analysis claim must survive"
    assert projection.goal_status == GoalStatus.LIMITED
    cause_prop = next(p for p in projection.propositions if p.requirement_id == "r_cause")
    assert cause_prop.coverage != CoverageState.SATISFIED


def test_league_level_history_does_not_require_entity_identity(monkeypatch):
    # A historical/league-level question has no ENTITY requirement, so no IDENTITY
    # proposition is created and no entity artifact is needed to complete it.
    interpreter = __import__("app.semantic.interpreter", fromlist=["SemanticInterpreter"]).SemanticInterpreter(llm=None)
    semantic = SemanticInterpretation(
        raw_query="Explain the 1994 baseball strike and its impact.",
        entities=[],
        analysis_requests=[AnalysisRequest(text="explain the 1994 strike", objective="explain the 1994 strike and its impact")],
        turn_type="INDEPENDENT_GOAL",
    )
    goal = RequirementBuilder().build(semantic)
    goal.propositions = build_propositions(goal, semantic)
    assert not [r for r in goal.requirements if r.kind == RequirementKind.ENTITY]
    assert not [p for p in goal.propositions if p.kind == PropositionKind.IDENTITY]


# ============================================================ F3: consistency check


def test_consistency_detects_composer_retraction_of_a_mandatory_result():
    requirement = UserRequirement(id="r1", kind=RequirementKind.ANALYSIS, statement="trade investigation", value={"objective": "trade and before/after performance change"}, completion=RequirementCompletion.GROUNDED)
    goal = Goal(statement="trade investigation", raw_query="investigate the trade", requirements=[requirement])
    goal.propositions = build_propositions(goal)
    proposition = goal.propositions[0]
    proposition.coverage = CoverageState.SATISFIED
    proposition.required_information = "trade and before/after performance change"
    claim = Claim(text="The player was traded; OPS went from .750 to .900.", proposition_ids=[proposition.id])
    projection = AnswerProjection(goal_id=goal.id, goal_status=GoalStatus.COMPLETE, claims=[claim], propositions=goal.propositions)

    consistency = check_status_consistency(goal, projection, "I cannot provide a complete example; the trade and before/after performance data are unavailable.", llm=None)
    assert consistency.status == ConsistencyStatus.DOWNGRADED
    assert proposition.id in consistency.retracted_proposition_ids


def test_consistency_accepts_an_answer_that_communicates_the_result():
    requirement = UserRequirement(id="r1", kind=RequirementKind.ANALYSIS, statement="trade investigation", value={"objective": "trade and before/after performance change"}, completion=RequirementCompletion.GROUNDED)
    goal = Goal(statement="trade investigation", raw_query="investigate the trade", requirements=[requirement])
    goal.propositions = build_propositions(goal)
    proposition = goal.propositions[0]
    proposition.coverage = CoverageState.SATISFIED
    proposition.required_information = "trade and before/after performance change"
    claim = Claim(text="The player was traded; OPS went from .750 to .900.", proposition_ids=[proposition.id])
    projection = AnswerProjection(goal_id=goal.id, goal_status=GoalStatus.COMPLETE, claims=[claim], propositions=goal.propositions)

    consistency = check_status_consistency(goal, projection, "The player was traded and his OPS changed from .750 to .900.", llm=None)
    assert consistency.status == ConsistencyStatus.CONSISTENT


# ============================================================ F5: diagnostics


def test_diagnostics_preserve_evidence_and_redact_secrets(tmp_path):
    path = tmp_path / "diag.json"
    recorder = DiagnosticRecorder(path, max_text=200)
    recorder.record(
        "web_research",
        url="https://example.com/a",
        api_key="super-secret-value",
        span="X" * 500,
        findings=[{"url": "https://example.com/a", "evidence_spans": [{"text": "the passage"}]}],
    )
    assert recorder.write() == str(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    data = payload[0]["data"]
    assert data["api_key"] == "***"
    assert data["url"] == "<omitted>"
    assert data["span"] == "<omitted>"
    assert data["findings"][0]["evidence_spans"][0]["text"] == "<omitted>"


def _persist_fixture_chain(artifact, proposition, text):
    """Supply real fixture documents instead of relying on optimistic legacy admission."""
    document = _doc(body_text=text)
    span = _span(text)
    finding = EvidenceFinding(proposition_id=proposition.id, document_id=document.id, span_id=span.id,
        status=VerificationStatus.SUPPORTED, admissible=True, source_url=document.url,
        content_hash=document.content_hash, retrieved_at=document.retrieved_at)
    from app.verification.lineage import text_hash, proposition_signature
    finding.span_hash = text_hash(text)
    finding.proposition_signature = proposition_signature(proposition)
    data = artifact.structured_data
    data["documents"] = [document.model_dump(mode="json")]
    data["evidence_spans"] = [span.model_dump(mode="json")]
    data["evidence_findings"] = [finding.model_dump(mode="json")]
    data["findings"][0]["proposition_findings"] = data["evidence_findings"]


def test_diagnostics_path_is_ignored_by_git():
    import subprocess

    result = subprocess.run(["git", "check-ignore", ".runtime/diagnostics/x.json"], capture_output=True, text=True)
    assert result.returncode == 0, "runtime diagnostics must stay out of version control"


# ============================================================ engine-level F3 path


class _StubInterpreter:
    def __init__(self, semantic: SemanticInterpretation):
        self._semantic = semantic

    def interpret(self, *args, **kwargs) -> SemanticInterpretation:
        return self._semantic.model_copy(deep=True)


class _StubPlanner:
    def __init__(self, factory):
        self._factory = factory

    def plan(self, **kwargs):
        return self._factory(kwargs), "stub", []


def _engine_without_live_sources(tmp_path):
    from app.runtime.engine import Engine

    base = get_config()
    broken_pg = dataclasses.replace(base.postgres, port=59999)
    no_llm = dataclasses.replace(base.llm, api_key="")
    return Engine(dataclasses.replace(base, postgres=broken_pg, llm=no_llm, runtime_dir=tmp_path))


def test_engine_repairs_retracting_composer_with_verified_claims(tmp_path):
    engine = _engine_without_live_sources(tmp_path)
    semantic = SemanticInterpretation(
        raw_query="investigate the trade",
        entities=[EntityMention(mention="Shohei Ohtani", type_hint="player")],
        analysis_requests=[AnalysisRequest(text="trade", objective="trade and before/after performance change")],
    )
    engine.interpreter = _StubInterpreter(semantic)
    engine.planner = _StubPlanner(lambda kwargs: [])

    def fake_evaluate(goal, artifacts, needs, **kwargs):
        from tests.governed_state_fixture import approved_snapshot
        propositions = goal.propositions
        for proposition in propositions:
            proposition.coverage = CoverageState.SATISFIED
        return approved_snapshot(AnswerProjection(
            goal_id=goal.id,
            goal_status=GoalStatus.COMPLETE,
            claims=[Claim(text="The player was traded; OPS went from .750 to .900.", proposition_ids=[p.id for p in propositions])],
            propositions=propositions,
        ))

    engine.judge.evaluate = fake_evaluate
    engine.composer.compose = lambda *args, **kwargs: "I cannot provide a complete example; the trade and before/after performance data are unavailable."

    result = engine.ask("p5b_engine_consistency", "investigate the trade", trace_level="off")

    # The runtime prefers a bounded repair built from the accepted claims over silently
    # changing the evidence judgment; the verified result is now expressed.
    assert result.projection.goal_status == GoalStatus.COMPLETE
    assert result.consistency is not None
    assert result.consistency.status == ConsistencyStatus.CONSISTENT
    assert result.consistency.method == "deterministic_repair"
    assert result.projection.consistency is None
    assert ".750" in result.answer and ".900" in result.answer


def test_engine_rejects_upstream_inconsistent_authority_without_presentation_mutation(tmp_path):
    engine = _engine_without_live_sources(tmp_path)
    semantic = SemanticInterpretation(
        raw_query="investigate the trade",
        entities=[EntityMention(mention="Shohei Ohtani", type_hint="player")],
        analysis_requests=[AnalysisRequest(text="trade", objective="trade and before/after performance change")],
    )
    engine.interpreter = _StubInterpreter(semantic)
    engine.planner = _StubPlanner(lambda kwargs: [])

    def fake_evaluate(goal, artifacts, needs, **kwargs):
        propositions = goal.propositions
        for proposition in propositions:
            proposition.coverage = CoverageState.SATISFIED
        # No claim carries the result, so no bounded repair is possible.
        return AnswerProjection(goal_id=goal.id, goal_status=GoalStatus.COMPLETE, claims=[], propositions=propositions)

    engine.judge.evaluate = fake_evaluate
    engine.composer.compose = lambda *args, **kwargs: "The result could not be provided."

    # The mock Judge violates its own integrity contract. The bounded Judge recovery
    # cannot cure it, so the runtime must return a governed LIMITED fallback instead of
    # exposing an internal exception or silently marking the Goal COMPLETE.
    result = engine.ask("p5b_engine_consistency_fail", "investigate the trade", trace_level="off")
    assert result.projection.goal_status == GoalStatus.LIMITED
    assert result.projection.claims == []
    assert any("integrity recovery" in note for note in result.projection.limitations)


def test_engine_does_not_require_entity_resolution_for_league_history(tmp_path):
    from app.observe.trace import Trace
    from app.runtime.context import ExecutionContext

    engine = _engine_without_live_sources(tmp_path)
    goal = RequirementBuilder().build(
        SemanticInterpretation(
            raw_query="Explain the 1994 baseball strike and its impact.",
            analysis_requests=[AnalysisRequest(text="explain", objective="explain the 1994 strike")],
        )
    )
    ctx = ExecutionContext(goal=goal, conversation=ConversationState(session_id="s"))
    needs = [PlannerNeed(id="n1", objective="research", capability="web_research")]
    engine._ensure_entity_prerequisite(needs, ctx, Trace("off"))
    assert all(need.capability != "entity_resolution" for need in needs)
