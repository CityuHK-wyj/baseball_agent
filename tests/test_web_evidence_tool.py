import unittest

from app.agent.executor import Executor
from app.agent.orchestrator import Orchestrator
from app.agent.planner import RuleBasedPlanner
from app.agent.registry import ArtifactRegistry
from app.agent.routing import Router, ToolCapability
from app.assessment.judge import RuleBasedJudge
from app.assessment.service import AssessmentService
from app.models.artifacts import ArtifactDescriptor
from app.models.evidence import RawWebResult
from app.semantic.evidence import RuleBasedEvidenceExtractor
from app.tools.web_evidence import WebEvidenceTool
from tests.factories import objective, requirement


class WebEvidenceToolTests(unittest.TestCase):
    def test_raw_web_result_becomes_assessed_evidence_without_exposing_raw_page(self):
        raw = RawWebResult(
            result_id="page-1", url="https://example.test/judge", title="Judge injury update",
            source="official-club",
            text="Aaron Judge was placed on the injured list in 2025 with a wrist strain.",
        )
        identifiers = iter(f"id-{index}" for index in range(100))
        ids = lambda _prefix: next(identifiers)
        registry = ArtifactRegistry()
        assessment = AssessmentService(registry, RuleBasedJudge(), id_factory=ids)
        injury_requirement = requirement(
            requirement_id="injury", objective_ref="o1",
            descriptor=ArtifactDescriptor(
                artifact_type="EVIDENCE", data_keys=("injury_status",),
                granularity="event", population_scope="player:1",
            ),
            evidence_purpose="EXISTENCE",
        )
        tool = WebEvidenceTool(lambda _task: raw, RuleBasedEvidenceExtractor(
            id_factory=lambda _prefix: "evidence-1"), (injury_requirement,))
        router = Router((ToolCapability(tool=tool.name, source_kind="WEB",
                                        supported_artifact_types=("EVIDENCE",)),), id_factory=ids)
        orchestrator = Orchestrator(
            RuleBasedPlanner(id_factory=ids), router,
            Executor({tool.name: tool}, max_retries=0, id_factory=ids),
            assessment, registry, id_factory=ids,
        )

        result = orchestrator.run(objective(), (injury_requirement,))

        self.assertEqual(result.objective_state.status, "COMPLETE")
        self.assertEqual([item.artifact_ref for item in result.response_package.accepted_evidence],
                         ["evidence-1"])
        self.assertEqual(result.response_package.accepted_evidence[0].source_kind, "WEB")
        self.assertIn("raw-page-1", [item.artifact_id for item in registry.artifacts()])
        self.assertNotIn("raw-page-1", [item.artifact_ref
                                          for item in result.response_package.accepted_evidence])


if __name__ == "__main__":
    unittest.main()
