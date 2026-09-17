import json
import tempfile
import unittest
from pathlib import Path

from osgeo import ogr, osr

from scripts.rows_web import export_rows


class RowsWebTests(unittest.TestCase):
    def source(self, folder, scenario=True):
        path = Path(folder) / "rows.gpkg"
        dataset = ogr.GetDriverByName("GPKG").CreateDataSource(str(path))
        reference = osr.SpatialReference()
        reference.ImportFromEPSG(31983)
        for layer_name in ("continuous_rows", "diagnostic_rows"):
            layer = dataset.CreateLayer(layer_name, reference, ogr.wkbLineString25D)
            layer.CreateField(ogr.FieldDefn("candidate_id", ogr.OFTString))
            layer.CreateField(ogr.FieldDefn("blocker_codes", ogr.OFTString))
            for index in range(2):
                feature = ogr.Feature(layer.GetLayerDefn())
                if scenario:
                    feature.SetField("candidate_id", "CF0A_CONSERVACAO")
                feature.SetField("blocker_codes", "RECEIVER_NOT_REVIEWED")
                geometry = ogr.CreateGeometryFromWkt(f"LINESTRING Z (500000 {7600000 + index} 100,500010 {7600000 + index} 101)")
                feature.SetGeometry(geometry)
                layer.CreateFeature(feature)
        dataset = None
        return path

    def test_parts_preserve_all_rows_and_diagnostics(self):
        with tempfile.TemporaryDirectory() as folder:
            source = self.source(folder)
            result = export_rows(source, source, Path(folder) / "web", max_part_vertices=3)
            self.assertEqual(len(result["outputs"]), 4)
            self.assertEqual(sum(record["feature_count"] for record in result["outputs"]), 4)
            self.assertEqual({record["inspection_status"] for record in result["outputs"]}, {"SCREENING", "DIAGNOSTIC"})
            for record in result["outputs"]:
                payload = json.loads(Path(record["path"]).read_text())
                feature = payload["features"][0]
                self.assertEqual(feature["properties"]["source_elevations_m"], [100, 101])
                self.assertEqual(feature["properties"]["blocker_codes"], "RECEIVER_NOT_REVIEWED")
                self.assertEqual(payload["terrain_sha256"], result["terrain_sha256"])
                self.assertFalse(feature["properties"]["guidance_authorized"])

    def test_missing_scenario_does_not_publish_files(self):
        with tempfile.TemporaryDirectory() as folder:
            source = self.source(folder, scenario=False)
            output = Path(folder) / "web"
            with self.assertRaisesRegex(ValueError, "identity"):
                export_rows(source, source, output)
            self.assertFalse(output.exists())
