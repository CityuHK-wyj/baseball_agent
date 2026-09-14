import unittest
from datetime import date

from app.assessment.adequacy import (league_coverage_limitation, qualification_signal,
                                     sample_adequacy_signal)
from app.assessment.validator import validate_artifact
from app.models.contracts import LeagueStateSnapshot, QualificationRule, SampleAdequacyRule
from tests.factories import artifact, requirement


class SampleAdequacyTests(unittest.TestCase):
    def test_below_floor_produces_a_severity_scaled_signal(self):
        rule = SampleAdequacyRule(min_sample=100, sample_unit="BATTED_BALL")
        self.assertIsNone(sample_adequacy_signal(rule, 150))
        self.assertEqual(sample_adequacy_signal(rule, 10).severity, "MAJOR")
        self.assertEqual(sample_adequacy_signal(rule, 80).severity, "MODERATE")

    def test_validator_uses_sample_adequacy_rule(self):
        need = requirement(sample_adequacy_rule=SampleAdequacyRule(min_sample=100))
        result = validate_artifact(artifact(row_count=20), need)
        self.assertTrue(result.passed)
        codes = [signal.code for signal in result.soft_signals]
        self.assertIn("LOW_SAMPLE", codes)

    def test_qualification_depends_on_league_progress(self):
        rule = QualificationRule(kind="MLB_QUALIFIED", min_plate_appearances=100)
        behind = LeagueStateSnapshot(season=2025, as_of=date(2025, 9, 1), games_played=140,
                                     games_scheduled=162, local_coverage_end=date(2025, 7, 1))
        current = LeagueStateSnapshot(season=2025, as_of=date(2025, 9, 1), games_played=140,
                                      games_scheduled=162, local_coverage_end=date(2025, 9, 1))
        self.assertIsNotNone(qualification_signal(rule, behind))
        self.assertIsNone(qualification_signal(rule, current))

    def test_league_snapshot_derived_values(self):
        snapshot = LeagueStateSnapshot(season=2025, as_of=date(2025, 9, 1), games_played=81,
                                       games_scheduled=162, local_coverage_end=date(2025, 8, 20))
        self.assertAlmostEqual(snapshot.official_progress, 0.5)
        self.assertEqual(snapshot.local_lag_days, 12)
        self.assertTrue(snapshot.local_behind_official)
        self.assertIsNotNone(league_coverage_limitation(snapshot))

    def test_invalid_snapshot_is_rejected(self):
        with self.assertRaises(Exception):
            LeagueStateSnapshot(season=2025, as_of=date(2025, 9, 1), games_played=200,
                                games_scheduled=162)


if __name__ == "__main__":
    unittest.main()
