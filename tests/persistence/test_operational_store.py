import tempfile
import unittest
from pathlib import Path

from app.models.checkpoint import Checkpoint
from app.persistence.store import SqliteOperationalStore


def checkpoint(checkpoint_id: str, run_id: str = "run-1", position: str = "PLAN_ACCEPTED") -> Checkpoint:
    return Checkpoint(checkpoint_id=checkpoint_id, run_id=run_id, recovery_position=position)


class OperationalStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = SqliteOperationalStore(":memory:")

    def tearDown(self):
        self.store.close()

    def test_object_round_trip_and_versioning(self):
        self.store.save_object("requirement_state", "r1", "run-1", {"status": "PENDING"})
        stored = self.store.get_object("requirement_state", "r1")
        self.assertEqual(stored.payload, {"status": "PENDING"})
        self.assertEqual(stored.version, 0)

        self.store.save_object("requirement_state", "r1", "run-1", {"status": "PENDING"})
        self.assertEqual(self.store.get_object("requirement_state", "r1").version, 0, "idempotent save")

        self.store.save_object("requirement_state", "r1", "run-1", {"status": "SATISFIED"})
        updated = self.store.get_object("requirement_state", "r1")
        self.assertEqual(updated.payload, {"status": "SATISFIED"})
        self.assertEqual(updated.version, 1)

    def test_get_missing_object_returns_none(self):
        self.assertIsNone(self.store.get_object("absent", "x"))

    def test_list_objects_filters_by_kind_and_run(self):
        self.store.save_object("artifact", "a1", "run-1", {"x": 1})
        self.store.save_object("artifact", "a2", "run-2", {"x": 2})
        self.store.save_object("assessment", "s1", "run-1", {"x": 3})
        self.assertEqual([item.object_id for item in self.store.list_objects("artifact", "run-1")], ["a1"])
        self.assertEqual(len(self.store.list_objects("artifact")), 2)
        self.assertEqual(len(self.store.list_objects("assessment", "run-1")), 1)

    def test_checkpoints_are_append_only_and_latest_wins(self):
        self.store.save_checkpoint(checkpoint("c1", position="RUN_STARTED"))
        self.store.save_checkpoint(checkpoint("c2", position="PLAN_ACCEPTED"))
        self.assertEqual(self.store.latest_checkpoint("run-1").checkpoint_id, "c2")
        self.assertEqual(len(self.store.list_checkpoints("run-1")), 2)
        self.assertIsNone(self.store.latest_checkpoint("run-unknown"))

    def test_file_backed_store_reopens_with_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "operational.db"
            first = SqliteOperationalStore(path)
            first.save_object("artifact", "a1", "run-1", {"x": 1})
            first.save_checkpoint(checkpoint("c1"))
            first.close()

            second = SqliteOperationalStore(path)
            self.assertEqual(second.get_object("artifact", "a1").payload, {"x": 1})
            self.assertEqual(second.latest_checkpoint("run-1").checkpoint_id, "c1")
            second.close()


if __name__ == "__main__":
    unittest.main()
