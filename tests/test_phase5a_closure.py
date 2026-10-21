"""Phase 5A closure tests: operation-aware completeness, projection safety, end-to-end F1.

These exercise the specific gaps raised by the independent revalidation:
- F1 independent-turn context isolation and the full Engine semantic -> Planner ->
  ToolRequest -> entity_resolution path;
- F2 verified ordering, ID-domain proof, per-export lineage, and deterministic runtime
  converter discovery;
- F5 operation-aware cross-input compatibility (incomplete / incompatible / legal
  cross-season and cross-source) and measured completeness at the row budget.
"""

from __future__ import annotations
from tools.analytical_requirement_fixture import analytical_request

import dataclasses

import pytest

from app.capabilities.base import CapabilityResult, CapabilitySpec
from app.capabilities.analytics.executor import QueryResult
from app.capabilities.analytics.ir import AnalyticalQuery
from app.capabilities.analytics.tool import LocalAnalyticsTool, _enumeration_contract
from app.capabilities.artifact_projection import ArtifactProjectionTool
from app.capabilities.entity_resolution import EntityResolutionTool
from app.capabilities.python_compute import PythonComputeTool
from app.capabilities.registry import CapabilityRegistry
from app.config import get_config
from tools.requirement_ancestry_fixture import Goal
from app.contracts.models import (
    AnalysisRequest,
    AnswerProjection,
    Artifact,
    Constraint,
    ConversationState,
    EntityMention,
    EntityReference,
    EnumerationContract,
    Export,
    GlobalOperation,
    GoalStatus,
    IdentityLevel,
    InputBinding,
    NeedStatus,
    PlannerNeed,
    RequirementCompletion,
    RequirementKind,
    Scope,
    SemanticInterpretation,
    ToolOutcome,
    ToolRequest,
    UserRequirement,
)
from app.planner.planner import derive_criticality
from app.runtime.binder import RuntimeBinder
from app.runtime.context import ExecutionContext
from app.runtime.engine import Engine
from app.semantic.entities import build_entity_references, strip_context_contamination
from app.verification.enumeration import check_compatibility

# =============================================================== shared helpers


def _ctx(artifacts=None) -> ExecutionContext:
    return ExecutionContext(
        goal=Goal(statement="g", raw_query="q"),
        conversation=ConversationState(session_id="s"),
        artifacts=list(artifacts or []),
    )


def _complete_artifact(
    artifact_id: str,
    *,
    need_id: str = "n1",
    season: int = 2024,
    source: str = "POSTGRES",
    population: str = "REGULAR_SEASON",
    qualification: str = "PA >= 100",
    entity_kind: str | None = "PLAYER",
    namespace: str | None = "MLBAM",
    entities=("660271",),
    identity_columns=("batter_id",),
    rows=None,
) -> Artifact:
    rows = rows if rows is not None else [{"batter_id": 660271, "home_runs": 10}]
    return Artifact(
        id=artifact_id,
        kind="DATABASE_RESULT",
        structured_data={"rows": rows, "row_count": len(rows)},
        exports=[Export(id=f"exp_{artifact_id}", type="STATISTICAL_RESULT", payload={"rows": rows})],
        actual_scope=Scope(season=season, source=source, entities=list(entities), measures=["home_runs"]),
        enumeration=EnumerationContract(
            population=population,
            qualification=qualification,
            season=season,
            source=source,
            source_coverage=f"{source}:coverage",
            returned_rows=len(rows),
            completeness="COMPLETE",
            identity_columns=list(identity_columns),
            entity_kind=entity_kind,
            entity_namespace=namespace,
        ),
        need_id=need_id,
    )


# =============================================================== F5 operation-aware


def test_unknown_operation_across_sources_fails_closed():
    """The exact revalidation counterexample: two COMPLETE inputs, no declared operation."""
    left = _complete_artifact("a", season=2024, source="POSTGRES")
    right = _complete_artifact("b", season=2023, source="PARQUET")
    result = check_compatibility(GlobalOperation(kind="UNKNOWN"), [left, right])
    assert not result.ok
    assert result.code == "UNKNOWN_OPERATION"


def test_legal_per_entity_delta_across_seasons_is_allowed():
    left = _complete_artifact("a", season=2024, source="POSTGRES")
    right = _complete_artifact("b", season=2023, source="PARQUET", entity_kind="PLAYER")
    result = check_compatibility(
        GlobalOperation(kind="DELTA", identity="PLAYER", partition_by="SEASON"), [left, right]
    )
    assert result.ok, result.detail
    assert result.shared["partition_by"] == "SEASON"


def test_legal_cross_source_union_with_partition_proof_is_allowed():
    left = _complete_artifact("a", season=2025, source="POSTGRES")
    right = _complete_artifact("b", season=2023, source="PARQUET")
    result = check_compatibility(
        GlobalOperation(kind="RANK", identity="PLAYER", partition_by="SEASON"), [left, right]
    )
    assert result.ok, result.detail


def test_source_overlap_is_rejected():
    left = _complete_artifact("a", season=2024, source="POSTGRES")
    right = _complete_artifact("b", season=2024, source="POSTGRES")
    result = check_compatibility(GlobalOperation(kind="UNION", identity="PLAYER"), [left, right])
    assert not result.ok
    assert result.code == "SOURCE_OVERLAP"


def test_population_and_qualification_mismatch_are_rejected():
    base = _complete_artifact("a", season=2024)
    other_pop = _complete_artifact("b", season=2023, source="PARQUET", population="POSTSEASON")
    other_qual = _complete_artifact("c", season=2022, source="PARQUET", qualification="PA >= 50")
    op = GlobalOperation(kind="DELTA", identity="PLAYER", partition_by="SEASON")
    assert check_compatibility(op, [base, other_pop]).code == "POPULATION_MISMATCH"
    assert check_compatibility(op, [base, other_qual]).code == "QUALIFICATION_MISMATCH"


def test_unverified_identity_domain_is_rejected():
    base = _complete_artifact("a", season=2024)
    unknown = _complete_artifact("b", season=2023, source="PARQUET", entity_kind=None, namespace=None)
    result = check_compatibility(GlobalOperation(kind="DELTA", identity="PLAYER", partition_by="SEASON"), [base, unknown])
    assert not result.ok
    assert result.code == "IDENTITY_KIND_UNVERIFIED"


def test_incomplete_input_is_rejected_even_with_a_legal_operation():
    left = _complete_artifact("a", season=2024)
    right = _complete_artifact("b", season=2023, source="PARQUET")
    right.enumeration.completeness = "TRUNCATED"
    result = check_compatibility(
        GlobalOperation(kind="DELTA", identity="PLAYER", partition_by="SEASON"), [left, right]
    )
    assert not result.ok
    assert result.code == "INCOMPLETE_INPUT"


def test_join_allows_same_window_with_stable_identity():
    left = _complete_artifact("a", season=2024, rows=[{"batter_id": 1, "home_runs": 30}])
    right = _complete_artifact("b", season=2024, source="POSTGRES", rows=[{"batter_id": 1, "rbi": 90}])
    result = check_compatibility(GlobalOperation(kind="JOIN", identity="PLAYER"), [left, right])
    assert result.ok, result.detail


def test_python_compute_blocks_unknown_cross_source_merge():
    tool = PythonComputeTool(llm=None)
    left = _complete_artifact("art_a", need_id="n1", season=2024, source="POSTGRES")
    right = _complete_artifact("art_b", need_id="n3", season=2023, source="PARQUET")
    bindings = [
        InputBinding(need_id="n2", export_id="exp_art_a", export_type="STATISTICAL_RESULT", artifact_id="art_a", source_need_id="n1"),
        InputBinding(need_id="n2", export_id="exp_art_b", export_type="STATISTICAL_RESULT", artifact_id="art_b", source_need_id="n3"),
    ]
    request = analytical_request(
        id="tr",
        need_id="n2",
        capability="python_compute",
        objective="global maximum",
        inputs={"global_comparison": True, "program": "output = [{'n': 1}]"},
        bindings=bindings,
    )
    result = tool.execute(request, _ctx([left, right]))
    assert result.outcome.status == "BLOCKED"
    assert result.outcome.code == "UNKNOWN_OPERATION"


# =============================================================== F5 completeness budget


class _FakeResult:
    def __init__(self, row_count):
        self.row_count = row_count


class _FakeCompiled:
    def __init__(self, grouped=True):
        self.group_columns = ["batter_id"] if grouped else []
        self.output_columns = ["batter_id", "home_runs"] if grouped else ["home_runs"]


def _grouped_query() -> AnalyticalQuery:
    from app.capabilities.analytics.ir import Aggregate

    return AnalyticalQuery(group_by=["batter_id"], aggregates=[Aggregate(name="home_runs", function="COUNT", field="*")])


def test_row_budget_without_count_is_truncated():
    contract = _enumeration_contract(_grouped_query(), _FakeCompiled(), _FakeResult(5000), "POSTGRES", 5000)
    assert contract.completeness == "TRUNCATED"
    assert contract.population_count is None
    assert "bounded" in contract.note


def test_exact_count_marks_the_budget_complete_or_truncated():
    complete = _enumeration_contract(
        _grouped_query(), _FakeCompiled(), _FakeResult(5000), "POSTGRES", 5000, population_count=5000
    )
    assert complete.completeness == "COMPLETE"
    assert complete.population_count == 5000

    truncated = _enumeration_contract(
        _grouped_query(), _FakeCompiled(), _FakeResult(5000), "POSTGRES", 5000, population_count=7321
    )
    assert truncated.completeness == "TRUNCATED"


def test_source_failure_is_a_typed_failure_not_a_success():
    tool, request = _analytics_with_executor(_ErrorExecutor("boom", "SOURCE_UNAVAILABLE"))
    result = tool.execute(request, _ctx())
    assert result.outcome.status == "FAILED"
    assert result.outcome.code == "SOURCE_UNAVAILABLE"
    assert result.outcome.retryable is True
    assert result.artifact is None


def test_unfinished_query_fails_closed():
    tool, request = _analytics_with_executor(_ErrorExecutor("bad plan", "QUERY_FAILED"))
    result = tool.execute(request, _ctx())
    assert result.outcome.status == "FAILED"
    assert result.outcome.code == "QUERY_FAILED"
    assert result.artifact is None


def _analytics_with_executor(executor):
    tool = LocalAnalyticsTool(executor, llm=None)
    tool._build_ir = lambda intent, slice_data, source, feedback: {
        "relation": "pitch",
        "season": 2025,
        "population": "REGULAR_SEASON",
        "group_by": ["batter_id"],
        "aggregates": [{"name": "home_runs", "function": "COUNT_IF", "condition": {"op": "COMPARE", "field": "events", "comparator": "=", "value": "home_run"}}],
        "sort": [{"name": "home_runs", "direction": "DESC"}],
        "limit": 5,
        "intent_summary": "home runs",
    }
    request = analytical_request(
        id="tr_analytics",
        need_id="n1",
        capability="local_analytics",
        objective="home runs",
        inputs={"intent": {"objective": "home runs", "season": 2025, "population": "REGULAR_SEASON"}, "for_comparison": False,
                "entity_role":"BATTER", "constraints":{"group_by":["batter_id"], "ordering":[{"metric":"home_runs","direction":"DESC"}], "limit":5}},
    )
    return tool, request


class _ErrorExecutor:
    def __init__(self, message, code):
        self.message = message
        self.code = code

    def run(self, sql, source):
        return QueryResult([], [], 0, source, 0, "hash", self.message, self.code)


# =============================================================== F2 projection safety


def _rank_artifact(*, ordering_verified: bool = True, rows=None) -> Artifact:
    rows = rows or [
        {"batter_id": 660271, "batter_name": "Shohei Ohtani", "home_runs": 54},
        {"batter_id": 592450, "batter_name": "Aaron Judge", "home_runs": 53},
    ]
    columns = list(rows[0].keys())
    return Artifact(
        id="art_rank",
        kind="DATABASE_RESULT",
        structured_data={"rows": rows, "row_count": len(rows)},
        exports=[
            Export(
                id="exp_rank",
                type="RANKED_ENTITY_SET",
                payload={"rows": rows, "columns": columns, "group_columns": ["batter_id"]},
                contract={"grain": "GROUP", "verified_ordering": ordering_verified, "ordering": ["home_runs DESC"]},
            )
        ],
        actual_scope=Scope(season=2025, measures=["home_runs"], source="POSTGRES", entities=["660271", "592450"]),
        enumeration=EnumerationContract(
            population="REGULAR_SEASON",
            season=2025,
            source="POSTGRES",
            returned_rows=len(rows),
            completeness="COMPLETE",
            ordering=["home_runs DESC"],
            ordering_verified=ordering_verified,
            identity_columns=["batter_id"],
            entity_kind="PLAYER",
            entity_namespace="MLBAM",
        ),
        need_id="n1",
    )


def _projection_request(*, inputs=None, artifact=None):
    artifact = artifact or _rank_artifact()
    binding = InputBinding(need_id="n2", export_id="exp_rank", export_type="RANKED_ENTITY_SET", artifact_id=artifact.id, source_need_id="n1")
    return analytical_request(
        id="tr_proj",
        need_id="n2",
        capability="artifact_projection",
        objective="project subject",
        inputs=inputs if inputs is not None else {"selection": {"mode": "TOP"}},
        bindings=[binding],
    ), artifact


def test_default_top_requires_verified_ordering():
    artifact = _rank_artifact(ordering_verified=False)
    artifact.exports[0].contract.pop("verified_ordering", None)
    artifact.enumeration.ordering_verified = False
    request, artifact = _projection_request(artifact=artifact)
    result = ArtifactProjectionTool().execute(request, _ctx([artifact]))
    assert result.outcome.status == "BLOCKED"
    assert result.outcome.code == "UNVERIFIED_ORDERING"


def test_explicit_index_is_allowed_without_verified_ordering():
    artifact = _rank_artifact(ordering_verified=False)
    artifact.enumeration.ordering_verified = False
    export = artifact.exports[0]
    request = analytical_request(
        id="tr_proj",
        need_id="n2",
        capability="artifact_projection",
        objective="project row 1",
        inputs={"row_index": 1},
        bindings=[InputBinding(need_id="n2", export_id="exp_rank", export_type="RANKED_ENTITY_SET", artifact_id=artifact.id, source_need_id="n1")],
    )
    # _rank_artifact rebuilds its own export id; align the binding id.
    request.bindings[0].export_id = artifact.exports[0].id
    result = ArtifactProjectionTool().execute(request, _ctx([artifact]))
    assert result.outcome.status == "SUCCESS"
    assert result.artifact.structured_data["selected_row"]["batter_id"] == 592450


def test_generic_id_is_not_emitted_as_a_player_id_set():
    rows = [{"entity_id": 77, "name": "Some Subject", "value": 1}]
    artifact = _rank_artifact(rows=rows)
    artifact.exports[0].contract = {"grain": "GROUP", "verified_ordering": True, "ordering": ["value DESC"]}
    artifact.enumeration.ordering = ["value DESC"]
    artifact.enumeration.ordering_verified = True
    artifact.enumeration.entity_kind = None
    artifact.enumeration.entity_namespace = None
    # entity_id is not in the catalog-validated player allowlist.
    artifact.exports[0].payload["columns"] = ["entity_id", "name", "value"]
    request, artifact = _projection_request(artifact=artifact)
    result = ArtifactProjectionTool().execute(request, _ctx([artifact]))
    assert result.outcome.status == "SUCCESS"
    assert result.artifact.export("PLAYER_ID_SET") is None
    identity = result.artifact.structured_data["selection"]["selected_row_identity"]
    assert "canonical_id" not in identity
    assert identity.get("id_kind") == "UNKNOWN"
    assert "entity_id" not in (result.artifact.actual_scope.entities or [])


def test_every_export_carries_lineage():
    request, artifact = _projection_request()
    result = ArtifactProjectionTool().execute(request, _ctx([artifact]))
    projection = result.artifact
    for export in projection.exports:
        contract = export.contract
        assert contract.get("source_artifact_id") == "art_rank"
        assert contract.get("source_export_id") == artifact.exports[0].id
        assert contract.get("lineage")
        assert contract.get("selection_rule")
    players = projection.export("PLAYER_ID_SET")
    assert players is not None and players.contract.get("selected_row_identity")
    assert players.contract.get("original_scope")


# =============================================================== F2 runtime repair


class _NoLLM:
    configured = False

    def complete_json(self, *args, **kwargs):  # pragma: no cover
        raise RuntimeError("no llm")

    def complete_text(self, *args, **kwargs):  # pragma: no cover
        raise RuntimeError("no llm")


class _FakeRankTool:
    def __init__(self):
        self.spec = CapabilitySpec(
            name="local_analytics",
            description="",
            accepts=["PLAYER_ID_SET", "SCOPE"],
            produces=["RANKED_ENTITY_SET"],
        )

    def execute(self, request, ctx):
        rows = [
            {"batter_id": 660271, "batter_name": "Shohei Ohtani", "home_runs": 54},
            {"batter_id": 592450, "batter_name": "Aaron Judge", "home_runs": 53},
        ]
        artifact = Artifact(
            id="art_rank",
            kind="DATABASE_RESULT",
            structured_data={"rows": rows, "row_count": len(rows)},
            exports=[
                Export(
                    id="exp_rank",
                    type="RANKED_ENTITY_SET",
                    payload={"rows": rows, "columns": list(rows[0].keys()), "group_columns": ["batter_id"]},
                    contract={"grain": "GROUP", "verified_ordering": True, "ordering": ["home_runs DESC"]},
                )
            ],
            actual_scope=Scope(season=2025, measures=["home_runs"], source="POSTGRES", entities=["660271", "592450"]),
            enumeration=EnumerationContract(
                population="REGULAR_SEASON",
                season=2025,
                source="POSTGRES",
                returned_rows=2,
                completeness="COMPLETE",
                ordering=["home_runs DESC"],
                ordering_verified=True,
                identity_columns=["batter_id"],
                entity_kind="PLAYER",
                entity_namespace="MLBAM",
            ),
            need_id=request.need_id,
        )
        return CapabilityResult(artifact, ToolOutcome(status="SUCCESS", code="OK", need_id=request.need_id, tool_request_id=request.id))


class _RecordingWeb:
    def __init__(self):
        self.spec = CapabilitySpec(name="web_research", description="", accepts=["SEARCH_QUERY"], produces=["WEB_EVIDENCE"])
        self.requests = []

    def execute(self, request, ctx):
        self.requests.append(request)
        artifact = Artifact(
            id="art_web",
            kind="WEB_EVIDENCE",
            structured_data={"findings": []},
            exports=[Export(id=f"exp_{request.id}_web", type="WEB_EVIDENCE", payload={})],
            actual_scope=Scope(source="WEB"),
            need_id=request.need_id,
        )
        return CapabilityResult(artifact, ToolOutcome(status="SUCCESS", code="OK", need_id=request.need_id, tool_request_id=request.id))


def _engine_without_live_sources():
    base = get_config()
    broken_pg = dataclasses.replace(base.postgres, port=59999)
    no_llm = dataclasses.replace(base.llm, api_key="")
    config = dataclasses.replace(base, postgres=broken_pg, llm=no_llm, runtime_dir=base.runtime_dir)
    return Engine(config)


def test_runtime_inserts_projection_when_planner_omits_it():
    engine = _engine_without_live_sources()
    recorder = _RecordingWeb()
    engine.registry.register(_FakeRankTool())
    engine.registry.register(recorder)

    goal = Goal(
        statement="rank then investigate",
        raw_query="2025 home run leader interview",
        requirements=[
            UserRequirement(id="r1", kind=RequirementKind.ANALYSIS, statement="rank home runs", completion=RequirementCompletion.GROUNDED)
        ],
    )
    needs = [
        PlannerNeed(id="n1", objective="rank 2025 home runs", capability="local_analytics", requires=["r1"]),
        PlannerNeed(
            id="n3",
            objective="find an interview",
            capability="web_research",
            depends_on=["n1"],
            input_requirements=["SEARCH_QUERY"],
            requires=["r1"],
        ),
    ]
    for need in needs:
        need.status = NeedStatus.PENDING
    derive_criticality(needs, goal)
    ctx = ExecutionContext(goal=goal, conversation=ConversationState(session_id="s"), config=engine.config, llm=engine.llm)
    artifacts: list = []
    outcomes: list = []
    from app.observe.trace import Trace

    engine._execute_pending(ctx, SemanticInterpretation(raw_query="rank", language="en"), needs, artifacts, outcomes, Trace("off"))

    assert any(need.capability == "artifact_projection" for need in needs), "runtime must insert the converter"
    projection = next(a for a in artifacts if a.kind == "ENTITY_PROJECTION")
    search = projection.export("SEARCH_QUERY")
    assert search.payload["subjects"] == ["Shohei Ohtani"]
    assert recorder.requests, "web_research must run after the inserted conversion"
    request = recorder.requests[-1]
    assert "Shohei Ohtani" in request.inputs.get("search_query", "") or "Shohei Ohtani" in request.inputs.get("entity_names", [])
    assert any(o.need_id == "n3" and o.status == "SUCCESS" for o in outcomes)


# =============================================================== F1 end to end (Engine)


class _StubInterpreter:
    def __init__(self, semantic: SemanticInterpretation):
        self._semantic = semantic

    def interpret(self, *args, **kwargs) -> SemanticInterpretation:
        return self._semantic.model_copy(deep=True)


class _StubPlanner:
    def __init__(self, factory):
        self._factory = factory
        self.captured = None

    def plan(self, **kwargs):
        self.captured = kwargs
        return self._factory(kwargs), "stub", []


def _entity_requirement_planner(entity_requirement_id: str):
    def factory(_kwargs):
        return [
            PlannerNeed(
                id="n1",
                objective="resolve the carried entity",
                capability="entity_resolution",
                desired_output="PLAYER_ID_SET",
                criticality_hint="core",
                requires=[entity_requirement_id],
            )
        ]

    return factory


def _seed_prior_goal(engine, session: str, references: list[EntityReference]) -> Goal:
    requirements = [
        UserRequirement(
            id=f"prior_r{index}",
            kind=RequirementKind.ENTITY,
            statement="prior entity",
            value={"mention": reference.surface},
            entity_ref=reference,
        )
        for index, reference in enumerate(references, start=1)
    ]
    prior = Goal(statement="prior", raw_query="prior query", requirements=requirements)
    engine.conversation.commit(
        session,
        user_text="prior query",
        goal=prior,
        answer="ok",
        projection=AnswerProjection(goal_id=prior.id, goal_status=GoalStatus.COMPLETE),
    )
    return prior


def _canonical(surface="大谷翔平", player_id="660271", name="Shohei Ohtani") -> EntityReference:
    return EntityReference(
        surface=surface,
        canonical_id=player_id,
        canonical_name=name,
        namespace="MLBAM",
        status=IdentityLevel.CANONICAL,
        identity_verified=True,
        source="context",
    )


def test_engine_follow_up_carries_canonical_entity_to_resolver():
    engine = _engine_without_live_sources()
    session = "f1_follow"
    _seed_prior_goal(engine, session, [_canonical()])
    semantic = SemanticInterpretation(
        raw_query="他2024赛季打击表现如何？",
        entities=[EntityMention(mention="他", type_hint="player", carried=True)],
        constraints=[Constraint(kind="season", value=2024)],
        analysis_requests=[AnalysisRequest(text="表现", objective="performance")],
    )
    engine.interpreter = _StubInterpreter(semantic)
    planner = _StubPlanner(lambda kwargs: _entity_requirement_planner(
        [r.id for r in kwargs["goal"].requirements if r.kind == RequirementKind.ENTITY][0]
    )(kwargs))
    engine.planner = planner

    result = engine.ask(session, "他2024赛季打击表现如何？", trace_level="off")

    assert result.semantic.turn_type == "FOLLOW_UP"
    resolver_artifacts = [a for a in result.artifacts if a.kind == "ENTITY_RESOLUTION"]
    assert resolver_artifacts, result.answer
    resolved = resolver_artifacts[0].structured_data["resolved"]
    assert resolved and resolved[0]["id"] == 660271
    assert resolved[0]["source"] == "context"
    # Planner actually saw the canonical reference.
    refs = planner.captured["semantic"].entity_references
    assert any(ref.canonical_id == "660271" for ref in refs)


def test_engine_independent_turn_does_not_inherit_a_stale_entity():
    engine = _engine_without_live_sources()
    session = "f1_independent"
    _seed_prior_goal(engine, session, [_canonical()])
    # A broad, entity-free independent question; the model hallucinates a stale constraint.
    semantic = SemanticInterpretation(
        raw_query="本赛季联盟本垒打最多的是谁？",
        entities=[],
        constraints=[Constraint(kind="entity", value="Shohei Ohtani"), Constraint(kind="season", value=2025)],
        analysis_requests=[AnalysisRequest(text="本垒打最多", objective="most home runs")],
    )
    engine.interpreter = _StubInterpreter(semantic)
    engine.planner = _StubPlanner(lambda _kwargs: [])

    result = engine.ask(session, "本赛季联盟本垒打最多的是谁？", trace_level="off")

    assert result.semantic.turn_type == "INDEPENDENT_GOAL"
    assert not [r for r in result.goal.requirements if r.kind == RequirementKind.ENTITY]


def test_engine_ambiguous_pronoun_asks_with_options():
    engine = _engine_without_live_sources()
    session = "f1_ambiguous"
    _seed_prior_goal(
        engine,
        session,
        [_canonical(), _canonical(surface="山本由伸", player_id="808967", name="Yoshinobu Yamamoto")],
    )
    semantic = SemanticInterpretation(
        raw_query="他的2024赛季表现如何？",
        entities=[EntityMention(mention="他", type_hint="player", carried=True)],
        analysis_requests=[AnalysisRequest(text="表现", objective="performance")],
    )
    engine.interpreter = _StubInterpreter(semantic)
    engine.planner = _StubPlanner(lambda _kwargs: [])

    result = engine.ask(session, "他的2024赛季表现如何？", trace_level="off")

    assert result.projection.clarification is not None
    assert result.projection.goal_status == GoalStatus.WAITING_FOR_USER
    assert len(result.projection.clarification.options) == 2


def test_strip_context_contamination_keeps_an_anchored_explicit_mention():
    from app.contracts.models import ConversationState

    state = ConversationState(session_id="s", recent_entities=[{"id": "660271", "name": "Shohei Ohtani", "mention": "大谷翔平"}])
    semantic = SemanticInterpretation(
        raw_query="大谷翔平2024赛季表现如何？",
        entities=[EntityMention(mention="大谷翔平", type_hint="player")],
        analysis_requests=[AnalysisRequest(text="表现", objective="performance")],
    )
    stripped = strip_context_contamination(semantic, state=state, parent_goal=None)
    assert [e.mention for e in stripped.entities] == ["大谷翔平"]


# =============================================================== F2 real web fixture


def test_web_replay_fixture_preserves_url_and_evidence_span(monkeypatch):
    from app.capabilities.web_research import WebResearchTool
    from app.capabilities.web_search import FetchedPage
    from app.config import WebConfig

    class _Hit:
        def __init__(self):
            self.url = "https://fixture.example/ohtani-interview"
            self.title = "Shohei Ohtani on his approach"
            self.snippet = "fixture"
            self.engine = "fixture"

    class _Backend:
        name = "fixture"

        def __init__(self):
            self.last_error = ""

        def search(self, query, *, limit=5, language="en"):
            return [_Hit()]

    def fake_fetch(url, config):
        return FetchedPage(
            url=url,
            final_url=url,
            title="Shohei Ohtani on his approach",
            text=(
                "Shohei Ohtani explained the mechanical adjustment behind his increased "
                "home run production in an interview. " * 8
            ),
            retrieved_at="2026-09-21T00:00:00Z",
            ok=True,
        )

    tool = WebResearchTool(WebConfig(broad_search=False), llm=None)
    tool.backends = [_Backend()]
    monkeypatch.setattr("app.capabilities.web_research.fetch_page", fake_fetch)

    request = analytical_request(
        id="tr_fixture_web",
        need_id="n9",
        capability="web_research",
        objective="interview explaining increased home run production",
        inputs={
            "entity_names": ["Shohei Ohtani"],
            "objective": "interview explaining increased home run production",
            "target_information": "interview",
            "user_wording": "find an interview",
        },
    )
    result = tool.execute(request)
    assert result.outcome.status == "SUCCESS"
    finding = result.artifact.structured_data["findings"][0]
    assert finding["url"] == "https://fixture.example/ohtani-interview"
    assert finding["evidence_spans"], "the fixture page must yield an evidence span"
    assert any("Shohei Ohtani" in span["text"] for span in finding["evidence_spans"])
