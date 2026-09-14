import unittest

from scripts.overflow_geojson import export_overflow_geojson


class OverflowGeojsonTests(unittest.TestCase):
    def test_utm_central_meridian_and_vertical_lineage(self):
        path = {"id": "P", "reach_id": "T", "receiver_id": "R", "length_m": 100,
                "elevation_drop_m": 1, "screening_status": "SCREENED_CLEAR",
                "coordinates": [[500000, 10000000, 100], [500000, 9999900, 99]]}
        result = export_overflow_geojson([path], "EPSG:32723")
        feature = result["features"][0]
        self.assertAlmostEqual(feature["geometry"]["coordinates"][0][0], -45)
        self.assertAlmostEqual(feature["geometry"]["coordinates"][0][1], 0)
        self.assertLess(feature["geometry"]["coordinates"][1][1], 0)
        self.assertEqual(len(feature["geometry"]["coordinates"][0]), 2)
        self.assertEqual(feature["properties"]["source_elevations_m"], [100, 99])
        self.assertEqual(path["coordinates"][0], [500000, 10000000, 100])

    def test_missing_geographic_and_feet_crs_are_rejected(self):
        for crs in (None, "EPSG:4326", "EPSG:2263"):
            with self.subTest(crs=crs), self.assertRaises(ValueError):
                export_overflow_geojson([], crs)
