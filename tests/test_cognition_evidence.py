"""Cognition vs. Evidence: hypotheses guide investigation, evidence gates claims.

Covers the Phase-4 corrections:
- explanatory questions do not force a statistical requirement;
- a dictionary miss still yields a candidate identity usable by open research;
- a candidate identity cannot enter a capability that needs a canonical id;
- one unresolved requirement does not erase an independently supported claim;
- web search failure is distinguished from genuinely empty / irrelevant results;
- background knowledge is allowed only for non-high-risk explanations and never cited.
"""

from __future__ import annotations
from tests.m212_receipt_fixture import with_receipt

import pytest

from app.capabilities.base import CapabilitySpec
from app.capabilities.web_search import FetchedPage
from tools.requirement_ancestry_fixture import Goal
from app.contracts.models import (
    AnalysisRequest,
    Artifact,
    Claim,
    Constraint,
    EntityMention,
    EpistemicStatus,
    Export,
    GoalStatus,
    IdentityLevel,
    PlannerNeed,
    RequirementCompletion,
    RequirementKind,
    RequirementStatus,
    Scope,
    SemanticInterpretation,
    ToolRequest,
    UserRequirement,
)
from app.capabilities.web_research import WebResearchTool
from app.semantic.goals import RequirementBuilder, is_explanatory_question, is_high_risk, wants_value
from app.semantic.interpreter import SemanticInterpreter
from app.verification.judge import Judge


# --------------------------------------------------------------------- requirements


def _semantic(raw: str, *, measure: str | None = None, with_entity: bool = True) -> SemanticInterpretation:
    return SemanticInterpretation(
        raw_query=raw,
        language="zh",
        entities=[EntityMention(mention="大谷翔平", type_hint="player")] if with_entity else [],
        constraints=[Constraint(kind="season", value=2025)] + (
            [Constraint(kind="measure", value=measure)] if measure else []
        ),
        analysis_requests=[AnalysisRequest(text=raw, objective=raw)],
    )


def test_explanation_question_does_not_force_a_measure():
    goal = RequirementBuilder().build(_semantic("为什么大谷翔平2025年的本垒打变多了？"))
    measures = [r for r in goal.requirements if r.kind == RequirementKind.MEASURE]
    assert measures and all(not r.required for r in measures)
    analysis = [r for r in goal.requirements if r.kind == RequirementKind.ANALYSIS]
    assert analysis and analysis[0].completion == RequirementCompletion.EXPLANATION
    assert analysis[0].background_ok is True


def test_value_question_keeps_measure_required():
    goal = RequirementBuilder().build(_semantic("大谷翔平2025年本垒打是多少？"))
    measures = [r for r in goal.requirements if r.kind == RequirementKind.MEASURE]
    assert measures and measures[0].required is True
    assert measures[0].completion == RequirementCompletion.VALUE


def test_scope_requirements_have_scope_completion():
    goal = RequirementBuilder().build(_semantic("大谷翔平2025赛季表现如何？"))
    seasons = [r for r in goal.requirements if r.kind == RequirementKind.SEASON]
    assert seasons and seasons[0].completion == RequirementCompletion.SCOPE


def test_high_risk_explanation_disallows_background():
    goal = RequirementBuilder().build(_semantic("为什么会有大谷翔平受伤的传闻？"))
    analysis = [r for r in goal.requirements if r.kind == RequirementKind.ANALYSIS]
    assert analysis and analysis[0].background_ok is False


def test_intent_helpers():
    assert is_explanatory_question("为什么他变强了")
    assert not is_explanatory_question("他2025年打击表现如何")
    assert wants_value("本垒打是多少")
    assert not wants_value("他有多强")
    assert is_high_risk("他现在在哪支球队")
    assert is_high_risk("有没有伤病报道")
    assert not is_high_risk("解释一下什么是OPS")


# --------------------------------------------------------------- entity identity levels


class _EmptyDirectory:
    def search_by_name(self, name, limit=5):
        return []


class _EmptyStore:
    def match_alias(self, text):
        return None

    def record_candidate(self, *args, **kwargs):
        return ""


def _candidate_request(mention="Babe Ruth"):
    return ToolRequest(
        need_id="n1",
        capability="entity_resolution",
        objective="resolve",
        inputs={"mentions": [{"mention": mention, "type_hint": "player"}], "raw_query": f"{mention} 1927 表现如何"},
    )


def test_dictionary_miss_yields_candidate_not_failure():
    from app.capabilities.entity_resolution import EntityResolutionTool

    tool = EntityResolutionTool(_EmptyStore(), _EmptyDirectory(), llm=None)
    result = tool.execute(_candidate_request())
    assert result.artifact is not None
    assert result.outcome.status == "SUCCESS"
    assert result.outcome.code == "CANDIDATE_ONLY"
    identity = result.artifact.structured_data["identity"]["Babe Ruth"]
    assert identity["level"] == IdentityLevel.CANDIDATE.value
    assert identity["id"] is None
    mapping = result.artifact.export("ENTITY_MAPPING")
    assert mapping is not None and mapping.payload["candidates"]


def test_candidate_identity_cannot_enter_canonical_capability():
    """A candidate export may feed open research but never a canonical-id capability."""
    from app.capabilities.entity_resolution import EntityResolutionTool
    from app.capabilities.registry import CapabilityRegistry
    from app.runtime.binder import RuntimeBinder

    class _CanonicalCapability:
        spec = CapabilitySpec(name="local_analytics", description="", accepts=["PLAYER_ID_SET"], produces=["STATISTICAL_RESULT"])

        def execute(self, request, ctx):  # pragma: no cover
            raise NotImplementedError

    registry = CapabilityRegistry()
    registry.register(_CanonicalCapability())
    tool = EntityResolutionTool(_EmptyStore(), _EmptyDirectory(), llm=None)
    artifact = tool.execute(_candidate_request()).artifact
    need = PlannerNeed(id="n2", objective="stats", capability="local_analytics", depends_on=["n1"], input_requirements=["PLAYER_ID_SET"])
    result = RuntimeBinder(registry).bind(need, [], [artifact])
    assert not result.ok
    assert "EMPTY_ENTITY_SET" in result.failure_codes()


# --------------------------------------------------------------------- judge gating


def _entity_artifact(mention="大谷翔平", player_id=660271, name="Shohei Ohtani") -> Artifact:
    return Artifact(
        id="art_entity",
        kind="ENTITY_RESOLUTION",
        structured_data={"resolved": [{"mention": mention, "id": player_id, "name": name, "role": "BATTER"}]},
        exports=[Export(id="exp_entities", type="PLAYER_ID_SET", payload={"ids": [player_id], "names": [name]})],
        actual_scope=Scope(entities=[str(player_id)], entity_names=[name]),
        need_id="n1",
    )


def _db_artifact(*, keys, rows=None, season=2025, entities=("660271",), need_id="n2") -> Artifact:
    return with_receipt(Artifact(
        id="art_db",
        kind="DATABASE_RESULT",
        structured_data={
            "rows": rows if rows is not None else [{"home_runs": 10}],
            "row_count": 1,
            "actual_measures": [{"column": k, "key": k, "verified": True} for k in keys],
        },
        text_content="home_runs\n10",
        actual_scope=Scope(entities=list(entities), season=season, measures=list(keys), source="POSTGRES"),
        need_id=need_id,
    ))


def _web_artifact(*, text="Shohei Ohtani hit 55 home runs in 2025.", relevant=True, need_id="n3") -> Artifact:
    return Artifact(
        id="art_web",
        kind="WEB_EVIDENCE",
        structured_data={
            "findings": [{"url": "https://example.com", "title": "Ohtani", "grounded": True, "evidence_spans": [{"text": text}]}],
        },
        text_content=text,
        exports=[Export(id="exp_web", type="WEB_EVIDENCE", payload={})],
        actual_scope=Scope(source="WEB"),
        need_id=need_id,
    )


def _multi_measure_goal():
    return Goal(
        statement="two measures",
        raw_query="大谷翔平2025赛季本垒打和打点分别是多少？",
        requirements=[
            UserRequirement(id="r1", kind=RequirementKind.ENTITY, statement="entity", value={"mention": "大谷翔平", "role":"BATTER"}),
            UserRequirement(id="r2", kind=RequirementKind.SEASON, statement="season", value=2025, completion=RequirementCompletion.SCOPE),
            UserRequirement(id="r3", kind=RequirementKind.MEASURE, statement="HR", value={"key": "home_runs", "label": "Home runs", "derivable": True}, completion=RequirementCompletion.VALUE),
            UserRequirement(id="r4", kind=RequirementKind.MEASURE, statement="RBI", value={"key": "rbi", "label": "RBI", "derivable": False}, completion=RequirementCompletion.VALUE),
        ],
    )


def test_independent_unsupported_requirement_does_not_erase_supported_claim():
    needs = [
        PlannerNeed(id="n1", objective="resolve", capability="entity_resolution", requires=["r1"]),
        PlannerNeed(id="n2", objective="db", capability="local_analytics", depends_on=["n1"], requires=["r2", "r3", "r4"]),
    ]
    projection = Judge().evaluate(_multi_measure_goal(), [_entity_artifact(), _db_artifact(keys=["home_runs"])], needs)
    assert projection.goal_status == GoalStatus.LIMITED
    # The home-run claim survives even though the RBI requirement is unsupported.
    assert any(claim.epistemic == EpistemicStatus.SUPPORTED for claim in projection.claims)
    assert any("artifact" in [ref.source for ref in claim.support_refs] for claim in projection.claims)


def test_explanation_without_evidence_is_limited_not_failed_when_background_allowed():
    goal = Goal(
        statement="explain",
        raw_query="解释一下为什么强力打者容易三振",
        requirements=[
            UserRequirement(id="r1", kind=RequirementKind.ANALYSIS, statement="explain", completion=RequirementCompletion.EXPLANATION, background_ok=True),
        ],
    )
    projection = Judge().evaluate(goal, [], [PlannerNeed(id="n1", objective="web", capability="web_research", requires=["r1"])])
    assert projection.goal_status == GoalStatus.LIMITED
    assert projection.background_ok is True
    assert not projection.claims


def test_high_risk_explanation_without_evidence_is_failed():
    goal = Goal(
        statement="injury rumor",
        raw_query="他现在是不是受伤了",
        requirements=[
            UserRequirement(id="r1", kind=RequirementKind.ANALYSIS, statement="injury status", completion=RequirementCompletion.EXPLANATION, background_ok=False),
        ],
    )
    projection = Judge().evaluate(goal, [], [PlannerNeed(id="n1", objective="web", capability="web_research", requires=["r1"])])
    assert projection.goal_status == GoalStatus.FAILED


def test_empty_population_does_not_confirm_scope():
    goal = Goal(
        statement="empty",
        raw_query="大谷翔平2026年季后赛表现如何",
        requirements=[
            UserRequirement(id="r1", kind=RequirementKind.ENTITY, statement="entity", value={"mention": "大谷翔平", "role":"BATTER"}),
            UserRequirement(id="r2", kind=RequirementKind.SEASON, statement="season", value=2026, completion=RequirementCompletion.SCOPE),
            UserRequirement(id="r3", kind=RequirementKind.GAME_POPULATION, statement="pop", value="POSTSEASON", completion=RequirementCompletion.SCOPE),
            UserRequirement(id="r4", kind=RequirementKind.ANALYSIS, statement="performance"),
        ],
    )
    empty_db = Artifact(
        id="art_empty",
        kind="DATABASE_RESULT",
        structured_data={"rows": [{"home_runs": 0}], "row_count": 1, "empty_population": True},
        actual_scope=Scope(entities=["660271"], season=2026, game_population="POSTSEASON", source="POSTGRES"),
        need_id="n2",
    )
    needs = [PlannerNeed(id="n1", objective="resolve", capability="entity_resolution", requires=["r1"]),
             PlannerNeed(id="n2", objective="db", capability="local_analytics", depends_on=["n1"], requires=["r2", "r3", "r4"])]
    projection = Judge().evaluate(goal, [_entity_artifact(), empty_db], needs)
    assert projection.goal_status != GoalStatus.COMPLETE


def test_irrelevant_web_finding_is_not_a_claim():
    goal = Goal(
        statement="rbi",
        raw_query="大谷翔平2025赛季打点是多少",
        requirements=[
            UserRequirement(id="r1", kind=RequirementKind.ENTITY, statement="entity", value={"mention": "大谷翔平", "role":"BATTER"}),
            UserRequirement(
                id="r2", kind=RequirementKind.MEASURE, statement="RBI",
                value={"key": "rbi", "label": "Runs batted in (RBI)", "derivable": True},
                completion=RequirementCompletion.VALUE,
            ),
        ],
    )
    # The page mentions Ohtani but never RBI: it is subject-relevant, not question-relevant.
    projection = Judge().evaluate(goal, [_entity_artifact(), _web_artifact(text="Shohei Ohtani won the 2025 World Series.")], [])
    assert not projection.claims


def test_definition_backed_by_knowledge_drops_incidental_web_claims():
    goal = Goal(
        statement="define OPS",
        raw_query="解释一下什么是OPS？",
        requirements=[
            UserRequirement(
                id="r1", kind=RequirementKind.ANALYSIS, statement="explain OPS",
                completion=RequirementCompletion.EXPLANATION, background_ok=True,
            ),
        ],
    )
    knowledge = Artifact(
        id="art_knowledge",
        kind="KNOWLEDGE_ITEM",
        structured_data={"items": [{"id": "GLOSSARY:OPS", "type": "DEFINITION", "title": "OPS", "summary": "OBP + SLG."}]},
        exports=[Export(id="exp_k", type="KNOWLEDGE_ITEM", payload={})],
        actual_scope=Scope(source="SHARED_KNOWLEDGE"),
        need_id="n1",
    )
    web = _web_artifact(text="The team posted a strong OPS last season.", need_id="n2")
    projection = Judge().evaluate(goal, [knowledge, web], [])
    assert projection.claims
    assert not any("http" in claim.text for claim in projection.claims)


def test_no_sources_produces_no_fabricated_citations():
    goal = Goal(
        statement="stats",
        raw_query="大谷翔平2025赛季打击表现如何",
        requirements=[
            UserRequirement(id="r1", kind=RequirementKind.ENTITY, statement="entity", value={"mention": "大谷翔平", "role":"BATTER"}),
            UserRequirement(id="r2", kind=RequirementKind.ANALYSIS, statement="overall hitting"),
        ],
    )
    projection = Judge().evaluate(goal, [], [])
    assert projection.claims == []
    for claim in projection.claims:
        assert claim.support_refs, "a SUPPORTED claim must carry evidence references"


# --------------------------------------------------------------------- web taxonomy


class _FakeBackend:
    def __init__(self, hits=None, error=""):
        self.name = "fake"
        self._hits = hits or []
        self._configured_error = error
        self.last_error = ""

    def search(self, query, *, limit=5, language="en"):
        self.last_error = self._configured_error
        return list(self._hits)


class _Hit:
    def __init__(self, url="https://example.com/a", title="Ohtani 2025 home runs", engine="fake"):
        self.url = url
        self.title = title
        self.snippet = ""
        self.engine = engine


def _web_tool(backends):
    from app.config import WebConfig

    tool = WebResearchTool(WebConfig(broad_search=False), llm=None)
    tool.backends = backends
    return tool


def _request(subjects=("Shohei Ohtani",)):
    return ToolRequest(
        need_id="n9",
        capability="web_research",
        objective="2025 home runs",
        inputs={"entity_names": list(subjects), "objective": "2025 home runs", "user_wording": ""},
    )


def test_all_search_backends_failing_is_source_unavailable():
    backend = _FakeBackend(hits=[], error="ConnectError: refused")
    tool = _web_tool([backend])
    result = tool.execute(_request())
    assert result.outcome.status == "FAILED"
    assert result.outcome.code in {"SEARCH_BACKEND_UNAVAILABLE", "SOURCE_UNAVAILABLE"}
    assert result.outcome.retryable is True


def test_no_results_without_transport_error_is_no_results():
    tool = _web_tool([_FakeBackend(hits=[], error="")])
    result = tool.execute(_request())
    assert result.outcome.status == "NO_DATA"
    assert result.outcome.code == "NO_RESULTS"


def test_hits_without_retrievable_page_is_source_unavailable(monkeypatch):
    tool = _web_tool([_FakeBackend(hits=[_Hit()], error="")])

    def fake_fetch(url, config):
        return FetchedPage(url=url, final_url=url, title="", text="", retrieved_at="now", ok=False, error="boom", error_code="FETCH_FAILED")

    monkeypatch.setattr("app.capabilities.web_research.fetch_page", fake_fetch)
    result = tool.execute(_request())
    assert result.outcome.status == "FAILED"
    assert result.outcome.code == "SOURCE_UNAVAILABLE"


def test_irrelevant_page_is_irrelevant_results(monkeypatch):
    tool = _web_tool([_FakeBackend(hits=[_Hit()], error="")])

    def fake_fetch(url, config):
        return FetchedPage(url=url, final_url=url, title="Other", text="Completely unrelated content " * 20, retrieved_at="now", ok=True)

    monkeypatch.setattr("app.capabilities.web_research.fetch_page", fake_fetch)
    result = tool.execute(_request(subjects=("Shohei Ohtani",)))
    assert result.outcome.status == "NO_DATA"
    assert result.outcome.code == "IRRELEVANT_RESULTS"
    assert result.artifact is not None
    assert all(not f.get("relevant") for f in result.artifact.structured_data["findings"])


def test_relevant_page_is_grounded_success(monkeypatch):
    tool = _web_tool([_FakeBackend(hits=[_Hit()], error="")])

    def fake_fetch(url, config):
        return FetchedPage(
            url=url,
            final_url=url,
            title="Shohei Ohtani 2025",
            text="Shohei Ohtani hit fifty five home runs during the 2025 season. " * 5,
            retrieved_at="now",
            ok=True,
        )

    monkeypatch.setattr("app.capabilities.web_research.fetch_page", fake_fetch)
    result = tool.execute(_request(subjects=("Shohei Ohtani",)))
    assert result.outcome.status == "SUCCESS"
    assert result.artifact.status == "ACCEPTED"
    assert any(f.get("grounded") and f.get("relevant") for f in result.artifact.structured_data["findings"])
