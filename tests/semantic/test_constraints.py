import unittest

from app.models.contracts import CategoryConstraint, NumericConstraint
from app.semantic.constraints import authority_rank, effective_constraints, normalize_constraints


class ConstraintAuthorityTests(unittest.TestCase):
    def test_authority_defaults_from_origin(self):
        explicit = CategoryConstraint(key="pitch_type", values=("FF",), origin="USER_EXPLICIT")
        inferred = CategoryConstraint(key="pitch_type", values=("FF",), origin="CONTEXT_INFERRED")
        self.assertEqual(explicit.authority, "USER_CONSTRAINT")
        self.assertEqual(inferred.authority, "INFERRED_DEFAULT")

    def test_authority_precedence_order(self):
        self.assertGreater(authority_rank("SYSTEM_POLICY"), authority_rank("USER_CONSTRAINT"))
        self.assertGreater(authority_rank("USER_CONSTRAINT"), authority_rank("USER_PREFERENCE"))
        self.assertGreater(authority_rank("USER_PREFERENCE"), authority_rank("INFERRED_DEFAULT"))

    def test_normalize_keeps_highest_authority_per_key(self):
        low = NumericConstraint(key="exit_velocity", operator="GT", value=90, unit="mph",
                                origin="CONTEXT_INFERRED")
        high = NumericConstraint(key="exit_velocity", operator="GT", value=95, unit="mph",
                                 origin="USER_EXPLICIT")
        normalized = normalize_constraints((low, high))
        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0].value, 95)

    def test_equal_authority_conflicts_are_preserved(self):
        lower = NumericConstraint(key="exit_velocity", operator="GT", value=95, unit="mph")
        upper = NumericConstraint(key="exit_velocity", operator="LT", value=110, unit="mph")
        self.assertEqual(len(normalize_constraints((lower, upper))), 2)

    def test_effective_constraints_merges_groups_with_precedence(self):
        policy = CategoryConstraint(key="season_scope", values=("REGULAR",), origin="SYSTEM_DEFAULT",
                                    authority="SYSTEM_POLICY")
        user = CategoryConstraint(key="season_scope", values=("POSTSEASON",), origin="USER_EXPLICIT")
        merged = effective_constraints((user,), (policy,))
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].authority, "SYSTEM_POLICY")


if __name__ == "__main__":
    unittest.main()
