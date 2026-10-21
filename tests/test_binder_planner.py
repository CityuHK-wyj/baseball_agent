from app.capabilities.analytics.catalog import CATALOG
from app.capabilities.registry import CapabilityRegistry
from app.contracts.models import Artifact, Export, Goal, PlannerNeed, RequirementKind, Scope, UserRequirement
from app.planner.planner import Planner, derive_criticality
from app.runtime.binder import RuntimeBinder


class _DummyCapability:
    def __init__(self, name, accepts, produces, required=None):
        from app.capabilities.base import CapabilitySpec

        self.spec = CapabilitySpec(name=name, description="", accepts=accepts, produces=produces, required_inputs=required or [])

    def execute(self, request, ctx):  # pragma: no cover
        raise NotImplementedError


def _registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    registry.register(_DummyCapability("local_analytics", ["PLAYER_ID_SET"], ["STATISTICAL_RESULT"]))
    registry.register(_DummyCapability("python_compute", ["STATISTICAL_RESULT"], ["DERIVED_MEASURE"], ["STATISTICAL_RESULT"]))
    return registry


def _stat_artifact(artifact_id: str, need_id: str, rows) -> Artifact:
    return Artifact(
        id=artifact_id,
        kind="DATABASE_RESULT",
        structured_data={"rows": rows, "row_count": len(rows)},
        exports=[Export(id=f"exp_{artifact_id}", type="STATISTICAL_RESULT", payload={"rows": rows}, contract={})],
        actual_scope=Scope(season=rows[0].get("season") if rows else None),
        need_id=need_id,
    )


def test_binder_binds_one_export_per_dependency():
    binder = RuntimeBinder(_registry())
    artifacts = [
        _stat_artifact("a2023", "n2", [{"season": 2023, "home_runs": 44}]),
        _stat_artifact("a2024", "n3", [{"season": 2024, "home_runs": 54}]),
    ]
    need = PlannerNeed(
        id="n4",
        objective="compare",
        capability="python_compute",
        depends_on=["n2", "n3"],
        input_requirements=["STATISTICAL_RESULT"],
    )
    result = binder.bind(need, [], artifacts)
    assert result.ok
    assert len(result.bindings) == 2
    assert {b.export_id for b in result.bindings} == {"exp_a2023", "exp_a2024"}
    assert {b.source_need_id for b in result.bindings} == {"n2", "n3"}


def test_binder_reports_unresolved_dependency():
    binder = RuntimeBinder(_registry())
    need = PlannerNeed(id="n4", objective="x", capability="python_compute", depends_on=["missing"], input_requirements=["STATISTICAL_RESULT"])
    result = binder.bind(need, [], [])
    assert not result.ok
    assert any("missing" in item for item in result.unresolved)


def test_binder_pseudo_reference_is_not_required_to_resolve():
    # A planner hint like "n2:STATISTICAL_RESULT" is not an export id; the binder resolves
    # the real export from the declared dependency instead.
    binder = RuntimeBinder(_registry())
    artifacts = [_stat_artifact("a", "n2", [{"season": 2023, "home_runs": 44}])]
    need = PlannerNeed(id="n3", objective="x", capability="python_compute", depends_on=["n2"], input_requirements=["STATISTICAL_RESULT"])
    result = binder.bind(need, [], artifacts)
    assert result.ok and result.bindings[0].export_id == "exp_a"


def _goal() -> Goal:
    return Goal(
        statement="s",
        raw_query="q",
        requirements=[
            UserRequirement(id="r1", kind=RequirementKind.ENTITY, statement="entity", value={"mention": "X"}),
            UserRequirement(id="r2", kind=RequirementKind.ANALYSIS, statement="analysis"),
        ],
    )


def test_planner_validation_rejects_unknown_capability_and_strips_physical_schema():
    planner = Planner(llm=None, registry=_registry(), catalog=CATALOG)
    raw = [
        {"id": "n1", "objective": "a", "capability": "does_not_exist"},
        {
            "id": "n2",
            "objective": "b",
            "capability": "local_analytics",
            "constraints": {"table": "statcast_pitches", "field": "release_speed", "season": 2025},
            "input_refs": ["n1:PLAYER_ID_SET"],
        },
    ]
    accepted, rejected = planner._validate(raw, _goal(), [], 8)
    reasons = {r["id"]: r["reason"] for r in rejected}
    assert reasons["n1"] == "UNSUPPORTED_CAPABILITY"
    n2_reasons = {r["reason"] for r in rejected if r["id"] == "n2"}
    assert "PHYSICAL_SCHEMA_STRIPPED" in n2_reasons
    assert "PSEUDO_REFERENCE" in n2_reasons
    assert accepted[0].constraints == {"season": 2025}
    assert all(":" not in key for key in accepted[0].constraints)


def test_planner_rejects_unknown_dependency():
    planner = Planner(llm=None, registry=_registry(), catalog=CATALOG)
    raw = [{"id": "n1", "objective": "a", "capability": "local_analytics", "depends_on": ["ghost"]}]
    accepted, rejected = planner._validate(raw, _goal(), [], 8)
    assert not accepted
    assert rejected[0]["reason"] == "DEPENDENCY_REJECTED"


def test_criticality_derivation_cannot_be_raised_by_hint():
    needs = [
        PlannerNeed(id="n1", objective="stats", capability="local_analytics", requires=["r2"], criticality_hint="core"),
        PlannerNeed(id="n2", objective="news", capability="python_compute", criticality_hint="core"),  # no link to core req
    ]
    derive_criticality(needs, _goal())
    by_id = {n.id: n for n in needs}
    assert by_id["n1"].criticality.value == "CORE"
    assert by_id["n2"].criticality.value == "SUPPORTING"
