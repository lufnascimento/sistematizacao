import unittest

from terraflux_api.map_layers import build_map_layers


class MapLayerTests(unittest.TestCase):
    def test_unreferenced_files_and_other_projects_are_not_ready(self):
        run = {"id": "run", "project_id": "project", "status": "SUCCEEDED"}
        base = {"id": "a", "run_id": "run", "project_id": "project", "product_id": "p", "sha256": "hash", "size_bytes": 10}
        result = build_map_layers(run, [
            {**base, "filename": "old.geojson"},
            {**base, "filename": "map.png"},
            {**base, "filename": "other.geojson", "project_id": "another"},
        ])
        self.assertEqual(len(result["layers"]), 1)
        self.assertEqual(result["ready_count"], 0)
        self.assertIsNone(result["layers"][0]["source_url"])

    def test_only_successful_explicit_web_geometry_is_loadable(self):
        artifact = {"id": "a", "run_id": "run", "project_id": "project", "product_id": "p",
                    "sha256": "hash", "size_bytes": 10, "filename": "paths.geojson",
                    "spatial_metadata": {"format": "RFC7946", "horizontal_crs": "OGC:CRS84", "geometry_type": "LineString"}}
        for status, expected in (("SUCCEEDED", 1), ("RUNNING", 0), ("FAILED", 0)):
            with self.subTest(status=status):
                result = build_map_layers({"id": "run", "project_id": "project", "status": status}, [artifact])
                self.assertEqual(result["ready_count"], expected)
                self.assertFalse(result["terrain_mesh_available"])
