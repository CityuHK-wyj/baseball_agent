"""Containment contracts while the verified executors are being implemented."""

import importlib
import unittest
from unittest.mock import patch


class SafetyTests(unittest.TestCase):
    def test_legacy_analytics_writers_are_blocked_before_io(self):
        calls = [
            ("app.data", "ensure_hot_schema", (None,)),
            ("app.data", "sync_player_dictionary", (None, [1])),
            ("app.data", "fetch_and_append_mlb_data", ("2025-01-01", "2025-01-02")),
            ("app.data", "archive_mlb_history", ()),
            ("app.features.engine", "build_batting_snapshot", ("2025-01-01", "2025-01-02")),
            ("data_loader.fetch_and_load", "fetch_and_append_mlb_data", ("2025-01-01", "2025-01-02")),
            ("data_loader.fetch_and_load", "sync_player_dictionary", (None, [1])),
            ("data_loader.fetch_batting_stats", "build_batting_snapshot", ("2025-01-01", "2025-01-02")),
            ("data_loader.archive_history_to_parquet", "archive_mlb_history", ()),
        ]
        for module, name, args in calls:
            with self.subTest(module=module, name=name):
                function = getattr(importlib.import_module(module), name)
                with self.assertRaisesRegex(PermissionError, "read-only"):
                    function(*args)

    def test_legacy_llm_loop_is_disabled_before_any_paid_call(self):
        from app.agent.orchestrator import run_all_channel_baseball_agent
        with self.assertRaisesRegex(RuntimeError, "not yet"):
            run_all_channel_baseball_agent("synthetic request")

    def test_unvalidated_sql_cannot_reach_a_database(self):
        import json
        from app.tools.postgres import query_local_hot_db
        from app.tools.duckdb import query_local_cold_parquet
        for query, connector in [(query_local_hot_db, "psycopg2.connect"),
                                 (query_local_cold_parquet, "duckdb.connect")]:
            with patch(connector, side_effect=AssertionError("Unexpected database connection")) as connect:
                result = json.loads(query("DROP TABLE statcast_pitches"))
                self.assertEqual(result["error"], "BLOCKED_BY_POLICY")
                connect.assert_not_called()

    def test_audit_and_plot_helpers_cannot_bypass_containment(self):
        from app.data import audit_parquet_data
        from data_loader.audit_parquet_data import audit_my_data
        from plot_heatmap import generate_pitch_heatmap
        for function, args in [(audit_parquet_data, ()), (audit_my_data, ()),
                               (generate_pitch_heatmap, ("Synthetic Player",))]:
            with patch("duckdb.connect", side_effect=AssertionError("Unexpected connection")), \
                 patch("psycopg2.connect", side_effect=AssertionError("Unexpected connection")):
                with self.assertRaisesRegex(PermissionError, "read-only"):
                    function(*args)
