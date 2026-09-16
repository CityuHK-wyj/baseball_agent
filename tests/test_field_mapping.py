import unittest

from app.models.schema import FieldMapping, LocationDefinition
from app.semantic.field_mapping import (BATTER_RELATIVE_UPPER_EDGE, DEFAULT_FIELD_MAPPINGS,
                                        DEFAULT_LOCATION_DEFINITIONS, FieldMappingRegistry,
                                        ZONE_UPPER_THIRD)


class FieldMappingRegistryTests(unittest.TestCase):
    def test_semantic_keys_map_to_physical_fields_without_planner_columns(self):
        registry = FieldMappingRegistry()
        self.assertEqual(registry.physical_field("pitch_velocity", "PARQUET"), "release_speed")
        self.assertEqual(registry.physical_field("exit_velocity", "PARQUET"), "launch_speed")
        self.assertEqual(registry.physical_field("batter", "PARQUET"), "batter")
        self.assertEqual(registry.physical_field("batter", "POSTGRES"), "batter_id")
        self.assertIsNone(registry.physical_field("pitch_velocity", "FEATURE"))

    def test_fastball_code_set_is_explicit_and_testable(self):
        registry = FieldMappingRegistry()
        self.assertEqual(registry.pitch_type_codes("fastball"), ("FF", "SI", "FC", "FA"))
        self.assertIsNone(registry.pitch_type_codes("mystery"))

    def test_batter_relative_upper_edge_requires_sz_fields(self):
        registry = FieldMappingRegistry()
        location = registry.location(BATTER_RELATIVE_UPPER_EDGE)
        self.assertEqual(location.required_physical_fields, ("plate_z", "sz_top", "sz_bot"))
        self.assertEqual(registry.location(ZONE_UPPER_THIRD).zone_codes, (1, 2, 3))

    def test_zone_definition_requires_only_zone(self):
        registry = FieldMappingRegistry()
        self.assertEqual(registry.location(ZONE_UPPER_THIRD).required_physical_fields, ("zone",))

    def test_source_kinds_require_every_key(self):
        registry = FieldMappingRegistry()
        self.assertEqual(registry.source_kinds_for(("exit_velocity", "batter")),
                         ("PARQUET", "POSTGRES"))
        self.assertEqual(registry.source_kinds_for(("exit_velocity", "mystery")), ())

    def test_registry_rejects_duplicate_location_names(self):
        with self.assertRaises(ValueError):
            FieldMappingRegistry(locations=(DEFAULT_LOCATION_DEFINITIONS[0], DEFAULT_LOCATION_DEFINITIONS[0]))

    def test_default_mappings_are_physical_not_semantic(self):
        fields = {item.physical_field for item in DEFAULT_FIELD_MAPPINGS}
        self.assertIn("release_speed", fields)
        self.assertIn("launch_speed", fields)
        self.assertNotIn("pitch_velocity", fields)

    def test_game_type_maps_on_both_sources(self):
        registry = FieldMappingRegistry()
        self.assertEqual(registry.physical_field("game_type", "PARQUET"), "game_type")
        self.assertEqual(registry.physical_field("game_type", "POSTGRES"), "game_type")


if __name__ == "__main__":
    unittest.main()
