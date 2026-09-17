"""Safe Analytical IR: validation, derived calculations, and the SQL boundary."""

import unittest
from datetime import date

from app.artifact_runtime.analytical_ir import (Aggregate, AggregateRef, AnalyticalQuery,
                                                Between, BinaryOperand, Compare,
                                                EntitySetFilter, PeriodSelector, Selection)
from app.artifact_runtime.ir_compiler import compile_analytical_query
from app.artifact_runtime.schema_catalog import catalog_from_registry
from app.models.artifact_runtime import ArtifactExport
from app.models.contracts import TimeRange
from app.validation.sql_guard import guard_read_only_sql


def _count_if_hard_hit_query(**changes) -> AnalyticalQuery:
    base = dict(
        query_id="q", source_kind="POSTGRES", table="statcast_pitches",
        selections=(
            Selection(alias="batter", kind="GROUP_KEY", field="batter_id"),
            Selection(alias="hard_hit_rate", kind="DERIVED", expression=BinaryOperand(
                op="PCT", left=AggregateRef(alias="hard_hit"),
                right=AggregateRef(alias="measured"))),
            Selection(alias="hard_hit", kind="AGGREGATE", aggregate=Aggregate(
                op="COUNT_IF", alias="hard_hit",
                condition=Compare(field="launch_speed", operator="GTE", value=95.0))),
            Selection(alias="measured", kind="AGGREGATE", aggregate=Aggregate(
                op="COUNT_NON_NULL", field="launch_speed", alias="measured")),
        ),
        order_by="hard_hit_rate", direction="DESC", limit=10, min_rows=20)
    base.update(changes)
    return AnalyticalQuery(**base)


class DerivedCalculationTests(unittest.TestCase):
    def setUp(self):
        self.catalog = catalog_from_registry()

    def test_derived_hard_hit_rate_is_built_from_trusted_fields(self):
        result = compile_analytical_query(_count_if_hard_hit_query(), self.catalog)
        self.assertTrue(result.ok, result.detail)
        self.assertIn("SUM(CASE WHEN launch_speed >= 95.0 THEN 1 ELSE 0 END)", result.sql)
        self.assertIn("COUNT(launch_speed)", result.sql)
        self.assertIn("NULLIF", result.sql)

    def test_compiled_sql_passes_the_read_only_guard(self):
        result = compile_analytical_query(_count_if_hard_hit_query(), self.catalog)
        guard = guard_read_only_sql(result.sql, dialect="postgres",
                                    allowed_tables=("statcast_pitches",))
        self.assertTrue(guard.allowed, guard.reason)

    def test_period_comparison_uses_conditional_aggregation(self):
        query = _count_if_hard_hit_query(
            selections=(
                Selection(alias="batter", kind="GROUP_KEY", field="batter_id"),
                Selection(alias="ev_a", kind="AGGREGATE", aggregate=Aggregate(
                    op="AVG", field="launch_speed", alias="ev_a", period="a")),
                Selection(alias="ev_b", kind="AGGREGATE", aggregate=Aggregate(
                    op="AVG", field="launch_speed", alias="ev_b", period="b")),
                Selection(alias="diff", kind="DERIVED", expression=BinaryOperand(
                    op="DIFF", left=AggregateRef(alias="ev_a"),
                    right=AggregateRef(alias="ev_b"))),
            ),
            date_field="game_date",
            periods=(
                PeriodSelector(label="a", time_range=TimeRange(start=date(2025, 4, 1), end=date(2025, 6, 30))),
                PeriodSelector(label="b", time_range=TimeRange(start=date(2025, 7, 1), end=date(2025, 9, 30))),
            ), order_by="diff")
        result = compile_analytical_query(query, self.catalog)
        self.assertTrue(result.ok, result.detail)
        self.assertIn("CASE WHEN game_date >= DATE '2025-04-01'", result.sql)
        self.assertIn("CASE WHEN game_date >= DATE '2025-07-01'", result.sql)


class IRValidationTests(unittest.TestCase):
    def setUp(self):
        self.catalog = catalog_from_registry()

    def test_unknown_field_is_rejected(self):
        query = _count_if_hard_hit_query(
            filters=(Compare(field="drop_table_now", operator="EQ", value="x"),))
        result = compile_analytical_query(query, self.catalog)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "UNKNOWN_FIELD")

    def test_arbitrary_identifier_cannot_be_smuggled(self):
        query = _count_if_hard_hit_query(
            selections=(Selection(alias="x", kind="GROUP_KEY",
                                  field="batter_id) FROM statcast_pitches; DROP TABLE x --"),
                        Selection(alias="m", kind="AGGREGATE", aggregate=Aggregate(
                            op="COUNT_NON_NULL", field="launch_speed", alias="m"))))
        result = compile_analytical_query(query, self.catalog)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "UNKNOWN_FIELD")

    def test_unknown_table_is_rejected(self):
        query = _count_if_hard_hit_query(table="secrets")
        result = compile_analytical_query(query, self.catalog)
        self.assertEqual(result.code, "UNKNOWN_TABLE")

    def test_unsupported_operation_is_rejected(self):
        query = _count_if_hard_hit_query(
            selections=(Selection(alias="g", kind="GROUP_KEY", field="batter_id"),
                        Selection(alias="bad", kind="AGGREGATE", aggregate=Aggregate(
                            op="AVG", field="pitch_type", alias="bad"))))
        result = compile_analytical_query(query, self.catalog)
        self.assertEqual(result.code, "UNSUPPORTED_OPERATION")

    def test_limit_out_of_range_is_rejected(self):
        result = compile_analytical_query(_count_if_hard_hit_query(limit=100000),
                                          self.catalog)
        self.assertEqual(result.code, "INVALID_LIMIT")

    def test_window_outside_source_coverage_is_rejected(self):
        query = _count_if_hard_hit_query(
            window=TimeRange(start=date(1990, 1, 1), end=date(1990, 12, 31)))
        result = compile_analytical_query(query, self.catalog)
        self.assertEqual(result.code, "INSUFFICIENT_SOURCE_COVERAGE")

    def test_missing_entity_set_export_is_rejected(self):
        query = _count_if_hard_hit_query(
            entity_set=EntitySetFilter(field="batter_id", export_ref="ref-missing"))
        result = compile_analytical_query(query, self.catalog, resolve_export=lambda ref: None)
        self.assertEqual(result.code, "MISSING_ENTITY_SET")

    def test_entity_set_is_compiled_to_canonical_ids(self):
        query = _count_if_hard_hit_query(
            entity_set=EntitySetFilter(field="batter_id", export_ref="ref-roster"))
        export = ArtifactExport(export_id="e1", export_type="PLAYER_ID_SET",
                                value=[660271, 123456])
        result = compile_analytical_query(query, self.catalog,
                                          resolve_export=lambda ref: export)
        self.assertTrue(result.ok, result.detail)
        self.assertIn("batter_id IN (660271, 123456)", result.sql)

    def test_non_numeric_entity_values_are_not_inlined(self):
        query = _count_if_hard_hit_query(
            entity_set=EntitySetFilter(field="batter_id", export_ref="ref-roster"))
        export = ArtifactExport(export_id="e1", export_type="PLAYER_ID_SET",
                                value=["New York Yankees"])
        result = compile_analytical_query(query, self.catalog,
                                          resolve_export=lambda ref: export)
        self.assertEqual(result.code, "MISSING_ENTITY_SET")


class IRDeterminismPropertyTests(unittest.TestCase):
    """Property-style: vary ids, windows and thresholds; output stays valid."""

    def setUp(self):
        self.catalog = catalog_from_registry()

    def test_parameter_variations_produce_valid_read_only_sql(self):
        cases = [
            (95.0, 10, date(2025, 4, 1), date(2025, 9, 30)),
            (90.5, 25, date(2024, 3, 15), date(2024, 10, 1)),
            (100.0, 1, date(2025, 5, 5), date(2025, 5, 6)),
        ]
        for threshold, limit, start, end in cases:
            with self.subTest(threshold=threshold, limit=limit):
                query = _count_if_hard_hit_query(
                    limit=limit, order_by="rate",
                    filters=(Between(field="game_date", low=start.isoformat(),
                                     high=end.isoformat()),),
                    selections=(
                        Selection(alias="batter", kind="GROUP_KEY", field="batter_id"),
                        Selection(alias="rate", kind="DERIVED", expression=BinaryOperand(
                            op="PCT", left=AggregateRef(alias="hh"),
                            right=AggregateRef(alias="m"))),
                        Selection(alias="hh", kind="AGGREGATE", aggregate=Aggregate(
                            op="COUNT_IF", alias="hh", condition=Compare(
                                field="launch_speed", operator="GTE", value=threshold))),
                        Selection(alias="m", kind="AGGREGATE", aggregate=Aggregate(
                            op="COUNT_NON_NULL", field="launch_speed", alias="m"))))
                result = compile_analytical_query(query, self.catalog)
                self.assertTrue(result.ok, result.detail)
                guard = guard_read_only_sql(result.sql, dialect="postgres",
                                            allowed_tables=("statcast_pitches",))
                self.assertTrue(guard.allowed, guard.reason)


if __name__ == "__main__":
    unittest.main()
