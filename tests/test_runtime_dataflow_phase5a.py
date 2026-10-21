"""Phase 5A regression tests: runtime dataflow integrity.

Covers the three systemic defects this phase repairs, using general contract /
property / metamorphic assertions (no player-, year- or question-specific cases):

F1  contextual entity propagation (ADR 0018)
F2  typed artifact interoperability (ADR 0019)
F5  analytical enumeration completeness (ADR 0020)

plus checks that the existing SQL guard and measure verification do not regress.
"""

from __future__ import annotations

from app.capabilities.analytics.guard import guard_sql
from app.capabilities.analytics.ir import Aggregate, AnalyticalQuery
from app.capabilities.analytics.measures import declare_measures, resolve_texts
from app.capabilities.artifact_projection import ArtifactProjectionTool
from app.capabilities.base import CapabilitySpec
from app.capabilities.entity_resolution import EntityResolutionTool
from app.capabilities.python_compute import PythonComputeTool
from app.capabilities.registry import CapabilityRegistry
from tools.requirement_ancestry_fixture import Goal
from app.contracts.models import (
    AnalysisRequest,
    Artifact,
    Constraint,
    ConversationState,
    EntityMention,
    EntityReference,
    EnumerationContract,
    Export,
    IdentityLevel,
    InputBinding,
    PlannerNeed,
    RequirementKind,
    Scope,
    SelectionRule,
    SemanticInterpretation,
    ToolRequest,
    UserRequirement,
)
from app.runtime.binder import RuntimeBinder
from app.runtime.context import ExecutionContext
from app.runtime.engine import bound_search_subjects
from app.semantic.entities import (
    build_entity_references,
    is_contextual_surface,
    mentions_from_requirements,
)
from app.semantic.goals import RequirementBuilder

# --------------------------------------------------------------------------- helpers


def _ctx(artifacts: list[Artifact] | None = None) -> ExecutionContext:
    return ExecutionContext(
        goal=Goal(statement="g", raw_query="q"),
        conversation=ConversationState(session_id="s"),
        artifacts=list(artifacts or []),
    )


def _identity_ref(surface="大谷翔平", player_id="660271", name="Shohei Ohtani") -> EntityReference:
    return EntityReference(
        surface=surface,
        canonical_id=player_id,
        canonical_name=name,
        namespace="MLBAM",
        status=IdentityLevel.CANONICAL,
        identity_verified=True,
        source="context",
    )


def _prior_goal(*refs: EntityReference) -> Goal:
    return Goal(
        statement="prior",
        raw_query="prior query",
        requirements=[
            UserRequirement(
                id=f"r{i}",
                kind=RequirementKind.ENTITY,
                statement="entity",
                value={"mention": ref.surface},
                entity_ref=ref,
            )
            for i, ref in enumerate(refs, start=1)
        ],
    )


def _follow_up_semantic(surface="他", *, season=2024) -> SemanticInterpretation:
    return SemanticInterpretation(
        raw_query=f"{surface}{season}赛季呢？",
        entities=[EntityMention(mention=surface, type_hint="player", carried=True)],
        constraints=[Constraint(kind="season", value=season)],
        analysis_requests=[AnalysisRequest(text="打击表现", objective="batting performance")],
    )


class _EmptyStore:
    def match_alias(self, text):
        return None

    def record_candidate(self, *args, **kwargs):
        return ""


class _EmptyDirectory:
    def search_by_name(self, name, limit=5):
        return []


class _WebStub:
    def __init__(self):
        self.spec = CapabilitySpec(
            name="web_research",
            description="",
            accepts=["SEARCH_QUERY"],
            produces=["WEB_EVIDENCE"],
        )

    def execute(self, request, ctx):  # pragma: no cover - not called in these tests
        raise NotImplementedError


# ============================================================ F1 contextual entities


def test_contextual_pronoun_carries_canonical_identity_across_turns():
    parent = _prior_goal(_identity_ref())
    semantic = _follow_up_semantic()
    refs = build_entity_references(semantic, state=ConversationState(session_id="s"), parent_goal=parent)

    assert len(refs) == 1
    ref = refs[0]
    # The original surface survives, and so does the canonical identity.
    assert ref.surface == "他"
    assert ref.canonical_id == "660271"
    assert ref.canonical_name == "Shohei Ohtani"
    assert ref.namespace == "MLBAM"
    assert ref.status == IdentityLevel.CANONICAL
    assert ref.antecedent == "大谷翔平"
    assert ref.carried_from_goal == parent.id
    assert ref.identity_verified is True

    semantic.entity_references = refs
    goal = RequirementBuilder().build(semantic, parent_goal=parent, turn_type="FOLLOW_UP")
    entity = [r for r in goal.requirements if r.kind == RequirementKind.ENTITY][0]
    assert entity.value["mention"] == "他"
    assert entity.value["canonical_id"] == "660271"
    assert entity.entity_ref.canonical_id == "660271"

    # The ambiguous surface never reaches the tool unresolved.
    mention = mentions_from_requirements(goal)[0]
    assert mention["mention"] == "他"
    assert mention["canonical_id"] == "660271"
    assert mention["status"] == "CANONICAL"


def test_interpreter_named_constraint_resolves_a_pronoun_without_a_prior_turn():
    semantic = SemanticInterpretation(
        raw_query="他2024赛季呢？",
        entities=[EntityMention(mention="他", type_hint="player", carried=True)],
        constraints=[Constraint(kind="entity", value="Mookie Betts"), Constraint(kind="season", value=2024)],
        analysis_requests=[AnalysisRequest(text="表现", objective="performance")],
    )
    refs = build_entity_references(semantic, state=ConversationState(session_id="s"), parent_goal=None)
    assert len(refs) == 1, "the named constraint must attach to the pronoun, not duplicate it"
    ref = refs[0]
    assert ref.surface == "他"
    assert ref.canonical_name == "Mookie Betts"
    # A model-named entity is a name, never a fabricated canonical id.
    assert ref.canonical_id is None
    assert ref.status == IdentityLevel.RESOLVED


def test_true_ambiguity_is_not_forced_to_a_canonical_id():
    parent = _prior_goal(_identity_ref(), _identity_ref(surface="山本由伸", player_id="808967", name="Yoshinobu Yamamoto"))
    ref = build_entity_references(
        _follow_up_semantic(), state=ConversationState(session_id="s"), parent_goal=parent
    )[0]
    assert ref.ambiguous is True
    assert ref.canonical_id is None
    assert ref.status == IdentityLevel.CANDIDATE


def test_independent_question_does_not_inherit_a_prior_entity():
    semantic = SemanticInterpretation(
        raw_query="Aaron Judge 2024赛季打击表现如何？",
        entities=[EntityMention(mention="Aaron Judge", type_hint="player")],
        constraints=[Constraint(kind="season", value=2024)],
        analysis_requests=[AnalysisRequest(text="表现", objective="performance")],
    )
    state = ConversationState(
        session_id="s",
        recent_entities=[{"id": "660271", "name": "Shohei Ohtani", "mention": "大谷翔平"}],
    )
    ref = build_entity_references(semantic, state=state, parent_goal=None)[0]
    # An explicit, non-contextual mention never silently adopts the prior subject.
    assert ref.surface == "Aaron Judge"
    assert ref.canonical_id is None
    assert not ref.is_contextual()
    assert is_contextual_surface("Aaron Judge") is False


def test_entity_resolution_honours_contextual_canonical_identity():
    tool = EntityResolutionTool(_EmptyStore(), _EmptyDirectory(), llm=None)
    request = ToolRequest(
        need_id="n1",
        capability="entity_resolution",
        objective="resolve carried subject",
        inputs={
            "mentions": [
                {
                    "mention": "他",
                    "type_hint": "player",
                    "canonical_id": "660271",
                    "canonical_name": "Shohei Ohtani",
                    "namespace": "MLBAM",
                    "status": "CANONICAL",
                    "antecedent": "大谷翔平",
                }
            ]
        },
    )
    result = tool.execute(request)
    assert result.outcome.status == "SUCCESS"
    resolved = result.artifact.structured_data["resolved"]
    assert resolved and resolved[0]["id"] == 660271
    assert resolved[0]["source"] == "context"
    identity = result.artifact.structured_data["identity"]["他"]
    assert identity["level"] == IdentityLevel.CANONICAL.value
    assert identity["id"] == 660271


# ======================================================= F2 typed interoperability


def _rank_artifact() -> Artifact:
    rows = [
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
                contract={"grain": "GROUP"},
            )
        ],
        actual_scope=Scope(
            season=2025, game_population="REGULAR_SEASON", measures=["home_runs"], source="POSTGRES"
        ),
        enumeration=EnumerationContract(
            population="REGULAR_SEASON",
            season=2025,
            source="POSTGRES",
            returned_rows=len(rows),
            limit=5,
            completeness="TRUNCATED",
            ordering=["home_runs DESC"],
            ordering_verified=True,
            identity_columns=["batter_id"],
        ),
        need_id="n1",
    )


def _projection_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    registry.register(ArtifactProjectionTool())
    registry.register(_WebStub())
    return registry


def test_binder_rejects_rank_to_web_without_a_converter():
    binder = RuntimeBinder(_projection_registry())
    need = PlannerNeed(
        id="n2",
        objective="interview the subject",
        capability="web_research",
        depends_on=["n1"],
        input_requirements=["SEARCH_QUERY"],
    )
    result = binder.bind(need, [], [_rank_artifact()])
    assert not result.ok
    assert not result.bindings
    assert "EXPORT_TYPE_INCOMPATIBLE" in result.failure_codes()


def test_projection_converts_rank_to_searchable_subject_with_lineage():
    registry = _projection_registry()
    binder = RuntimeBinder(registry)
    rank = _rank_artifact()
    needs = [PlannerNeed(id="n1", objective="rank", capability="local_analytics")]
    projection_need = PlannerNeed(
        id="n2",
        objective="project the top subject",
        capability="artifact_projection",
        depends_on=["n1"],
        input_requirements=["RANKED_ENTITY_SET"],
    )
    binding = binder.bind(projection_need, needs, [rank])
    assert binding.ok

    ctx = _ctx([rank])
    request = ToolRequest(
        id="tr_proj",
        need_id="n2",
        capability="artifact_projection",
        objective=projection_need.objective,
        inputs={"selection": {"mode": "TOP"}},
        bindings=binding.bindings,
    )
    result = ArtifactProjectionTool().execute(request, ctx)
    artifact = result.artifact
    assert result.outcome.status == "SUCCESS"

    search = artifact.export("SEARCH_QUERY")
    assert search is not None
    assert search.payload["subjects"] == ["Shohei Ohtani"]
    assert search.payload["canonical_id"] == "660271"
    assert search.payload["source_artifact_id"] == "art_rank"
    assert search.payload["selected_row_identity"]["canonical_id"] == 660271
    assert "art_rank" in artifact.lineage
    assert artifact.structured_data["selection"]["selection_rule"]["mode"] == "TOP"

    # The projected artifact is now a legal web input; no Binder relaxation was needed.
    web_need = PlannerNeed(
        id="n3",
        objective="find an interview",
        capability="web_research",
        depends_on=["n2"],
        input_requirements=["SEARCH_QUERY"],
    )
    web_binding = binder.bind(web_need, [*needs, projection_need], [rank, artifact])
    assert web_binding.ok
    assert web_binding.bindings[0].export_type == "SEARCH_QUERY"
    assert bound_search_subjects(_ctx([rank, artifact]), web_binding) == ["Shohei Ohtani"]


def test_projection_records_explicit_row_selection():
    registry = _projection_registry()
    binder = RuntimeBinder(registry)
    rank = _rank_artifact()
    need = PlannerNeed(
        id="n2",
        objective="project row 1",
        capability="artifact_projection",
        depends_on=["n1"],
        input_requirements=["RANKED_ENTITY_SET"],
    )
    binding = binder.bind(need, [], [rank])
    request = ToolRequest(
        id="tr_proj2",
        need_id="n2",
        capability="artifact_projection",
        objective=need.objective,
        inputs={"row_index": 1},
        bindings=binding.bindings,
    )
    artifact = ArtifactProjectionTool().execute(request, _ctx([rank])).artifact
    assert artifact.structured_data["selected_row"]["batter_id"] == 592450
    assert artifact.structured_data["selection"]["selection_rule"] == SelectionRule(
        mode="INDEX", by="home_runs DESC", index=1, detail="explicit row index"
    ).model_dump()


# ======================================================= F5 enumeration completeness


class _CompiledStub:
    def __init__(self, *, grouped: bool):
        self.group_columns = ["batter_id"] if grouped else []
        self.output_columns = ["batter_id", "home_runs"] if grouped else ["home_runs"]


class _ResultStub:
    def __init__(self, row_count: int):
        self.row_count = row_count


def _enum(query: AnalyticalQuery, *, grouped: bool, row_count: int, limit: int | None):
    from app.capabilities.analytics.tool import _enumeration_contract

    return _enumeration_contract(query, _CompiledStub(grouped=grouped), _ResultStub(row_count), "POSTGRES", limit)


def test_enumeration_contract_distinguishes_complete_from_truncated():
    grouped = AnalyticalQuery(
        group_by=["batter_id"],
        aggregates=[Aggregate(name="home_runs", function="COUNT", field="*")],
        sort=[],
    )
    scalar = AnalyticalQuery(aggregates=[Aggregate(name="home_runs", function="COUNT", field="*")])

    truncated = _enum(grouped, grouped=True, row_count=100, limit=100)
    assert truncated.completeness == "TRUNCATED"
    assert truncated.limit == 100 and truncated.returned_rows == 100

    complete_short = _enum(grouped, grouped=True, row_count=7, limit=100)
    assert complete_short.completeness == "COMPLETE"

    assert _enum(grouped, grouped=True, row_count=10, limit=None).completeness == "COMPLETE"
    assert _enum(scalar, grouped=False, row_count=1, limit=1).completeness == "COMPLETE"


def _stats_artifact(
    artifact_id: str = "art_stats",
    *,
    need_id: str = "n1",
    season: int = 2025,
    completeness: str = "COMPLETE",
    source: str = "POSTGRES",
    rows=None,
) -> Artifact:
    rows = rows if rows is not None else [{"season": season, "home_runs": 10}]
    columns = list(rows[0].keys())
    return Artifact(
        id=artifact_id,
        kind="DATABASE_RESULT",
        structured_data={"rows": rows, "row_count": len(rows)},
        exports=[
            Export(id=f"exp_{artifact_id}", type="STATISTICAL_RESULT", payload={"rows": rows, "columns": columns})
        ],
        actual_scope=Scope(season=season, source=source, measures=["home_runs"], entities=["660271"]),
        enumeration=EnumerationContract(
            population="REGULAR_SEASON",
            season=season,
            source=source,
            returned_rows=len(rows),
            limit=100 if completeness == "TRUNCATED" else None,
            completeness=completeness,
            qualification="PA >= 100",
            identity_columns=["batter_id"],
            entity_kind="PLAYER",
            entity_namespace="MLBAM",
        ),
        need_id=need_id,
    )


def _compute_request(artifact: Artifact, *, global_comparison: bool, program: str | None = None) -> ToolRequest:
    binding = InputBinding(
        need_id="n2",
        export_id=f"exp_{artifact.id}",
        export_type="STATISTICAL_RESULT",
        artifact_id=artifact.id,
        source_need_id=artifact.need_id,
    )
    return ToolRequest(
        id="tr_compute",
        need_id="n2",
        capability="python_compute",
        objective="largest change",
        inputs={
            "global_comparison": global_comparison,
            "program": program or 'output = [{"total": sum(v[0]["home_runs"] for v in inputs.values())}]',
        },
        bindings=[binding],
    )


def test_global_comparison_on_truncated_input_is_blocked():
    tool = PythonComputeTool(llm=None)
    truncated = _stats_artifact(completeness="TRUNCATED")
    result = tool.execute(_compute_request(truncated, global_comparison=True), _ctx([truncated]))
    assert result.outcome.status == "BLOCKED"
    assert result.outcome.code == "INCOMPLETE_INPUT"
    assert result.artifact is None


def test_global_comparison_on_complete_input_is_allowed_and_carries_contract():
    tool = PythonComputeTool(llm=None)
    complete = _stats_artifact(completeness="COMPLETE")
    result = tool.execute(_compute_request(complete, global_comparison=True), _ctx([complete]))
    assert result.outcome.status == "SUCCESS"
    enumeration = result.artifact.enumeration
    assert enumeration is not None and enumeration.completeness == "COMPLETE"


def test_non_global_difference_may_still_use_a_bounded_input():
    tool = PythonComputeTool(llm=None)
    truncated = _stats_artifact(completeness="TRUNCATED")
    result = tool.execute(
        _compute_request(truncated, global_comparison=False, program='output = [{"n": 1}]'),
        _ctx([truncated]),
    )
    assert result.outcome.status == "SUCCESS"


def test_cross_source_comparison_preserves_each_input_scope():
    tool = PythonComputeTool(llm=None)
    postgres = _stats_artifact("art_pg", need_id="n1", season=2024, source="POSTGRES")
    parquet = _stats_artifact("art_pq", need_id="n3", season=2023, source="PARQUET")
    bindings = [
        InputBinding(need_id="n2", export_id="exp_art_pg", export_type="STATISTICAL_RESULT", artifact_id="art_pg", source_need_id="n1"),
        InputBinding(need_id="n2", export_id="exp_art_pq", export_type="STATISTICAL_RESULT", artifact_id="art_pq", source_need_id="n3"),
    ]
    request = ToolRequest(
        id="tr_cross",
        need_id="n2",
        capability="python_compute",
        objective="difference",
        inputs={
            "global_comparison": True,
            "operation": {"kind": "DELTA", "identity": "PLAYER", "partition_by": "SEASON"},
            "program": (
                'pg = inputs["n1"][0]["home_runs"]\n'
                'pq = inputs["n3"][0]["home_runs"]\n'
                'output = [{"delta": pg - pq}]'
            ),
        },
        bindings=bindings,
    )
    result = tool.execute(request, _ctx([postgres, parquet]))
    assert result.outcome.status == "SUCCESS"
    scopes = result.artifact.structured_data["source_scopes"]
    by_source = {scope["source"]: scope for scope in scopes}
    assert set(by_source) == {"POSTGRES", "PARQUET"}
    assert by_source["POSTGRES"]["season"] == 2024
    assert by_source["PARQUET"]["season"] == 2023
    # The pydantic contract survives even if the program uses a different alias.
    assert len(scopes) == 2


# ============================================== existing safety regressions


def test_sql_guard_still_rejects_non_read_statements():
    assert guard_sql("SELECT 1").ok
    assert not guard_sql("DROP TABLE public.statcast_pitches").ok
    assert not guard_sql("SELECT 1; SELECT 2").ok


def test_measure_verification_still_does_not_attest_from_a_column_name():
    assert resolve_texts(["打击率"])[0].key == "avg"
    declared = declare_measures(
        AnalyticalQuery(aggregates=[Aggregate(name="avg", function="COUNT", field="*")])
    )
    item = next(item for item in declared if item.column == "avg")
    # A COUNT aliased "avg" is not a batting average.
    assert item.key != "avg" or not item.verified
