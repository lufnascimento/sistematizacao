import tempfile
import unittest
from pathlib import Path

from osgeo import ogr, osr

from scripts.boundaries_web import export_boundaries
from tests import test_terrain_web_mesh as terrain_helpers


class BoundariesWebTests(unittest.TestCase):
    def boundary(self):
        reference = osr.SpatialReference()
        reference.ImportFromEPSG(31983)
        geometry = ogr.CreateGeometryFromWkt(
            "POLYGON ((500001 7599999,500009 7599999,500009 7599991,500001 7599991,500001 7599999),"
            "(500003 7599997,500007 7599997,500007 7599993,500003 7599993,500003 7599997))"
        )
        return {"srs": reference, "field_ids": ["field-a"], "geometries": [geometry]}

    def test_outlines_keep_identity_holes_and_original_geometry(self):
        with tempfile.TemporaryDirectory() as folder:
            terrain = Path(folder) / "terrain.tif"
            terrain_helpers.TerrainWebMeshTests().create_raster(terrain)
            boundary = self.boundary()
            result = export_boundaries(boundary, terrain, Path(folder) / "outline.geojson")
            self.assertEqual(len(result["features"]), 2)
            self.assertEqual(boundary["geometries"][0].GetGeometryRef(0).GetPointCount(), 5)
            self.assertTrue(result["features"][1]["properties"]["interior_ring"])
            for feature in result["features"]:
                self.assertEqual(feature["properties"]["field_id"], "field-a")
                self.assertTrue(feature["properties"]["height_coverage_complete"])
                self.assertEqual(len(feature["geometry"]["coordinates"]), len(feature["properties"]["source_elevations_m"]))
            self.assertFalse(result["guidance_authorized"])

    def test_missing_heights_are_null_not_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            terrain = Path(folder) / "terrain.tif"
            terrain_helpers.TerrainWebMeshTests().create_raster(terrain, hole=True)
            result = export_boundaries(self.boundary(), terrain, Path(folder) / "outline.geojson")
            interior = result["features"][1]["properties"]
            self.assertIn(None, interior["source_elevations_m"])
            self.assertNotIn(0, interior["source_elevations_m"])
            self.assertFalse(interior["height_coverage_complete"])

    def test_large_boundary_is_not_partially_published(self):
        with tempfile.TemporaryDirectory() as folder:
            terrain = Path(folder) / "terrain.tif"
            terrain_helpers.TerrainWebMeshTests().create_raster(terrain)
            output = Path(folder) / "outline.geojson"
            with self.assertRaisesRegex(ValueError, "limit"):
                export_boundaries(self.boundary(), terrain, output, max_vertices=3)
            self.assertFalse(output.exists())
