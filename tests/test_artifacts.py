import unittest

from pydantic import ValidationError

from app.agent.registry import ArtifactRegistry
from app.assessment.judge import RuleBasedJudge
from app.assessment.service import AssessmentService
from app.assessment.validator import validate_artifact
from app.models.artifacts import ArtifactAssessment, DeterministicResult, HardFailure, JudgeResult
from tests.factories import artifact, requirement


class DeterministicValidationTests(unittest.TestCase):
    def test_aligned_artifact_has_no_failures_or_signals(self):
        result = validate_artifact(artifact(), requirement())
        self.assertTrue(result.passed)
        self.assertEqual(result.soft_signals, ())

    def test_integrity_failure_is_hard_and_stops_comparison(self):
        result = validate_artifact(artifact(integrity="CORRUPTED"), requirement())
        self.assertFalse(result.passed)
        self.assertEqual([failure.code for failure in result.hard_failures], ["INTEGRITY_FAILURE"])

    def test_type_entity_key_and_constraint_mismatches_are_hard(self):
        cases = {
            "TYPE_MISMATCH": artifact(descriptor=artifact().descriptor.model_copy(update={"artifact_type": "EVIDENCE"})),
            "ENTITY_MISMATCH": artifact(descriptor=artifact().descriptor.model_copy(update={"entities": ()})),
            "MISSING_DATA_KEY": artifact(descriptor=artifact().descriptor.model_copy(update={"data_keys": ("launch_angle",)})),
        }
        for code, broken in cases.items():
            with self.subTest(code=code):
                result = validate_artifact(broken, requirement())
                self.assertIn(code, [failure.code for failure in result.hard_failures])

    def test_time_coverage_partial_is_soft_and_non_overlap_is_hard(self):
        from tests.factories import time_range
        need = requirement(descriptor=requirement().descriptor.model_copy(update={"time_range": time_range()}))
        partial = validate_artifact(artifact(observed_time_range=time_range("2024-04-01", "2024-05-01")), need)
        self.assertTrue(partial.passed)
        self.assertEqual(partial.soft_signals[0].code, "PARTIAL_TIME_COVERAGE")
        disjoint = validate_artifact(artifact(observed_time_range=time_range("2020-04-01", "2020-09-30")), need)
        self.assertFalse(disjoint.passed)
        self.assertIn("TIME_RANGE_MISMATCH", [failure.code for failure in disjoint.hard_failures])

    def test_zero_rows_and_low_sample_are_soft(self):
        zero = validate_artifact(artifact(row_count=0), requirement())
        self.assertTrue(zero.passed)
        self.assertEqual(zero.soft_signals[0].code, "ZERO_ROWS")
        low = validate_artifact(artifact(row_count=5), requirement(min_row_count=100))
        self.assertEqual(low.soft_signals[0].code, "LOW_SAMPLE")
        self.assertEqual(low.soft_signals[0].severity, "MAJOR")

    def test_missing_optional_key_is_a_minor_soft_signal(self):
        need = requirement(descriptor=requirement().descriptor.model_copy(
            update={"optional_data_keys": ("launch_angle",)}))
        result = validate_artifact(artifact(), need)
        self.assertTrue(result.passed)
        self.assertEqual(result.soft_signals[0].code, "OPTIONAL_KEY_MISSING")

    def test_category_constraints_are_matched_by_identity(self):
        from app.models.contracts import CategoryConstraint
        constraint = CategoryConstraint(key="pitch_type", values=("FF", "SL"))
        need = requirement(descriptor=requirement().descriptor.model_copy(update={"constraints": (constraint,)}))
        missing = validate_artifact(artifact(), need)
        self.assertIn("CONSTRAINT_MISMATCH", [failure.code for failure in missing.hard_failures])
        applied = artifact(descriptor=artifact().descriptor.model_copy(update={"constraints": (constraint,)}))
        self.assertTrue(validate_artifact(applied, need).passed)


class ArtifactAssessmentTests(unittest.TestCase):
    def test_hard_failure_forces_reject_even_with_a_strong_judge(self):
        with self.assertRaises(ValidationError):
            ArtifactAssessment(
                assessment_id="a1", artifact_ref="x", requirement_ref="r1",
                deterministic_result=DeterministicResult(hard_failures=(HardFailure(code="TYPE_MISMATCH", detail="d"),)),
                judge_result=JudgeResult(level="STRONG", rationale="judge disagrees"),
                final_level="STRONG", assessment_summary="contradiction")

    def test_same_artifact_is_acceptable_for_existence_and_weak_for_inference(self):
        registry = ArtifactRegistry()
        registry.register(artifact(row_count=1))
        counter = iter(f"assessment-{index}" for index in range(10))
        service = AssessmentService(registry, RuleBasedJudge(), id_factory=lambda _prefix: next(counter))
        existence = requirement(requirement_id="r-exist", evidence_purpose="EXISTENCE", min_row_count=100)
        inferential = requirement(requirement_id="r-infer", evidence_purpose="INFERENTIAL", min_row_count=100)
        existence_assessment = service.assess("a1", existence)
        inferential_assessment = service.assess("a1", inferential)
        self.assertEqual(existence_assessment.final_level, "ACCEPTABLE")
        self.assertEqual(inferential_assessment.final_level, "WEAK")
        self.assertTrue(existence_assessment.accepted)
        self.assertFalse(inferential_assessment.accepted)
        self.assertEqual(existence_assessment.deterministic_result, inferential_assessment.deterministic_result)

    def test_zero_rows_is_rejected_regardless_of_purpose(self):
        registry = ArtifactRegistry()
        registry.register(artifact(row_count=0))
        counter = iter(f"assessment-{index}" for index in range(10))
        service = AssessmentService(registry, RuleBasedJudge(), id_factory=lambda _p: next(counter))
        for purpose in ("EXISTENCE", "DESCRIPTIVE", "INFERENTIAL"):
            with self.subTest(purpose=purpose):
                result = service.assess("a1", requirement(requirement_id=f"r-{purpose}", evidence_purpose=purpose))
                self.assertEqual(result.final_level, "REJECT")


class ArtifactRegistryTests(unittest.TestCase):
    def test_identity_is_immutable_and_lineage_must_exist(self):
        registry = ArtifactRegistry()
        registry.register(artifact("a1"))
        self.assertEqual(registry.register(artifact("a1")), registry.get("a1"))
        with self.assertRaises(ValueError):
            registry.register(artifact("a1", row_count=7))
        with self.assertRaises(ValueError):
            registry.register(artifact("a2", lineage=("missing",)))
        derived = artifact("a2", lineage=("a1",))
        self.assertEqual(registry.register(derived).lineage, ("a1",))
        self.assertEqual(len(registry), 2)


if __name__ == "__main__":
    unittest.main()
