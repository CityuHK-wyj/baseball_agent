import unittest
from pathlib import Path

from app.validation.sql_guard import guard_read_only_sql, resolve_within_root


class ReadOnlySqlGuardTests(unittest.TestCase):
    def test_plain_and_cte_selects_are_allowed(self):
        for sql in ("SELECT a FROM t",
                    "/* comment */ WITH x AS (SELECT 1 AS a) SELECT a FROM x",
                    "SELECT a FROM t UNION SELECT a FROM t2"):
            with self.subTest(sql=sql):
                self.assertTrue(guard_read_only_sql(sql).allowed)

    def test_mutations_and_administration_are_rejected(self):
        statements = [
            "DROP TABLE t", "INSERT INTO t VALUES (1)", "UPDATE t SET a=1",
            "DELETE FROM t", "CREATE TABLE t (a int)", "ALTER TABLE t ADD b int",
            "TRUNCATE TABLE t", "COPY (SELECT 1) TO '/tmp/x.csv'",
            "ATTACH 'x.db' AS y", "DETACH y", "INSTALL httpfs", "PRAGMA database_list",
            "GRANT ALL ON t TO x", "SELECT * FROM t INTO OUTFILE '/tmp/x'",
        ]
        for sql in statements:
            with self.subTest(sql=sql):
                result = guard_read_only_sql(sql, dialect="duckdb")
                self.assertFalse(result.allowed, sql)

    def test_multiple_statements_and_empty_input_are_rejected(self):
        self.assertFalse(guard_read_only_sql("SELECT 1; DROP TABLE t").allowed)
        self.assertFalse(guard_read_only_sql("   ").allowed)
        self.assertFalse(guard_read_only_sql("SELECT FROM WHERE").allowed)

    def test_forbidden_functions_are_rejected(self):
        for sql in ("SELECT pg_read_file('/etc/passwd')",
                    "SELECT dblink('host=evil', 'select 1')"):
            with self.subTest(sql=sql):
                self.assertFalse(guard_read_only_sql(sql).allowed)

    def test_table_allowlist_is_enforced(self):
        allowed = guard_read_only_sql("SELECT * FROM statcast_pitches",
                                      allowed_tables=("statcast_pitches", "player_dictionary"))
        self.assertTrue(allowed.allowed)
        self.assertEqual(allowed.tables, ("statcast_pitches",))
        disallowed = guard_read_only_sql("SELECT * FROM secret_table",
                                         allowed_tables=("statcast_pitches",))
        self.assertFalse(disallowed.allowed)

    def test_file_reading_requires_and_respects_an_explicit_root(self):
        root = "/data/parquet"
        allowed = guard_read_only_sql(
            "SELECT * FROM read_parquet('/data/parquet/mlb_2022.parquet')", dialect="duckdb",
            file_root=root)
        self.assertTrue(allowed.allowed)
        self.assertEqual(allowed.file_paths, ("/data/parquet/mlb_2022.parquet",))
        for sql in ("SELECT * FROM read_parquet('/etc/passwd')",
                    "SELECT * FROM read_parquet('/data/parquet/../../etc/passwd')"):
            with self.subTest(sql=sql):
                self.assertFalse(guard_read_only_sql(sql, dialect="duckdb", file_root=root).allowed)
        self.assertFalse(guard_read_only_sql(
            "SELECT * FROM read_parquet('/data/parquet/x.parquet')", dialect="duckdb").allowed)

    def test_glob_within_root_is_allowed_and_cte_alias_is_not_a_table(self):
        result = guard_read_only_sql(
            "WITH recent AS (SELECT * FROM read_parquet('/data/parquet/mlb_*.parquet')) SELECT * FROM recent",
            dialect="duckdb", file_root="/data/parquet", allowed_tables=())
        self.assertTrue(result.allowed, result.reason)
        self.assertEqual(result.tables, ())

    def test_resolve_within_root_handles_relative_and_escaping_paths(self):
        self.assertTrue(resolve_within_root("sub/x.parquet", "/data/parquet"))
        self.assertFalse(resolve_within_root("/data/parquet/../secret", "/data/parquet"))
        self.assertFalse(resolve_within_root("/etc/passwd", "/data/parquet"))


if __name__ == "__main__":
    unittest.main()
