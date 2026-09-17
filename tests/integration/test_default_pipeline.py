"""Public default composition: persisted knowledge, no synthetic analytics claims."""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from app.pipeline import AnalysisPipeline


class DefaultPipelineTests(unittest.TestCase):
    def test_explicit_dates_and_seasons_are_planned_without_silent_ambiguity(self):
        from app.semantic.requirement_decomposer import RuleBasedRequirementDecomposer
        with tempfile.TemporaryDirectory() as directory:
            subject = AnalysisPipeline.default(runtime_dir=Path(directory), demo=True)
            self.addCleanup(subject.close)
            for query, start, end in (
                ("Judge 2023表现", "2023-01-01", "2023-12-31"),
                ("Judge 2024-02-28 to 2024-03-01", "2024-02-28", "2024-03-01"),
            ):
                result = subject.analyze(query)
                requirements = RuleBasedRequirementDecomposer().decompose(result.objectives[0])
                self.assertEqual(requirements[0].descriptor.time_range.model_dump(mode="json"),
                                 {"start": start, "end": end})
            # "Judge 2023 vs 2024" is now a deterministic two-window comparison.
            comparison = subject.analyze("Judge 2023 vs 2024")
            self.assertEqual(len(comparison.objectives), 2)
            for query in ("Judge近0天", "Judge 2024-03-01 to 2024-02-28"):
                with self.assertRaises(ValueError):
                    subject.analyze(query)

    def test_recent_date_window_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subject = AnalysisPipeline.default(runtime_dir=root, demo=True,
                                                today=lambda: date(2026, 3, 1))
            result = subject.analyze("Judge近30天表现", run_id="dated")
            window = next(c for c in result.objectives[0].constraints if c.key == "date_range")
            self.assertEqual(window.values, ("2026-01-31", "2026-03-01"))
            subject.close()
            resumed = AnalysisPipeline.default(runtime_dir=root, demo=True,
                                                today=lambda: date(2026, 4, 1))
            self.addCleanup(resumed.close)
            recovered = resumed.resume_run("dated")
            self.assertEqual(recovered.objectives[0].constraints, result.objectives[0].constraints)
            from app.semantic.requirement_decomposer import RuleBasedRequirementDecomposer
            requirements = RuleBasedRequirementDecomposer().decompose(recovered.objectives[0])
            self.assertEqual(requirements[0].descriptor.time_range.start, date(2026, 1, 31))
            self.assertEqual(requirements[0].descriptor.time_range.end, date(2026, 3, 1))
            self.assertIsNone(requirements[1].descriptor.time_range)
            self.assertFalse(any(c.key == "date_range" for c in requirements[1].descriptor.constraints))

    def test_default_pipeline_answers_definition_from_shared_knowledge(self):
        with tempfile.TemporaryDirectory() as directory:
            subject = AnalysisPipeline.default(runtime_dir=Path(directory))
            self.addCleanup(subject.close)
            result = subject.analyze("DFA是什么意思？")
            self.assertEqual(result.objective_statuses, ("COMPLETE",))
            self.assertIn("40-man", result.responses[0])
            self.assertTrue(result.response_packages[0].accepted_evidence)
            self.assertTrue(all(item.source == "shared-knowledge" for item in
                                result.response_packages[0].accepted_evidence))
            self.assertNotIn("synthetic", result.responses[0])

    def test_chinese_team_alias_resolves_canonical_entity(self):
        with tempfile.TemporaryDirectory() as directory:
            subject = AnalysisPipeline.default(runtime_dir=Path(directory))
            self.addCleanup(subject.close)
            result = subject.analyze("道奇属于哪个分区？")
            self.assertEqual(result.objectives[0].entities[0].identifier, "LAD")
            self.assertIn("National League West", result.responses[0])

    def test_knowledge_capability_does_not_hide_paid_injury_provider(self):
        from app.agent.routing import ToolCapability
        with tempfile.TemporaryDirectory() as directory:
            subject = AnalysisPipeline.default(runtime_dir=Path(directory), capabilities=(
                ToolCapability(tool="paid-news", source_kind="WEB", cost="PAID",
                               supported_artifact_types=("EVIDENCE",)),))
            self.addCleanup(subject.close)
            result = subject.analyze("Judge injury")
            self.assertEqual(len(result.permissions), 1)
            self.assertEqual(result.permissions[0].tool, "paid-news")

    def test_default_paid_web_provider_approval_and_rejection(self):
        from app.models.evidence import RawWebResult
        from app.models.interaction import PermissionAnswer
        calls = []
        def fetch(task):
            calls.append(task.task_id)
            return RawWebResult(result_id=task.task_id, url="https://example.test/fixture",
                title="Injury fixture", source="test-fixture",
                text="Aaron Judge was placed on the injured list in 2025 with a wrist strain.")
        with tempfile.TemporaryDirectory() as directory:
            subject = AnalysisPipeline.default(runtime_dir=Path(directory), web_fetcher=fetch,
                                                web_cost="PAID")
            self.addCleanup(subject.close)
            for approved in (False, True):
                waiting = subject.analyze("Judge injury")
                self.assertEqual(calls, [])
                answer = PermissionAnswer(permission_ref=waiting.permissions[0].permission_id,
                                           approved=approved)
                result = subject.resume_permission(waiting.run_ids[0], answer)
                if approved:
                    self.assertEqual(result.objective_statuses, ("COMPLETE",))
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(result.response_packages[0].accepted_evidence[0].source_kind, "WEB")
                with self.assertRaises(ValueError):
                    subject.resume_permission(waiting.run_ids[0], answer)
