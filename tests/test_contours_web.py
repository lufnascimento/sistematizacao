import tempfile
import unittest
from pathlib import Path

from osgeo import ogr, osr

from scripts.contours_web import export_contours


class ContoursWebTests(unittest.TestCase):
    def source(self, folder, dimension=3, varying=False, epsg=31983):
        path = Path(folder) / "contours.gpkg"
        dataset = ogr.GetDriverByName("GPKG").CreateDataSource(str(path))
        reference = osr.SpatialReference()
        reference.ImportFromEPSG(epsg)
        layer = dataset.CreateLayer("contours", reference, ogr.wkbLineString25D if dimension == 3 else ogr.wkbLineString)
        feature = ogr.Feature(layer.GetLayerDefn())
        geometry = ogr.CreateGeometryFromWkt("LINESTRING Z (500000 10000000 123, 500010 10000000 123)" if dimension == 3 else "LINESTRING (500000 10000000, 500010 10000000)")
        if varying:
            geometry.SetPoint(1, 500010, 10000000, 124)
        feature.SetGeometry(geometry)
        layer.CreateFeature(feature)
        dataset = None
        return path

    def test_preserves_source_heights_separately_from_geographic_coordinates(self):
        with tempfile.TemporaryDirectory() as folder:
            source = self.source(folder)
            result = export_contours(source, source, Path(folder) / "web.geojson")
            feature = result["features"][0]
            self.assertEqual(feature["properties"]["source_elevations_m"], [123, 123])
            self.assertEqual(len(feature["geometry"]["coordinates"][0]), 2)
            self.assertAlmostEqual(feature["geometry"]["coordinates"][0][0], -45, places=5)
            self.assertEqual(len(result["terrain_sha256"]), 64)
            self.assertFalse(result["guidance_authorized"])

    def test_limit_does_not_publish_truncated_layer(self):
        with tempfile.TemporaryDirectory() as folder:
            source = self.source(folder)
            output = Path(folder) / "web.geojson"
            with self.assertRaises(ValueError):
                export_contours(source, source, output, max_vertices=1)
            self.assertFalse(output.exists())

    def test_rejects_missing_source_elevations(self):
        with tempfile.TemporaryDirectory() as folder:
            source = self.source(folder, dimension=2)
            with self.assertRaises(ValueError):
                export_contours(source, source, Path(folder) / "web.geojson")

    def test_rejects_varying_elevation_as_contour(self):
        with tempfile.TemporaryDirectory() as folder:
            source = self.source(folder, varying=True)
            with self.assertRaisesRegex(ValueError, "constant"):
                export_contours(source, source, Path(folder) / "web.geojson")

    def test_rejects_non_metric_source(self):
        with tempfile.TemporaryDirectory() as folder:
            source = self.source(folder, epsg=4326)
            with self.assertRaisesRegex(ValueError, "metric"):
                export_contours(source, source, Path(folder) / "web.geojson")
