import json
import tempfile
import unittest
from pathlib import Path

from osgeo import ogr, osr

from scripts.rows_web import export_rows
from scripts.build_platform_engine_request import build_request, write_validated_request
from tests import test_build_platform_engine_request as builder_tests


class RowsWebTests(unittest.TestCase):
    def test_reference_preserves_frozen_request_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            inputs = builder_tests.PlatformEngineRequestBuilderTests().make_inputs(root, reference_alert_grade_pct=3.5)
            request_path = root / "request.json"
            write_validated_request(build_request(inputs), request_path)
            source = self.source(folder)
            result = export_rows(source, inputs.dtm_path, root / "web", request_path=request_path)
            for record in result["outputs"]:
                payload = json.loads(Path(record["path"]).read_text())
                reference = payload["features"][0]["properties"]["profile_reference"]
                self.assertEqual(reference["grade_alert_pct"], 3.5)
                self.assertEqual(reference["request_id"], inputs.request_id)
                self.assertEqual(reference["request_sha256"], result["request_sha256"])
                self.assertEqual(reference["usage"], "SCREENING_ALERT_ONLY")
                self.assertEqual(reference["provenance"]["origin"], "E0_ASSUMPTION")
                summary = json.loads(Path(record["profile_summary"]["path"]).read_text())
                self.assertEqual(summary["web_rows_sha256"], record["sha256"])
                self.assertEqual(summary["request_sha256"], result["request_sha256"])
                self.assertEqual(summary["row_count"], record["feature_count"])
                self.assertTrue(all(row["alert_length_m"] == 10 for row in summary["rows"]))
                self.assertTrue(all(row["screening_status"] == "REFERENCE_EXCEEDED" for row in summary["rows"]))

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
                self.assertEqual(feature["properties"]["source_chainages_m"], [0, 10])
                self.assertEqual(feature["properties"]["chainage_reference"], "SOURCE_PROJECTED_METRIC_XY")
                self.assertEqual(feature["properties"]["blocker_codes"], "RECEIVER_NOT_REVIEWED")
                self.assertEqual(payload["terrain_sha256"], result["terrain_sha256"])
                self.assertFalse(feature["properties"]["guidance_authorized"])
                self.assertNotIn("profile_reference", feature["properties"])
                summary = json.loads(Path(record["profile_summary"]["path"]).read_text())
                self.assertFalse(summary["guidance_authorized"])
                self.assertTrue(all(row["screening_status"] == "NOT_EVALUATED" for row in summary["rows"]))

    def test_missing_scenario_does_not_publish_files(self):
        with tempfile.TemporaryDirectory() as folder:
            source = self.source(folder, scenario=False)
            output = Path(folder) / "web"
            with self.assertRaisesRegex(ValueError, "identity"):
                export_rows(source, source, output)
            self.assertFalse(output.exists())
