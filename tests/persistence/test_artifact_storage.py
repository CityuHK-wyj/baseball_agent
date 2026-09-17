import hashlib
import tempfile
import unittest
from pathlib import Path

from app.persistence.artifacts import LocalFilesystemArtifactStorage


class ArtifactStorageTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        self.storage = LocalFilesystemArtifactStorage(self.root)

    def tearDown(self):
        self._directory.cleanup()

    def test_put_get_round_trip_with_hash_and_size(self):
        payload = b'{"rows": 3}'
        stored = self.storage.put("artifact-a1", payload, content_type="application/json")
        self.assertTrue(self.storage.exists("artifact-a1"))
        self.assertEqual(self.storage.get("artifact-a1"), payload)
        self.assertEqual(stored.sha256, hashlib.sha256(payload).hexdigest())
        self.assertEqual(stored.byte_size, len(payload))
        self.assertEqual(stored.content_type, "application/json")
        self.assertTrue(stored.location.startswith(str(self.root)))

    def test_unsafe_artifact_references_are_rejected(self):
        for reference in ("../escape", "/etc/passwd", "a/b", "", ".", "..", "a\\b"):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                self.storage.put(reference, b"x")

    def test_same_reference_with_same_content_is_idempotent(self):
        first = self.storage.put("a1", b"x")
        second = self.storage.put("a1", b"x")
        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(len(list(self.root.iterdir())), 1)

    def test_same_reference_with_different_content_is_rejected(self):
        self.storage.put("a1", b"x")
        with self.assertRaises(ValueError):
            self.storage.put("a1", b"y")

    def test_missing_artifact_raises(self):
        self.assertFalse(self.storage.exists("absent"))
        with self.assertRaises(FileNotFoundError):
            self.storage.get("absent")


if __name__ == "__main__":
    unittest.main()
