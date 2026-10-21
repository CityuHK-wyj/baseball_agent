"""Stage 6: the Planner chooses between two domain families, not per-provider tools."""
from app.capabilities.base import (
    CAPABILITY_FAMILIES, FAMILY_LOCAL_DATA_ANALYTICS, FAMILY_SUPPORTING,
    FAMILY_WEB_EXTERNAL_RESEARCH, CapabilitySpec,
)
from app.capabilities.registry import CapabilityRegistry
from app.capabilities.analytics.tool import LocalAnalyticsTool
from app.capabilities.web_research import WebResearchTool
from app.capabilities.dynamic_fact import DynamicFactTool
from app.capabilities.python_compute import PythonComputeTool
from app.capabilities.artifact_projection import ArtifactProjectionTool
from app.capabilities.entity_resolution import EntityResolutionTool
from app.capabilities.knowledge import SharedKnowledgeTool


def _registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    registry.register(LocalAnalyticsTool(None, None))
    registry.register(WebResearchTool(None, None))
    registry.register(EntityResolutionTool(None, None, None))
    registry.register(DynamicFactTool(None, None))
    registry.register(SharedKnowledgeTool(None))
    registry.register(PythonComputeTool(None))
    registry.register(ArtifactProjectionTool())
    return registry


def test_exactly_two_domain_acquisition_families():
    registry = _registry()
    families = registry.capability_families()
    assert set(families) == set(CAPABILITY_FAMILIES)
    assert families[FAMILY_LOCAL_DATA_ANALYTICS] == ["local_analytics"]
    assert set(families[FAMILY_WEB_EXTERNAL_RESEARCH]) == {"web_research", "dynamic_fact"}
    # Identity, knowledge, projection and bounded computation stay execution infrastructure.
    assert set(families[FAMILY_SUPPORTING]) == {
        "entity_resolution", "shared_knowledge", "python_compute", "artifact_projection",
    }


def test_no_provider_or_api_is_a_top_level_domain_family():
    registry = _registry()
    domain = set(registry.capability_families()[FAMILY_LOCAL_DATA_ANALYTICS]) | set(
        registry.capability_families()[FAMILY_WEB_EXTERNAL_RESEARCH])
    assert domain == {"local_analytics", "web_research", "dynamic_fact"}
    # The provider-specific adapter is grouped inside the web family, not its own source.
    assert "dynamic_fact" not in registry.capability_families()[FAMILY_SUPPORTING]


def test_planner_view_exposes_family_for_every_capability():
    view = _registry().planner_view({"local_analytics": False})
    assert view
    assert all(item["family"] in CAPABILITY_FAMILIES for item in view)
    local = next(item for item in view if item["capability"] == "local_analytics")
    assert local["family"] == FAMILY_LOCAL_DATA_ANALYTICS
    assert local["available"] is False


def test_unclassified_capability_defaults_to_supporting_not_a_new_domain_family():
    registry = CapabilityRegistry()

    class AdHoc:
        spec = CapabilitySpec(name="ad_hoc_adapter", description="test", accepts=[], produces=[])

        def execute(self, request, context):  # pragma: no cover - not executed
            raise NotImplementedError

    registry.register(AdHoc())
    families = registry.capability_families()
    assert "ad_hoc_adapter" in families[FAMILY_SUPPORTING]
    assert "ad_hoc_adapter" not in families[FAMILY_LOCAL_DATA_ANALYTICS]
    assert "ad_hoc_adapter" not in families[FAMILY_WEB_EXTERNAL_RESEARCH]
