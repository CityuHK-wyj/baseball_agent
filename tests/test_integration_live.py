"""Live integration tests: real LLM + real PostgreSQL/Parquet + real web.

Run with: RUN_INTEGRATION=1 python3 -m pytest tests/test_integration_live.py -v
"""

from __future__ import annotations
from tools.analytical_requirement_fixture import analytical_request

import dataclasses
import json
import os
from pathlib import Path

import pytest

from app.config import get_config
from app.contracts.models import CoverageState, GoalStatus
from app.runtime.engine import Engine

pytestmark = pytest.mark.integration


def _require() -> None:
    if not get_config().llm.configured:
        pytest.skip("LLM is not configured")
    if os.environ.get("POSTGRES_PASSWORD") is None:
        pytest.skip("POSTGRES_PASSWORD is not set")
    # Verify the analytical sources actually answer before claiming a live pass. A source
    # that is configured but unavailable is an environment skip, never a business failure.
    from app.capabilities.analytics.catalog import SOURCE_PARQUET, SOURCE_POSTGRES
    from app.capabilities.analytics.executor import AnalyticalExecutor

    config = get_config()
    executor = AnalyticalExecutor(config.postgres, config.parquet.archive_path)
    postgres = executor.run("SELECT 1 AS ok", SOURCE_POSTGRES)
    parquet = executor.run("SELECT 1 AS ok", SOURCE_PARQUET)
    if postgres.error and parquet.error:
        pytest.skip(
            f"no analytical source available (postgres={postgres.error_code}:{postgres.error}; "
            f"parquet={parquet.error_code}:{parquet.error})"
        )


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    _require()
    # Each run owns its conversation history. Fixed test session IDs must never
    # resume a stale clarification from a previous invocation or developer session.
    config = dataclasses.replace(get_config(), runtime_dir=tmp_path_factory.mktemp('live-runtime'))
    return Engine(config)


def _db_artifacts(result):
    return [a for a in result.artifacts if a.kind == "DATABASE_RESULT"]


def test_chinese_single_season_batting(engine):
    result = engine.ask("it_season", "大谷翔平2025赛季打击表现如何？", trace_level="off")
    assert result.projection.goal_status == GoalStatus.COMPLETE
    db = _db_artifacts(result)
    assert db, "expected a database artifact"
    artifact = db[0]
    assert artifact.structured_data["row_count"] == 1
    assert "batter_id" in artifact.structured_data["applied_fields"]
    row = artifact.structured_data["rows"][0]
    assert row["pa"] > 400, row
    assert row["home_runs"] >= 40, row
    assert 0.2 < row["avg"] < 0.4
    assert result.answer.strip()


def test_chinese_postseason_pitching(engine):
    result = engine.ask("it_post", "山本由伸2025年季后赛投球表现如何？", trace_level="off")
    db = _db_artifacts(result)
    assert db, result.answer
    scope = db[0].actual_scope
    assert scope.game_population == "POSTSEASON"
    assert scope.source == "POSTGRES"
    assert "pitcher_id" in db[0].structured_data["applied_fields"]


def test_parquet_historical_season(engine):
    result = engine.ask("it_parquet", "大谷翔平2023赛季打击表现如何？", trace_level="off")
    db = _db_artifacts(result)
    assert db, result.answer
    assert db[0].actual_scope.source == "PARQUET"
    assert db[0].actual_scope.season == 2023
    row = db[0].structured_data["rows"][0]
    assert row["home_runs"] >= 40


def test_follow_up_reuses_conversation_entity(engine):
    engine.ask("it_follow", "大谷翔平2025赛季打击表现如何？", trace_level="off")
    result = engine.ask("it_follow", "那2024赛季呢？", trace_level="off")
    assert result.semantic.turn_type == "FOLLOW_UP"
    db = _db_artifacts(result)
    assert db, (result.answer, [(o.code, o.message) for o in result.outcomes])
    assert db[0].actual_scope.season == 2024
    assert "batter_id" in db[0].structured_data["applied_fields"]


def test_web_research_grounded(engine):
    result = engine.ask("it_web", "Aaron Judge 最近有伤病报道吗？", trace_level="off")
    web = [a for a in result.artifacts if a.kind == "WEB_EVIDENCE"]
    if not web:
        pytest.skip("network search unavailable in this environment")
    findings = web[0].structured_data["findings"]
    assert findings
    assert all(f["url"].startswith("http") for f in findings)
    assert any(f["evidence_spans"] for f in findings)
    assert result.projection.goal_status != GoalStatus.FAILED


def test_postgres_unavailable_is_reported_not_faked():
    _require()
    base = get_config()
    broken_pg = dataclasses.replace(base.postgres, port=59999)
    config = dataclasses.replace(base, postgres=broken_pg)
    engine = Engine(config)
    assert engine.source_status["postgres"] is False
    result = engine.ask("it_down", "大谷翔平2025赛季打击表现如何？", trace_level="off")
    # The runtime must not invent data: either the archive answers or the goal is limited/failed.
    db = _db_artifacts(result)
    if db:
        assert db[0].actual_scope.source == "PARQUET"
    else:
        assert result.projection.goal_status in {GoalStatus.LIMITED, GoalStatus.FAILED}


def test_adhoc_split_is_complete(engine):
    result = engine.ask("it_split", "2025年大谷翔平面对左投和右投的打击率分别是多少？", trace_level="off")
    db = _db_artifacts(result)
    assert db, result.answer
    assert result.projection.goal_status == GoalStatus.COMPLETE
    fields = db[0].structured_data["applied_fields"]
    # The split must use the real handedness column, whether via grouping or conditional
    # aggregation; both are valid Safe IR representations.
    assert "p_throws" in fields
    rows = db[0].structured_data["rows"]
    assert rows
    numeric = [v for row in rows for v in row.values() if isinstance(v, float)]
    assert any(0.1 < v < 0.5 for v in numeric), rows


def test_adhoc_ranking_with_qualification(engine):
    result = engine.ask("it_rank", "2025年常规赛至少有300个打席的打者中，本垒打最多的是谁？", trace_level="off")
    db = _db_artifacts(result)
    assert db, result.answer
    assert result.projection.goal_status == GoalStatus.COMPLETE
    rows = db[0].structured_data["rows"]
    assert rows and rows[0]["home_runs"] >= 50
    assert "batter_name" in rows[0]
    assert rows[0]["pa"] >= 300


def test_cross_source_python_compute(engine):
    result = engine.ask("it_xsrc", "大谷翔平2023赛季和2024赛季各打出了多少支本垒打？相差多少？", trace_level="off")
    derived = [a for a in result.artifacts if a.kind == "DERIVED_MEASURE"]
    sources = {a.actual_scope.source for a in _db_artifacts(result) if a.actual_scope}
    assert derived, result.answer
    assert result.projection.goal_status == GoalStatus.COMPLETE
    assert "PARQUET" in sources and "POSTGRES" in sources
    assert derived[0].structured_data["rows"], derived[0].structured_data


def test_english_question_substitution(engine):
    result = engine.ask("it_en", "How did Shohei Ohtani hit in the 2024 regular season?", trace_level="off")
    db = _db_artifacts(result)
    assert db, result.answer
    assert result.projection.goal_status == GoalStatus.COMPLETE
    assert db[0].actual_scope.season == 2024
    row = db[0].structured_data["rows"][0]
    assert row["home_runs"] >= 40


def test_other_player_substitution(engine):
    result = engine.ask("it_judge", "Aaron Judge 2024赛季打击表现如何？", trace_level="off")
    db = _db_artifacts(result)
    assert db, result.answer
    assert result.projection.goal_status == GoalStatus.COMPLETE
    fields = db[0].structured_data["applied_fields"]
    assert "batter_id" in fields
    row = db[0].structured_data["rows"][0]
    assert row["home_runs"] >= 40


# --------------------------------------------------- measure / entity correctness


def _database_claims(result):
    return [
        ref
        for claim in result.projection.claims
        for ref in claim.support_refs
        if ref.source == "artifact"
    ]


def test_unsupported_measure_is_limited_not_faked(engine):
    result = engine.ask("it_measure", "大谷翔平2025赛季打点(RBI)是多少？", trace_level="off")
    assert result.projection.goal_status != GoalStatus.COMPLETE
    assert not _db_artifacts(result), "a non-derivable measure must not produce a substitute result"
    assert not _database_claims(result)
    codes = {outcome.code for outcome in result.outcomes}
    assert "UNSUPPORTED_MEASURE" in codes or "MEASURE_MISMATCH" in codes


def test_ambiguous_entity_does_not_run_a_league_query(engine):
    result = engine.ask("it_ambiguous", "Luis Castillo 2025赛季投球表现如何？", trace_level="off")
    assert result.projection.goal_status == GoalStatus.WAITING_FOR_USER
    assert not _db_artifacts(result), "ambiguous entities must not fall back to an unfiltered query"
    assert not _database_claims(result)


def test_career_scope_fails_closed_without_crashing(engine):
    # Must be a typed limitation/block, never an uncaught TypeError from the catalog.
    result = engine.ask("it_career", "大谷翔平职业生涯一共打了多少支本垒打？", trace_level="off")
    assert result.projection.goal_status in {GoalStatus.LIMITED, GoalStatus.FAILED}


def test_empty_population_is_not_reported_as_complete(engine):
    result = engine.ask("it_empty", "大谷翔平2026年季后赛表现如何？", trace_level="off")
    db = _db_artifacts(result)
    if db:
        assert db[0].structured_data.get("empty_population") is True
    assert result.projection.goal_status != GoalStatus.COMPLETE


# -------------------------------------- Phase 5A real cross-tool execution (no network)


_RANK_IR = {
    "relation": "pitch",
    "population": "REGULAR_SEASON",
    "season": 2025,
    "group_by": ["batter_id"],
    "aggregates": [
        {
            "name": "home_runs",
            "function": "COUNT_IF",
            "condition": {"op": "COMPARE", "field": "events", "comparator": "=", "value": "home_run"},
        }
    ],
    "sort": [{"name": "home_runs", "direction": "DESC"}],
    "limit": 5,
    "intent_summary": "most 2025 regular-season home runs",
}


class _RecordingWeb:
    """Test double for the network provider; records the request the runtime produced."""

    def __init__(self):
        from app.capabilities.base import CapabilitySpec

        self.spec = CapabilitySpec(
            name="web_research", description="", accepts=["SEARCH_QUERY"], produces=["WEB_EVIDENCE"]
        )
        self.requests = []

    def execute(self, request, ctx):
        from app.capabilities.base import CapabilityResult
        from app.contracts.models import Artifact, Export, Scope, ToolOutcome

        self.requests.append(request)
        artifact = Artifact(
            id=f"art_web_{len(self.requests)}",
            kind="WEB_EVIDENCE",
            structured_data={"findings": []},
            exports=[Export(id=f"exp_{request.id}_web", type="WEB_EVIDENCE", payload={})],
            actual_scope=Scope(source="WEB"),
            need_id=request.need_id,
        )
        return CapabilityResult(
            artifact,
            ToolOutcome(status="SUCCESS", code="OK", need_id=request.need_id, tool_request_id=request.id),
        )


def _hand_built_goal(*, comparison=False):
    from app.contracts.models import RequirementCompletion, RequirementKind, UserRequirement
    from tools.requirement_ancestry_fixture import Goal
    shape = dict(analytical_role="BATTER",group_by=["batter_id"],ordering=[dict(metric="home_runs",direction="DESC")])
    if not comparison: shape["limit"] = 5
    return Goal(statement="rank then investigate", raw_query="Rank all batters by home runs" if comparison else "List top 5 batters by home runs",
                requirements=[UserRequirement(id="r1",kind=RequirementKind.ANALYSIS,statement="Rank batters by home runs",completion=RequirementCompletion.GROUNDED),
                              UserRequirement(kind=RequirementKind.SEASON,statement="2025",value=2025),
                              UserRequirement(kind=RequirementKind.GAME_POPULATION,statement="regular season",value="REGULAR_SEASON"),
                              UserRequirement(kind=RequirementKind.MEASURE,statement="home runs",value={"key":"home_runs","derivable":True}),
                              UserRequirement(kind=RequirementKind.CONSTRAINT,statement="bound ranking shape",value=shape)])


def _prepare_engine(engine):
    """Monkeypatch the analytics IR builder and replace the web provider with a recorder."""
    from app.capabilities.base import CapabilitySpec
    from app.contracts.models import PlannerNeed

    analytics = engine.registry.get("local_analytics")
    def bound_rank(intent, slice_data, source, feedback):
        raw = dict(_RANK_IR)
        raw["limit"] = intent["bound_obligations"]["limit"]
        raw["analytical_role"] = intent["bound_obligations"]["analytical_role"]
        return raw
    analytics._build_ir = bound_rank
    recorder = _RecordingWeb()
    engine.registry.register(recorder)
    return recorder


def _execute_hand_plan(engine, needs, goal):
    from app.contracts.models import ConversationState, NeedStatus
    from app.observe.trace import Trace
    from app.planner.planner import derive_criticality
    from app.runtime.context import ExecutionContext

    for need in needs:
        need.status = NeedStatus.PENDING
    derive_criticality(needs, goal)
    ctx = ExecutionContext(
        goal=goal,
        conversation=ConversationState(session_id="it_cross"),
        config=engine.config,
        llm=engine.llm,
        trace=Trace("off"),
        clock=engine.clock,
    )
    artifacts: list = []
    outcomes: list = []
    engine._execute_pending(ctx, _minimal_semantic(), needs, artifacts, outcomes, Trace("off"))
    return artifacts, outcomes


def _minimal_semantic():
    from app.contracts.models import SemanticInterpretation

    return SemanticInterpretation(raw_query="rank", language="en")


def test_f2_rank_to_web_cross_tool_execution(engine):
    """Real DB rank -> runtime-inserted projection -> strict Binder -> web request.

    The Planner deliberately omits ``artifact_projection``; the runtime must discover and
    insert the typed converter instead of the test hand-wiring the correct graph.
    """
    from app.contracts.models import PlannerNeed

    recorder = _prepare_engine(engine)
    goal = _hand_built_goal()
    needs = [
        PlannerNeed(
            id="n1",
            objective="rank 2025 home runs",
            capability="local_analytics",
            constraints={"season": 2025, "game_population": "REGULAR_SEASON", "measure": "home_runs"},
            requires=["r1"],
        ),
        PlannerNeed(
            id="n3",
            objective="find an interview explaining the performance",
            capability="web_research",
            depends_on=["n1"],
            input_requirements=["SEARCH_QUERY"],
            requires=["r1"],
        ),
    ]
    artifacts, outcomes = _execute_hand_plan(engine, needs, goal)

    rank = next(a for a in artifacts if a.need_id == "n1")
    assert any(need.capability == "artifact_projection" for need in needs), "runtime must insert the converter"
    projection = next(a for a in artifacts if a.kind == "ENTITY_PROJECTION")
    search = projection.export("SEARCH_QUERY")
    assert search is not None
    subject = search.payload["subjects"][0]
    assert subject
    assert projection.lineage and projection.lineage[0] == rank.id
    assert search.payload["selected_row_identity"]["canonical_id"] is not None
    # The web request the runtime built used the projected, canonical subject.
    assert recorder.requests, "web_research was never invoked through the strict Binder"
    request = recorder.requests[-1]
    assert subject in request.inputs.get("search_query", "") or subject in request.inputs.get("entity_names", [])
    assert any(o.need_id == "n3" and o.status == "SUCCESS" for o in outcomes)


def test_f5_comparison_input_enumerates_complete_population(engine):
    """A rank feeding a python_compute comparison must not be a pre-truncated top-N."""
    from app.contracts.models import PlannerNeed

    _prepare_engine(engine)
    goal = _hand_built_goal(comparison=True)
    program = "output = [{\"max_hr\": max(r[\"home_runs\"] for k, v in inputs.items() for r in v)}]"
    needs = [
        PlannerNeed(
            id="n1",
            objective="rank 2025 home runs",
            capability="local_analytics",
            constraints={"season": 2025, "game_population": "REGULAR_SEASON", "measure": "home_runs"},
            requires=["r1"],
        ),
        PlannerNeed(
            id="n2",
            objective="find the largest home run total globally",
            capability="python_compute",
            depends_on=["n1"],
            input_requirements=["RANKED_ENTITY_SET"],
            constraints={"global_comparison": True, "program": program},
            requires=["r1"],
        ),
    ]
    artifacts, outcomes = _execute_hand_plan(engine, needs, goal)

    rank = next(a for a in artifacts if a.need_id == "n1")
    # The bound Goal requires the complete population; 5000 is an internal ceiling.
    assert rank.structured_data["for_comparison"] is True
    assert rank.enumeration is not None
    assert rank.enumeration.completeness == "COMPLETE"
    assert rank.enumeration.limit == 5000
    assert rank.structured_data["row_count"] > 5
    assert any(o.need_id == "n2" and o.status == "SUCCESS" for o in outcomes)


def test_f5_row_budget_measures_population_count(engine):
    """A grouped scan that fills the 5,000 budget must measure — not invent — completeness."""
    from app.contracts.models import ConversationState, ToolRequest
    from tools.requirement_ancestry_fixture import Goal
    from app.runtime.context import ExecutionContext

    analytics = engine.registry.get("local_analytics")
    analytics._build_ir = lambda intent, slice_data, source, feedback: {
        "relation": "pitch",
        "population": "REGULAR_SEASON",
        "season": 2025,
        "group_by": ["batter_id", "game_pk"],
        "aggregates": [{"name": "pitches", "function": "COUNT", "field": "*"}],
        "sort": [{"name": "pitches", "direction": "DESC"}],
        "limit": None,
        "intent_summary": "batter-game pitch counts",
    }
    request = analytical_request(
        id="tr_budget",
        need_id="n1",
        capability="local_analytics",
        objective="batter game pitch counts",
        inputs={"intent": {"objective": "batter game pitch counts", "season": 2025, "population": "REGULAR_SEASON"}, "for_comparison": True,"entity_role":"BATTER","requested_measures":[{"key":"pitches"}],"constraints":{"group_by":["batter_id","game_pk"],"ordering":[{"metric":"pitches","direction":"DESC"}]}},
    )
    ctx = ExecutionContext(goal=Goal(statement="g", raw_query="q"), conversation=ConversationState(session_id="s"))
    result = analytics.execute(request, ctx)

    assert result.outcome.status == "SUCCESS", result.outcome.message
    enumeration = result.artifact.enumeration
    assert enumeration is not None
    assert enumeration.limit == 5000
    assert enumeration.returned_rows == 5000
    # The exact COUNT pushdown proves the population is larger; completeness is not faked.
    assert enumeration.population_count is not None and enumeration.population_count > 5000
    assert enumeration.completeness == "TRUNCATED"


def test_f5_legal_cross_season_cross_source_delta(engine):
    """A per-entity delta across seasons/sources is legal and retains both source scopes."""
    from app.contracts.models import ConversationState, Goal, InputBinding, ToolRequest
    from app.runtime.context import ExecutionContext

    analytics = engine.registry.get("local_analytics")
    compute = engine.registry.get("python_compute")

    def _season_artifact(season: int):
        request = analytical_request(
            id=f"tr_{season}",
            need_id=f"n_{season}",
            capability="local_analytics",
            objective="batting performance",
            inputs={
                "intent": {"objective": "batting performance", "season": season, "population": "REGULAR_SEASON"},
                "entity_sets": [{"role": "BATTER", "ids": [660271], "names": ["Shohei Ohtani"]}],
                "require_entities": True,
            },
        )
        ctx = ExecutionContext(goal=Goal(statement="g", raw_query="q"), conversation=ConversationState(session_id="s"))
        return analytics.execute(request, ctx).artifact

    base_2023 = _season_artifact(2023)
    base_2024 = _season_artifact(2024)
    assert base_2023 is not None and base_2024 is not None
    assert base_2023.actual_scope.source == "PARQUET"
    assert base_2024.actual_scope.source == "POSTGRES"

    bindings = [
        InputBinding(need_id="n_cmp", export_id=base_2023.exports[0].id, export_type=base_2023.exports[0].type, artifact_id=base_2023.id, source_need_id="n_2023"),
        InputBinding(need_id="n_cmp", export_id=base_2024.exports[0].id, export_type=base_2024.exports[0].type, artifact_id=base_2024.id, source_need_id="n_2024"),
    ]
    program = (
        'a = inputs["n_2023"][0]["home_runs"]\n'
        'b = inputs["n_2024"][0]["home_runs"]\n'
        'output = [{"delta": a - b}]'
    )
    request = analytical_request(
        id="tr_cmp",
        need_id="n_cmp",
        capability="python_compute",
        objective="difference in home runs between seasons",
        inputs={
            "operation": {"kind": "DELTA", "identity": "PLAYER", "partition_by": "SEASON"},
            "global_comparison": True,
            "program": program,
        },
        bindings=bindings,
    )
    ctx = ExecutionContext(
        goal=Goal(statement="g", raw_query="q"),
        conversation=ConversationState(session_id="s"),
        artifacts=[base_2023, base_2024],
    )
    result = compute.execute(request, ctx)
    assert result.outcome.status == "SUCCESS", result.outcome.message
    assert result.artifact.enumeration is not None and result.artifact.enumeration.completeness == "COMPLETE"
    scopes = result.artifact.structured_data["source_scopes"]
    assert {scope["source"] for scope in scopes} == {"PARQUET", "POSTGRES"}
    assert {scope["season"] for scope in scopes} == {2023, 2024}


class _FixtureHit:
    def __init__(self, query):
        self.url = "https://fixture.example/interview"
        self.title = f"Interview: {query}"
        self.snippet = "fixture snippet"
        self.engine = "fixture"


class _FixtureBackend:
    name = "fixture"

    def __init__(self):
        self.last_error = ""
        self.queries: list[str] = []

    def search(self, query, *, limit=5, language="en"):
        self.queries.append(query)
        return [_FixtureHit(query)]


def test_f2_real_planner_to_fixture_web(engine, monkeypatch):
    """User objective -> real Planner -> runtime projection -> replayable Web fixture.

    The Planner may or may not name ``artifact_projection`` itself; either way the runtime
    must reach the converter before external research. The page body is a deterministic
    fixture, so URL/evidence-span checks are reproducible.
    """
    from app.capabilities.web_research import WebResearchTool
    from app.capabilities.web_search import FetchedPage

    backend = _FixtureBackend()
    fixture_web = WebResearchTool(get_config().web, llm=None)
    fixture_web.backends = [backend]

    def fake_fetch(url, config):
        subject_text = " ".join(backend.queries) or "player"
        return FetchedPage(
            url=url,
            final_url=url,
            title=f"Interview about {subject_text}",
            text=(
                f"{subject_text}. The interview explains the mechanical adjustment behind the "
                "increased home run production and the player's approach at the plate. "
            ) * 8,
            retrieved_at="2026-09-21T00:00:00Z",
            ok=True,
        )

    monkeypatch.setattr("app.capabilities.web_research.fetch_page", fake_fetch)
    original_web = engine.registry.get("web_research")
    engine.registry.register(fixture_web)
    try:
        result = engine.ask(
            "it_real_planner",
            "2025年常规赛本垒打最多的打者是谁？请找一篇解释他表现原因的采访报道。",
            trace_level="off",
        )
    finally:
        engine.registry.register(original_web)

    ranked = [a for a in result.artifacts if a.kind == "DATABASE_RESULT" and a.export("RANKED_ENTITY_SET")]
    if not ranked:
        pytest.skip("planner/model did not produce a ranked analytics need in this environment")
    web = [a for a in result.artifacts if a.kind == "WEB_EVIDENCE"]
    if not web:
        pytest.skip("planner/model did not produce a web research need in this environment")

    projection = [a for a in result.artifacts if a.kind == "ENTITY_PROJECTION"]
    assert projection, "the runtime must insert the projection before external research"
    search = projection[0].export("SEARCH_QUERY")
    assert search is not None and search.payload["subjects"]
    subject = search.payload["subjects"][0]

    findings = web[0].structured_data["findings"]
    assert findings, "the fixture page must be retrieved"
    assert findings[0]["url"] == "https://fixture.example/interview"
    assert findings[0]["evidence_spans"], "the fixture page must yield an evidence span"
    # The fixture echoes the projected subject; at least one query carried it.
    assert any(subject.split()[-1] in query or subject in query for query in backend.queries), backend.queries


# ------------------------------------------------ Phase 5B real evidence / completion


def _assert_web_evidence_chain(artifact):
    """Validate provenance and, when present, a complete supported-evidence chain."""
    data = artifact.structured_data
    assert "documents" in data and "evidence_findings" in data
    docs = {doc["id"]: doc for doc in data["documents"]}
    spans = {span["id"]: span for span in data.get("evidence_spans", [])}
    for document in data["documents"]:
        assert document["url"].startswith("http")
        assert document["retrieval_status"]
        if document["retrieval_status"] == "OK":
            assert document["content_hash"]
            assert document["retrieved_at"]
    supported = []
    for finding in data["evidence_findings"]:
        span = spans.get(finding["span_id"])
        assert span is not None, "every persisted finding must resolve to a persisted span"
        assert span["document_id"] == finding["document_id"]
        assert span["document_id"] in docs
        assert span["source_type"] == "DOCUMENT_BODY"
        assert span["retrieval_status"] == "OK"
        assert span["end"] - span["start"] == len(span["text"])
        body = docs[span["document_id"]].get("body_text") or docs[span["document_id"]].get("text_excerpt", "")
        assert body[span["start"]:span["end"]] == span["text"]
        if finding["status"] == "SUPPORTED":
            supported.append(finding)
            assert finding.get("admissible") is True
            assert finding.get("hard_conflict") is False
            assert span["text"].strip()
            assert span["text"] != (docs[span["document_id"]].get("title") or "")
    return supported


def test_web_evidence_documents_spans_and_proposition_soundness(tmp_path):
    """Real Web: provenance is preserved; support requires a resolved body span."""
    _require()
    engine = Engine(dataclasses.replace(get_config(), runtime_dir=tmp_path))  # fresh tools and history
    result = engine.ask("it_p5b_web", "解释一下1994年美国职棒大联盟罢工的原因和影响。", trace_level="off")
    web = [a for a in result.artifacts if a.kind == "WEB_EVIDENCE"]
    if not web:
        pytest.skip("network search unavailable in this environment")
    supported_total = 0
    for artifact in web:
        supported_total += len(_assert_web_evidence_chain(artifact))
    mandatory = [p for p in result.projection.propositions if p.mandatory]
    if result.projection.goal_status == GoalStatus.COMPLETE:
        assert mandatory and all(p.coverage == CoverageState.SATISFIED for p in mandatory)
    if mandatory and not all(p.coverage == CoverageState.SATISFIED for p in mandatory):
        assert result.projection.goal_status != GoalStatus.COMPLETE
    # Two valid outcomes: a real supported chain, or a typed honest insufficiency. A run
    # with SUPPORTED findings must expose the complete chain (asserted above).
    if supported_total:
        assert any(p.coverage == CoverageState.SATISFIED for p in result.projection.propositions), result.answer
    else:
        # A typed contradiction is a stronger honest outcome than insufficiency: the
        # system detected contradicting passages rather than asserting unsupported support.
        codes = {a.structured_data.get("retrieval_status") for a in web}
        assert codes & {"IRRELEVANT_RESULTS", "INSUFFICIENT_EVIDENCE", "PARTIAL_EVIDENCE",
                        "SOURCE_UNAVAILABLE", "NO_RESULTS", "OK", "CONTRADICTED_EVIDENCE"}
        assert result.projection.goal_status != GoalStatus.COMPLETE or not mandatory


def test_goal_diagnostics_are_written_with_latency(tmp_path):
    _require()
    engine = Engine(dataclasses.replace(get_config(), runtime_dir=tmp_path))
    result = engine.ask("it_p5b_diag", "大谷翔平2024赛季打击表现如何？", trace_level="off")
    path = engine.config.runtime_dir / "diagnostics" / f"it_p5b_diag__{result.goal.id}.json"
    assert path.exists(), "a full-fidelity diagnostic record must be written per Goal"
    payload = json.loads(path.read_text(encoding="utf-8"))
    stages = {record["stage"] for record in payload}
    assert {"goal", "proposition_coverage", "judge_decision", "latency"} <= stages
    latency = next(record for record in payload if record["stage"] == "latency")
    assert latency["data"]["summary"].get("end_to_end", 0) >= 0
    usage = [record["data"] for record in payload if record["stage"] == "llm_usage"]
    assert usage, "provider attempts must reach persisted diagnostics"
    assert all("usage_available" in u and "input_tokens" in u and "output_tokens" in u for u in usage)


def test_real_provider_relation_usage_with_fixture_document(tmp_path):
    """Real LLM, explicit fixture evidence (no claim of real Web retrieval)."""
    config = get_config()
    if not config.llm.configured:
        pytest.skip("LLM is not configured")
    from app.llm.client import LLMClient
    from app.observe.diagnostics import DiagnosticRecorder
    from app.verification.evidence import verify_span
    from app.contracts.models import AnswerProposition, EvidenceSpan, RetrievedDocument, PropositionKind, VerificationStatus
    client = LLMClient(config.llm)
    recorder = DiagnosticRecorder(tmp_path / "real-usage.json")
    client.usage_sink = lambda **data: recorder.record("llm_usage", **data)
    text = "In 2024 Player Alpha hit 25 home runs, while Player Beta hit 30 home runs."
    document = RetrievedDocument(id="doc_fixture", url="https://example.com/fixture", retrieved_at="2026-09-27", content_hash="fixture", body_text=text)
    span = EvidenceSpan(document_id=document.id, text=text, start=0, end=len(text))
    proposition = AnswerProposition(statement="Player Alpha hit 25 home runs", subject="Player Alpha", kind=PropositionKind.VALUE,
                                    required_information="home runs", time_scope={"season": 2024})
    finding = verify_span(proposition, span, document, llm=client)
    assert finding.status == VerificationStatus.SUPPORTED
    assert finding.verifier == "llm"
    assert finding.checks["established_fact"]["subject"] == "Player Alpha"
    assert recorder.write()
    calls = [record["data"] for record in json.loads(recorder.path.read_text())]
    for call in calls:
        if not call["usage_available"]:
            assert call["input_tokens"] is None and call["output_tokens"] is None and call["total_tokens"] is None
    available = [call for call in calls if call["usage_available"]]
    assert available, "at least one real response must provide measured usage"
    measured = available[-1]
    assert measured["provider"] and measured["model"]
    assert measured["usage_available"]
    assert measured["input_tokens"] > 0 and measured["output_tokens"] > 0
