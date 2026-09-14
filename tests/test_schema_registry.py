import unittest

from app.context.registry_source import SchemaRegistrySource
from app.context.service import ContextRequest, ContextService
from app.models.schema import SchemaTable
from app.semantic.schema_registry import SchemaRegistry


def registry() -> SchemaRegistry:
    return SchemaRegistry(tables=(
        SchemaTable(table_name="statcast_pitches", source_kind="POSTGRES",
                    description="Hot Statcast pitch events", columns=("release_speed", "launch_speed")),
        SchemaTable(table_name="mlb_statcast_2015", source_kind="PARQUET",
                    description="Cold Statcast archive", columns=("game_date", "launch_speed")),
    ))


class SchemaRegistryTests(unittest.TestCase):
    def test_lookup_and_deterministic_search(self):
        items = registry()
        self.assertEqual(items.get("statcast_pitches").source_kind, "POSTGRES")
        self.assertEqual([t.table_name for t in items.search("launch_speed")],
                         ["statcast_pitches", "mlb_statcast_2015"])
        self.assertEqual([t.table_name for t in items.search("parquet")], ["mlb_statcast_2015"])
        self.assertEqual(items.search("missing"), ())

    def test_duplicate_table_names_fail(self):
        duplicate = SchemaTable(table_name="t", source_kind="POSTGRES")
        with self.assertRaises(ValueError):
            SchemaRegistry(tables=(duplicate, duplicate))

    def test_source_exposes_schema_context_items(self):
        service = ContextService((SchemaRegistrySource(registry()),))
        package = service.retrieve(ContextRequest(request_id="q", kinds=("SCHEMA",), query="launch_speed"))
        self.assertEqual({entry.item_id for entry in package.items},
                         {"statcast_pitches", "mlb_statcast_2015"})
        self.assertTrue(all(entry.kind == "SCHEMA" for entry in package.items))
        self.assertTrue(all(entry.provenance_ref.startswith("schema:") for entry in package.items))


if __name__ == "__main__":
    unittest.main()
