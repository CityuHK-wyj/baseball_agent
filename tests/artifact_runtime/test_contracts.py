"""Information-transfer contracts: references, scope comparison, artifacts, sufficiency."""

import unittest
from datetime import date

from app.artifact_runtime.artifacts import ArtifactStore
from app.artifact_runtime.references import ReferenceStore, UnknownReference
from app.artifact_runtime.scope import compare_scope
from app.artifact_runtime.sufficiency import CoverageJudge
from app.models.artifact_runtime import (ArtifactExport, EvidenceSource, Goal, Need,
                                         RuntimeArtifact, Scope, ScopeVerification)
from app.models.contracts import TimeRange


def _artifact(artifact_id="a1", *, actual_scope=None, status="OK", confidence=0.8,
              lineage=(), exports=(), text="evidence text"):
    return RuntimeArtifact(
        artifact_id=artifact_id, kind="test", actual_scope=actual_scope, status=status,
        confidence=confidence, lineage=lineage, exports=tuple(exports), text_content=text)


class ReferenceStoreTests(unittest.TestCase):
    def test_add_and_get_round_trip(self):
        store = ReferenceStore()
        ref = store.add("USER_MESSAGE", "conv:1", label="hello")
        self.assertEqual(store.get(ref.ref_id).target_id, "conv:1")
        self.assertEqual(store.of_type("USER_MESSAGE"), (ref,))

    def test_unknown_reference_raises(self):
        with self.assertRaises(UnknownReference):
            ReferenceStore().get("nope")


class ArtifactStoreTests(unittest.TestCase):
    def test_lineage_is_traced_through_derived_artifacts(self):
        store = ArtifactStore()
        base = _artifact("base")
        mid = _artifact("mid", lineage=("base",))
        top = _artifact("top", lineage=("mid",))
        for item in (base, mid, top):
            store.add(item)
        self.assertEqual(store.lineage("top"), ("top", "mid", "base"))

    def test_export_resolution(self):
        store = ArtifactStore()
        export = ArtifactExport(export_id="a1:PLAYER_ID_SET", export_type="PLAYER_ID_SET",
                                value=[1, 2])
        store.add(_artifact("a1", exports=(export,)))
        self.assertEqual(store.resolve_export("a1:PLAYER_ID_SET").value, [1, 2])
        self.assertIsNone(store.resolve_export("missing"))


class ScopeComparisonTests(unittest.TestCase):
    def test_matching_scope_has_no_gaps(self):
        window = TimeRange(start=date(2025, 4, 1), end=date(2025, 9, 30))
        requested = Scope(entities=("Yankees",), population="players", time_range=window,
                          metric="OPS")
        actual = Scope(entities=("Yankees",), population="players", time_range=window,
                       metric="OPS")
        comparison = compare_scope(requested, actual)
        self.assertFalse(comparison.blocking)
        self.assertEqual(comparison.temporal, 1.0)

    def test_temporal_mismatch_is_a_coverage_gap(self):
        requested = Scope(time_range=TimeRange(start=date(2025, 4, 1), end=date(2025, 9, 30)))
        actual = Scope(time_range=TimeRange(start=date(2024, 4, 1), end=date(2024, 9, 30)))
        comparison = compare_scope(requested, actual)
        self.assertTrue(comparison.blocking)
        self.assertIn("is not covered by evidence window", " ".join(comparison.gaps))

    def test_broader_evidence_window_does_not_satisfy_a_narrow_request(self):
        # A season aggregate is not evidence for a specific month, even though it
        # contains it.
        requested = Scope(time_range=TimeRange(start=date(2025, 8, 1), end=date(2025, 8, 31)))
        actual = Scope(time_range=TimeRange(start=date(2025, 1, 1), end=date(2025, 12, 31)))
        comparison = compare_scope(requested, actual)
        self.assertTrue(comparison.blocking)
        self.assertIn("broader window", " ".join(comparison.gaps))

    def test_narrower_evidence_window_is_partial(self):
        requested = Scope(time_range=TimeRange(start=date(2025, 1, 1), end=date(2025, 12, 31)))
        actual = Scope(time_range=TimeRange(start=date(2025, 6, 1), end=date(2025, 8, 31)))
        comparison = compare_scope(requested, actual)
        self.assertTrue(comparison.blocking)
        self.assertLess(comparison.temporal, 1.0)

    def test_requested_season_cannot_be_satisfied_by_another_season(self):
        requested = Scope(seasons=(2025,))
        actual = Scope(seasons=(2024,))
        self.assertTrue(compare_scope(requested, actual).blocking)

    def test_missing_population_and_measure_are_gaps(self):
        requested = Scope(population="players", metric="OPS")
        actual = Scope()
        comparison = compare_scope(requested, actual)
        self.assertEqual(comparison.population, 0.0)
        self.assertEqual(comparison.measure, 0.0)
        self.assertTrue(comparison.blocking)

    def test_flexible_population_naming_matches_by_meaning(self):
        # A roster/player set named in different words still matches.
        comparison = compare_scope(Scope(population="MLB team roster"),
                                   Scope(population="players"))
        self.assertFalse(comparison.blocking)
        comparison = compare_scope(Scope(population="MLB batters"),
                                   Scope(population="players"))
        self.assertFalse(comparison.blocking)

    def test_different_population_family_is_a_gap(self):
        comparison = compare_scope(Scope(population="pitchers"),
                                   Scope(population="players"))
        self.assertTrue(comparison.blocking)

    def test_flexible_measure_naming_matches_by_family(self):
        self.assertFalse(compare_scope(Scope(metric="average_exit_velocity"),
                                       Scope(metric="launch_speed")).blocking)
        self.assertFalse(compare_scope(Scope(metric="pitch_velocity"),
                                       Scope(metric="release_speed")).blocking)
        self.assertTrue(compare_scope(Scope(metric="ERA"),
                                      Scope(metric="launch_speed")).blocking)

    def test_unscoped_without_artifact_declares_no_scope(self):
        comparison = compare_scope(Scope(metric="OPS"), None)
        self.assertTrue(comparison.blocking)


class SufficiencyTests(unittest.TestCase):
    def _goal(self):
        return Goal(goal_id="g1", statement="2025 Yankees OPS",
                    scope=Scope(entities=("Yankees",), seasons=(2025,), metric="OPS"))

    def _need(self, scope):
        return Need(need_id="n1", objective="ops", required_scope=scope,
                    linked_artifacts=("a1",))

    def test_irrelevant_artifact_cannot_complete_the_goal(self):
        need = self._need(Scope(entities=("Yankees",), seasons=(2025,), metric="OPS"))
        artifact = _artifact("a1", actual_scope=Scope(entities=("Mets",), seasons=(2024,),
                                                      metric="ERA"))
        assessment = CoverageJudge().assess_need(self._goal(), need, (artifact,))
        self.assertNotEqual(assessment.verdict, "SATISFIED")
        self.assertFalse(assessment.core_goal_supported)

    def test_wrong_scope_artifact_is_not_complete(self):
        need = self._need(Scope(seasons=(2025,)))
        artifact = _artifact("a1", actual_scope=Scope(seasons=(2024,)))
        coverage = CoverageJudge().summarize(
            self._goal(), (need,), (CoverageJudge().assess_need(self._goal(), need,
                                                                (artifact,)),))
        self.assertFalse(coverage.core_goal_supported)

    def test_matching_artifact_completes_the_core_goal(self):
        need = self._need(Scope(entities=("Yankees",), seasons=(2025,), metric="OPS"))
        # Verified scope must come from independent evidence, not a declared scope alone.
        artifact = _artifact("a1", actual_scope=need.required_scope).model_copy(update={
            "scope_verifications": (
                ScopeVerification(dimension="entity", status="VERIFIED",
                                  verifier="test", evidence_refs=("a1",)),
                ScopeVerification(dimension="season", status="VERIFIED",
                                  verifier="test", evidence_refs=("a1",)),
                ScopeVerification(dimension="measure", status="VERIFIED",
                                  verifier="test", evidence_refs=("a1",)),
            )})
        judge = CoverageJudge()
        assessment = judge.assess_need(self._goal(), need, (artifact,))
        coverage = judge.summarize(self._goal(), (need,), (assessment,))
        self.assertEqual(assessment.verdict, "SATISFIED")
        self.assertTrue(coverage.core_goal_supported)

    def test_declared_scope_alone_cannot_complete_a_core_goal(self):
        # A Tool echoing the requested scope is not independent evidence.
        need = self._need(Scope(entities=("Yankees",), seasons=(2025,), metric="OPS"))
        artifact = _artifact("a1", actual_scope=need.required_scope)
        assessment = CoverageJudge().assess_need(self._goal(), need, (artifact,))
        self.assertNotEqual(assessment.verdict, "SATISFIED")

    def test_a_previously_produced_artifact_cannot_satisfy_a_new_need(self):
        need = self._need(Scope(seasons=(2025,)))
        need = need.model_copy(update={"linked_artifacts": ()})
        artifact = _artifact("old", actual_scope=Scope(seasons=(2025,)))
        assessment = CoverageJudge().assess_need(self._goal(), need, (artifact,))
        self.assertNotEqual(assessment.verdict, "SATISFIED")


if __name__ == "__main__":
    unittest.main()
