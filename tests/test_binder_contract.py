"""Binder contract enforcement: no silent scope widening, no wrong-type binding."""

from app.capabilities.base import CapabilitySpec
from app.capabilities.registry import CapabilityRegistry
from app.contracts.models import Artifact, Export, PlannerNeed, Scope
from app.runtime.binder import RuntimeBinder


class _DummyCapability:
    def __init__(self, name, accepts, produces, required=None):
        self.spec = CapabilitySpec(name=name, description="", accepts=accepts, produces=produces,
                                   required_inputs=required or [])

    def execute(self, request, ctx):  # pragma: no cover
        raise NotImplementedError


def _registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    registry.register(_DummyCapability("local_analytics", ["PLAYER_ID_SET", "SCOPE"], ["STATISTICAL_RESULT"]))
    registry.register(_DummyCapability("python_compute", ["STATISTICAL_RESULT", "RANKED_ENTITY_SET"], ["DERIVED_MEASURE"]))
    registry.register(_DummyCapability("dynamic_fact", ["PLAYER_ID_SET", "ENTITY_MAPPING"],
                                      ["DYNAMIC_FACT"], ["PLAYER_ID_SET"]))
    return registry


def _entity_artifact(artifact_id: str, need_id: str, *, ids=(660271,), namespace="MLBAM") -> Artifact:
    return Artifact(
        id=artifact_id,
        kind="ENTITY_RESOLUTION",
        structured_data={},
        exports=[Export(id=f"exp_{artifact_id}", type="PLAYER_ID_SET",
                        payload={"ids": list(ids), "names": ["X"]},
                        contract={"namespace": namespace})],
        actual_scope=Scope(entities=[str(i) for i in ids]),
        need_id=need_id,
    )


def _stats_artifact(artifact_id: str, need_id: str, season=2024, export_type="STATISTICAL_RESULT") -> Artifact:
    return Artifact(
        id=artifact_id,
        kind="DATABASE_RESULT",
        structured_data={"rows": [{"season": season}], "row_count": 1},
        exports=[Export(id=f"exp_{artifact_id}", type=export_type, payload={"rows": []})],
        actual_scope=Scope(season=season),
        need_id=need_id,
    )


def test_empty_entity_set_is_never_bound():
    binder = RuntimeBinder(_registry())
    artifacts = [_entity_artifact("a_empty", "n1", ids=())]
    need = PlannerNeed(id="n2", objective="stats", capability="local_analytics",
                       depends_on=["n1"], input_requirements=["PLAYER_ID_SET"])
    result = binder.bind(need, [], artifacts, requested_scope=Scope(entity_names=["洋基"]))
    assert not result.ok
    assert not result.bindings
    assert "EMPTY_ENTITY_SET" in result.failure_codes()


def test_declared_type_cannot_bypass_capability_accepts():
    binder = RuntimeBinder(_registry())
    artifacts = [Artifact(id="a_map", kind="ENTITY_RESOLUTION",
                          exports=[Export(id="exp_map", type="ENTITY_MAPPING", payload={"resolved": []})],
                          need_id="n1")]
    need = PlannerNeed(id="n2", objective="stats", capability="local_analytics",
                       depends_on=["n1"], input_requirements=["ENTITY_MAPPING"])
    result = binder.bind(need, [], artifacts)
    assert not result.ok
    assert not result.bindings
    assert "EXPORT_TYPE_INCOMPATIBLE" in result.failure_codes()


def test_required_input_completeness_is_enforced():
    binder = RuntimeBinder(_registry())
    artifacts = [Artifact(id="a_map", kind="ENTITY_RESOLUTION",
                          exports=[Export(id="exp_map", type="ENTITY_MAPPING", payload={"resolved": []})],
                          need_id="n1")]
    need = PlannerNeed(id="n2", objective="team", capability="dynamic_fact",
                       depends_on=["n1"], input_requirements=["ENTITY_MAPPING"])
    result = binder.bind(need, [], artifacts)
    assert "UNRESOLVED_REQUIRED_INPUT" in result.failure_codes()
    assert result.bindings  # the compatible ENTITY_MAPPING still binds, but the required PLAYER_ID_SET does not


def test_scope_incompatible_artifact_is_rejected():
    binder = RuntimeBinder(_registry())
    artifacts = [_stats_artifact("a2023", "n1", season=2023)]
    need = PlannerNeed(id="n2", objective="compare", capability="python_compute",
                       depends_on=["n1"], input_requirements=["STATISTICAL_RESULT"])
    result = binder.bind(need, [], artifacts, requested_scope=Scope(season=2024))
    assert not result.ok
    assert "SCOPE_INCOMPATIBLE" in result.failure_codes()


def test_scope_compatible_artifact_binds():
    binder = RuntimeBinder(_registry())
    artifacts = [_stats_artifact("a2024", "n1", season=2024)]
    need = PlannerNeed(id="n2", objective="diff", capability="python_compute",
                       depends_on=["n1"], input_requirements=["STATISTICAL_RESULT"])
    result = binder.bind(need, [], artifacts, requested_scope=Scope(season=2024))
    assert result.ok and len(result.bindings) == 1


def test_entity_namespace_mismatch_is_rejected():
    binder = RuntimeBinder(_registry())
    artifacts = [_entity_artifact("a", "n1", namespace="FANGRAPHS")]
    need = PlannerNeed(id="n2", objective="stats", capability="local_analytics",
                       depends_on=["n1"], input_requirements=["PLAYER_ID_SET"])
    result = binder.bind(need, [], artifacts)
    assert "ENTITY_INCOMPATIBLE" in result.failure_codes()
    assert not result.bindings


def test_two_same_type_exports_still_bind_both():
    binder = RuntimeBinder(_registry())
    artifacts = [_stats_artifact("a2023", "n2", season=2023), _stats_artifact("a2024", "n3", season=2024)]
    need = PlannerNeed(id="n4", objective="compare", capability="python_compute",
                       depends_on=["n2", "n3"], input_requirements=["STATISTICAL_RESULT"])
    result = binder.bind(need, [], artifacts)
    assert result.ok
    assert {binding.artifact_id for binding in result.bindings} == {"a2023", "a2024"}


def test_ranked_entity_set_satisfies_python_compute_contract():
    binder = RuntimeBinder(_registry())
    artifacts = [_stats_artifact("a_rank", "n2", export_type="RANKED_ENTITY_SET")]
    need = PlannerNeed(id="n3", objective="diff", capability="python_compute",
                       depends_on=["n2"], input_requirements=["STATISTICAL_RESULT"])
    result = binder.bind(need, [], artifacts)
    # The planner's declared type is over-narrow; the capability accepts RANKED_ENTITY_SET.
    assert result.ok and result.bindings[0].export_type == "RANKED_ENTITY_SET"
