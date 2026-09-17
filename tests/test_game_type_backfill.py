import tempfile
import unittest
from pathlib import Path

from data_loader.backfill_game_type import backfill_parquet


class GameTypeBackfillTests(unittest.TestCase):
    def test_incomplete_mapping_leaves_existing_archive_unchanged(self):
        import duckdb
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'mlb_statcast_2023.parquet'
            connection = duckdb.connect()
            try:
                connection.execute("COPY (SELECT * FROM (VALUES (1, 'R'), (2, 'W')) "
                                   f"AS p(game_pk, game_type)) TO '{archive}' (FORMAT PARQUET)")
            finally:
                connection.close()
            before = archive.read_bytes()
            with self.assertRaisesRegex(ValueError, 'missing'):
                backfill_parquet(root, {1: 'R'})
            self.assertEqual(archive.read_bytes(), before)
