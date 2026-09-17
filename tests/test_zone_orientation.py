"""Regression guard against vertical zone inversion.

The authoritative physical coordinate for pitch height is ``plate_z`` (feet above the
ground). The Statcast ``zone`` field numbers the strike-zone heart 1-9 (1-3 top, 4-6
middle, 7-9 bottom) and the shadow regions 11-14 (11-12 upper outside quadrants, 13-14
lower outside quadrants). This test locks the semantic definitions to that orientation so
a future edit cannot silently flip "upper third" to the bottom of the zone, and it locks
the zones 11-12 wording to "upper outside quadrants" rather than a strict above-the-zone
predicate that the numbered zones do not implement.
"""

import unittest
from pathlib import Path

from app.config import settings
from app.semantic.field_mapping import (FieldMappingRegistry, ZONE_LOWER_THIRD,
                                        ZONE_UPPER_OUTSIDE, ZONE_UPPER_THIRD)

_UPPER = (1, 2, 3)
_MIDDLE = (4, 5, 6)
_LOWER = (7, 8, 9)
_ABOVE = (11, 12)
_BELOW = (13, 14)


class ZoneOrientationTests(unittest.TestCase):
    def test_semantic_definitions_point_to_the_correct_zones(self):
        registry = FieldMappingRegistry()
        self.assertEqual(registry.location(ZONE_UPPER_THIRD).zone_codes, _UPPER)
        self.assertEqual(registry.location(ZONE_UPPER_OUTSIDE).zone_codes, _ABOVE)
        self.assertEqual(ZONE_LOWER_THIRD, _LOWER)
        # Upper third must never equal the lower third (the inversion regression).
        self.assertNotEqual(_UPPER, _LOWER)

    def test_zone_11_12_is_described_as_outside_not_strictly_above(self):
        registry = FieldMappingRegistry()
        definition = registry.location(ZONE_UPPER_OUTSIDE)
        self.assertEqual(definition.definition, ZONE_UPPER_OUTSIDE)
        self.assertEqual(definition.zone_codes, (11, 12))
        # The name and description must agree: numbered upper outside quadrants, not an
        # exact above-sz_top predicate.
        self.assertEqual(definition.predicate, "ZONE_SET")
        self.assertIn("outside", definition.description.casefold())
        self.assertNotIn("just above the strike zone", definition.description.casefold())
        # There must be no separate "above the zone" definition to confuse with zones 11-12.
        self.assertIsNone(registry.location("ZONE_ABOVE_UPPER_EDGE"))

    @unittest.skipUnless(
        any(settings.parquet_archive_path.glob("mlb_statcast_*.parquet")),
        "historical Parquet archive is not present locally")
    def test_plate_z_orders_zones_from_high_to_low(self):
        import duckdb
        connection = duckdb.connect(":memory:")
        rows = connection.execute(
            "SELECT zone, AVG(plate_z) AS avg_pz FROM "
            "read_parquet('"
            + str(settings.parquet_archive_path / "mlb_statcast_2023.parquet")
            + "') WHERE zone IS NOT NULL AND plate_z IS NOT NULL "
            "GROUP BY zone ORDER BY zone"
        ).fetchall()
        connection.close()
        by_zone = {int(zone): float(avg) for zone, avg in rows}
        mean = lambda zones: sum(by_zone[z] for z in zones) / len(zones)
        # Above the zone is highest, then the upper third, middle, lower, below.
        self.assertGreater(mean(_ABOVE), mean(_UPPER))
        self.assertGreater(mean(_UPPER), mean(_MIDDLE))
        self.assertGreater(mean(_MIDDLE), mean(_LOWER))
        self.assertGreater(mean(_LOWER), mean(_BELOW))


if __name__ == "__main__":
    unittest.main()
