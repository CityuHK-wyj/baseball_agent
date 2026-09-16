"""Real read-only analytics vertical slice: typed constraints -> Parquet -> ranked answer.

No synthetic fallback. These tests exercise the local Parquet archive through the
guarded DuckDB executor, so they are the live-verified analytics path.
"""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from app.config import settings
from app.models.clarification import ClarificationAnswer
from app.models.contracts import PopulationConstraint
from app.pipeline import AnalysisPipeline
from app.semantic.field_mapping import ZONE_UPPER_THIRD
from app.semantic.requirement_decomposer import RuleBasedRequirementDecomposer

TARGET = ("Find the top 5 MLB hitters by Exit Velocity when, after reaching two strikes, "
          "they faced fastballs above 95 mph located near the upper edge of the strike "
          "zone during 2023.")

_PARQUET_PRESENT = any(settings.parquet_archive_path.glob("mlb_statcast_*.parquet"))
_POSTGRES_PRESENT = bool(settings.postgres_password)


@unittest.skipUnless(_PARQUET_PRESENT, "historical Parquet archive is not present locally")
class RealAnalyticsSliceTests(unittest.TestCase):
    def _pipeline(self, root: Path, today=None) -> AnalysisPipeline:
        return AnalysisPipeline.default(
            runtime_dir=root, today=today or (lambda: date(2024, 1, 1)))

    def _run_target(self, subject: AnalysisPipeline, run_id: str,
                    option_index: int = 0):
        waiting = subject.analyze(TARGET, run_id=run_id)
        self.assertTrue(waiting.needs_clarification)
        self.assertEqual(waiting.clarifications[0].kind, "CONSTRAINT")
        option = waiting.clarifications[0].options[option_index]
        return subject.resume_clarification(
            run_id, ClarificationAnswer(
                clarification_ref=waiting.clarifications[0].clarification_id,
                chosen_option_id=option.option_id))

    def test_target_query_completes_with_real_parquet_data(self):
        with tempfile.TemporaryDirectory() as directory:
            subject = self._pipeline(Path(directory))
            self.addCleanup(subject.close)
            result = self._run_target(subject, "real-analytics")
            self.assertEqual(result.objective_statuses, ("COMPLETE",))
            package = result.response_packages[0]
            self.assertTrue(package.accepted_evidence)
            self.assertEqual(package.accepted_evidence[0].source_kind, "PARQUET")
            self.assertNotEqual(package.accepted_evidence[0].source_kind, "SYNTHETIC")
            self.assertIn("Result:", result.responses[0])
            self.assertIn("batter", result.responses[0])
            self.assertNotIn("synthetic", result.responses[0])
            run = result.runs[0]
            self.assertEqual(run.completion_report.stop_reason, "COMPLETE")
            self.assertTrue(run.retrieved_artifacts)

    def test_location_definition_is_preserved_not_redefined(self):
        with tempfile.TemporaryDirectory() as directory:
            subject = self._pipeline(Path(directory))
            self.addCleanup(subject.close)
            result = self._run_target(subject, "upper-third")
            self.assertEqual(result.objective_statuses, ("COMPLETE",))
            from app.semantic.requirement_decomposer import RuleBasedRequirementDecomposer
            requirement = RuleBasedRequirementDecomposer().decompose(result.objectives[0])[0]
            locations = [c for c in requirement.descriptor.constraints
                         if getattr(c, "kind", "") == "LOCATION"]
            self.assertEqual(locations[0].definition, ZONE_UPPER_THIRD)

    def test_batter_relative_upper_edge_completes_on_rebuilt_parquet(self):
        with tempfile.TemporaryDirectory() as directory:
            subject = self._pipeline(Path(directory))
            self.addCleanup(subject.close)
            # Option 2 is the exact batter-relative upper edge (needs sz_top/sz_bot).
            # The rebuilt historical archive now provides those fields.
            result = self._run_target(subject, "exact-upper", option_index=2)
            self.assertEqual(result.objective_statuses, ("COMPLETE",))
            self.assertEqual(result.response_packages[0].accepted_evidence[0].source_kind, "PARQUET")
            self.assertNotIn("synthetic", result.responses[0])
            self.assertIn("Result:", result.responses[0])

    def test_date_window_survives_restart_in_real_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subject = self._pipeline(root, today=lambda: date(2024, 1, 1))
            result = self._run_target(subject, "dated-analytics")
            self.assertEqual(result.objective_statuses, ("COMPLETE",))
            window = next(c for c in result.objectives[0].constraints if c.key == "date_range")
            subject.close()
            resumed = self._pipeline(root, today=lambda: date(2026, 6, 1))
            self.addCleanup(resumed.close)
            recovered = resumed.resume_run("dated-analytics")
            self.assertEqual(recovered.objectives[0].constraints, result.objectives[0].constraints)
            self.assertEqual(next(c for c in recovered.objectives[0].constraints
                                  if c.key == "date_range").values, window.values)

    def _requirement(self, result):
        return RuleBasedRequirementDecomposer().decompose(result.objectives[0])[0]

    def test_explicit_qualification_threshold_reaches_real_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            subject = self._pipeline(Path(directory))
            self.addCleanup(subject.close)
            result = subject.analyze("top 5 by exit velocity minimum 20 batted balls in 2023",
                                     run_id="qualification")
            self.assertEqual(result.objective_statuses, ("COMPLETE",))
            requirement = self._requirement(result)
            self.assertEqual(requirement.qualification_rule.min_batted_balls, 20)
            self.assertIn("qualification: >= 20", result.responses[0])
            self.assertNotIn("qualification: >= 3", result.responses[0])

    def test_default_population_is_regular_season_batted_balls(self):
        with tempfile.TemporaryDirectory() as directory:
            subject = self._pipeline(Path(directory))
            self.addCleanup(subject.close)
            result = subject.analyze("top 5 by exit velocity in 2023", run_id="default-population")
            self.assertEqual(result.objective_statuses, ("COMPLETE",))
            requirement = self._requirement(result)
            population = next(c for c in requirement.descriptor.constraints
                              if isinstance(c, PopulationConstraint))
            self.assertEqual(population.game_types, ("REGULAR_SEASON",))
            self.assertEqual(population.event_population, "BATTED_BALL")
            self.assertIn("Result:", result.responses[0])
            self.assertNotIn("synthetic", result.responses[0])

    def test_postseason_population_completes_on_real_parquet(self):
        with tempfile.TemporaryDirectory() as directory:
            subject = self._pipeline(Path(directory))
            self.addCleanup(subject.close)
            result = subject.analyze("top 5 by exit velocity in the postseason in 2023",
                                     run_id="postseason-population")
            self.assertEqual(result.objective_statuses, ("COMPLETE",))
            population = next(c for c in self._requirement(result).descriptor.constraints
                              if isinstance(c, PopulationConstraint))
            self.assertEqual(population.game_types, ("POSTSEASON",))

    def test_measured_contact_is_a_distinct_population_on_real_parquet(self):
        with tempfile.TemporaryDirectory() as directory:
            subject = self._pipeline(Path(directory))
            self.addCleanup(subject.close)
            result = subject.analyze("top 5 by exit velocity on all measured contact in 2023",
                                     run_id="contact-population")
            self.assertEqual(result.objective_statuses, ("COMPLETE",))
            population = next(c for c in self._requirement(result).descriptor.constraints
                              if isinstance(c, PopulationConstraint))
            self.assertEqual(population.event_population, "MEASURED_CONTACT")


@unittest.skipUnless(_POSTGRES_PRESENT, "live PostgreSQL credentials are not configured")
class RealPostgresAnalyticsSliceTests(unittest.TestCase):
    def _pipeline(self, root: Path) -> AnalysisPipeline:
        return AnalysisPipeline.default(runtime_dir=root, today=lambda: date(2026, 1, 1))

    def test_recent_regular_season_ranking_completes_on_postgres(self):
        with tempfile.TemporaryDirectory() as directory:
            subject = self._pipeline(Path(directory))
            self.addCleanup(subject.close)
            result = subject.analyze(
                "top 5 by exit velocity in the regular season in 2025", run_id="pg-analytics")
            self.assertEqual(result.objective_statuses, ("COMPLETE",))
            package = result.response_packages[0]
            self.assertTrue(package.accepted_evidence)
            self.assertEqual(package.accepted_evidence[0].source_kind, "POSTGRES")
            self.assertEqual(result.runs[0].completion_report.stop_reason, "COMPLETE")
            self.assertIn("Result:", result.responses[0])
            self.assertIn("qualification: >= 3", result.responses[0])

    def test_explicit_qualification_on_postgres(self):
        with tempfile.TemporaryDirectory() as directory:
            subject = self._pipeline(Path(directory))
            self.addCleanup(subject.close)
            result = subject.analyze(
                "top 5 by exit velocity minimum 20 batted balls in the regular season in 2025",
                run_id="pg-qualification")
            self.assertEqual(result.objective_statuses, ("COMPLETE",))
            self.assertIn("qualification: >= 20", result.responses[0])

    def test_postseason_population_on_postgres(self):
        with tempfile.TemporaryDirectory() as directory:
            subject = self._pipeline(Path(directory))
            self.addCleanup(subject.close)
            result = subject.analyze(
                "top 5 by exit velocity in the postseason in 2024", run_id="pg-postseason")
            self.assertEqual(result.objective_statuses, ("COMPLETE",))
            self.assertEqual(result.response_packages[0].accepted_evidence[0].source_kind,
                             "POSTGRES")


@unittest.skipUnless(_PARQUET_PRESENT and _POSTGRES_PRESENT,
                     "cross-source comparison needs both live sources")
class CrossSourceComparisonTests(unittest.TestCase):
    def test_2023_parquet_and_2024_postgres_are_distinct_accepted_products(self):
        with tempfile.TemporaryDirectory() as directory:
            subject = AnalysisPipeline.default(runtime_dir=Path(directory),
                                               today=lambda: date(2026, 1, 1))
            self.addCleanup(subject.close)
            result = subject.analyze(
                "top 5 by exit velocity in the regular season in 2023 vs 2024",
                run_id="cross-source")
            self.assertEqual(result.objective_statuses, ("COMPLETE", "COMPLETE"))
            kinds = [package.accepted_evidence[0].source_kind
                     for package in result.response_packages]
            self.assertEqual(kinds, ["PARQUET", "POSTGRES"])
            self.assertNotEqual(result.responses[0], result.responses[1])


if __name__ == "__main__":
    unittest.main()
