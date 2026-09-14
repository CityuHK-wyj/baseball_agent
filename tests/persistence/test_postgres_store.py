"""PostgreSQL operational store SQL contract.

No live database is used: a recording DB-API fake asserts the emitted SQL. The shared
round-trip/versioning logic is covered by the SQLite tests. Live validation is
UNVERIFIED_LIVE.
"""

import unittest

from app.models.checkpoint import Checkpoint
from app.persistence.store import OperationalStore, PostgresOperationalStore


class RecordingCursor:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, params=()):
        self.connection.statements.append((" ".join(sql.split()), tuple(params)))
        return self

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class RecordingConnection:
    def __init__(self):
        self.statements: list[tuple[str, tuple]] = []
        self.commits = 0
        self.closed = False

    def cursor(self):
        return RecordingCursor(self)

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


class PostgresStoreSqlTests(unittest.TestCase):
    def setUp(self):
        self.connection = RecordingConnection()
        self.store = PostgresOperationalStore(self.connection)

    def test_placeholder_style_is_percent_s(self):
        self.assertEqual(self.store.placeholder, "%s")

    def test_schema_is_created_through_the_injected_connection(self):
        sql = " ".join(statement for statement, _ in self.connection.statements)
        self.assertIn("CREATE TABLE IF NOT EXISTS objects", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS checkpoints", sql)

    def test_save_object_is_parameterised_and_does_not_inline_payload(self):
        payload = {"status": "SATISFIED", "note": "synthetic"}
        self.store.save_object("requirement_state", "r1", "run-1", payload)
        inserts = [item for item in self.connection.statements if item[0].startswith("INSERT INTO objects")]
        self.assertEqual(len(inserts), 1)
        sql, params = inserts[0]
        self.assertIn("%s", sql)
        self.assertNotIn("synthetic", sql, "payload must be a parameter, not inlined")
        self.assertIn("synthetic", params[-2])
        self.assertEqual(params[:3], ("requirement_state", "r1", "run-1"))

    def test_missing_object_returns_none(self):
        self.assertIsNone(self.store.get_object("artifact", "absent"))

    def test_checkpoint_insert_and_latest_query(self):
        self.store.save_checkpoint(Checkpoint(checkpoint_id="c1", run_id="run-1",
                                              recovery_position="PLAN_ACCEPTED"))
        self.assertIsNone(self.store.latest_checkpoint("run-1"))
        sql = " ".join(statement for statement, _ in self.connection.statements)
        self.assertIn("INSERT INTO checkpoints", sql)
        self.assertIn("ORDER BY created_at DESC", sql)

    def test_close_delegates_to_the_connection(self):
        self.store.close()
        self.assertTrue(self.connection.closed)

    def test_postgres_store_satisfies_the_operational_store_protocol(self):
        for method in ("save_object", "get_object", "list_objects", "save_checkpoint",
                       "latest_checkpoint", "list_checkpoints", "close"):
            with self.subTest(method=method):
                self.assertTrue(callable(getattr(self.store, method, None)))


if __name__ == "__main__":
    unittest.main()
