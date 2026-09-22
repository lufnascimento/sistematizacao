import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from terraflux_api.jobs import JobRunner
from terraflux_api.services import file_sha256


class RowWebPublicationTests(unittest.TestCase):
    def setup_runner(self, root, corrupt=False):
        source, terrain = root / "rows.gpkg", root / "dtm.tif"
        source.write_bytes(b"original-rows")
        terrain.write_bytes(b"source-terrain")
        output = root / "web"
        output.mkdir()
        layer = output / "rows_01_001.geojson"
        layer.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
        summary_path = output / "diagnostico_perfis_01_001.json"
        summary_path.write_text(json.dumps({"report_type": "SOURCE_ROW_PROFILE_SUMMARY",
            "source_sha256": file_sha256(source), "web_rows_sha256": file_sha256(layer),
            "terrain_sha256": file_sha256(terrain), "request_sha256": None, "scenario_key": "candidate",
            "inspection_status": "DIAGNOSTIC", "row_count": 0, "rows": [], "guidance_authorized": False}), encoding="utf-8")
        manifest = {"status": "AVAILABLE", "source_sha256": file_sha256(source), "terrain_sha256": file_sha256(terrain),
                    "outputs": [{"path": str(layer.resolve()), "sha256": "wrong" if corrupt else file_sha256(layer),
                                 "size_bytes": layer.stat().st_size, "scenario_key": "candidate", "scenario_name": "Conservacao",
                                 "inspection_status": "DIAGNOSTIC", "part": 1, "part_count": 1, "feature_count": 0,
                                 "profile_summary": {"path": str(summary_path), "sha256": file_sha256(summary_path), "size_bytes": summary_path.stat().st_size}}]}
        (output / "rows_web_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        runner = JobRunner(Mock(), root)
        runner._qgis_python_launcher = Mock(return_value=Path("qgis-python"))
        runner._run_process = Mock()
        runner._artifact = Mock()
        return runner, source, terrain

    def test_diagnostics_are_published_hidden_with_scenario_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            runner, source, terrain = self.setup_runner(Path(folder))
            self.assertEqual(runner._publish_row_web({"id": "run"}, source, terrain, "CF0_CONTINUOUS"), 3)
            self.assertEqual(runner._artifact.call_args_list[1].args[1].name, "diagnostico_perfis_01_001.json")
            metadata = runner._artifact.call_args.kwargs["spatial_metadata"]
            self.assertEqual(metadata["scenario_key"], "candidate")
            self.assertFalse(metadata["default_visible"])
            self.assertTrue(metadata["inspection_only"])

    def test_corrupt_derivative_is_not_published(self):
        with tempfile.TemporaryDirectory() as folder:
            runner, source, terrain = self.setup_runner(Path(folder), corrupt=True)
            with self.assertRaisesRegex(RuntimeError, "hash mismatch"):
                runner._publish_row_web({"id": "run"}, source, terrain, "CF0_CONTINUOUS")
            self.assertEqual(runner._artifact.call_count, 1)

    def test_request_mismatch_blocks_row_publication(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runner, source, terrain = self.setup_runner(root)
            request = root / "request.json"
            request.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "request lineage mismatch"):
                runner._publish_row_web({"id": "run"}, source, terrain, "CF0_CONTINUOUS", request)
            command = runner._run_process.call_args.args[1]
            self.assertEqual(command[command.index("--request") + 1], str(request))
            self.assertEqual(runner._artifact.call_count, 1)

    def test_summary_with_valid_hash_but_wrong_lineage_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runner, source, terrain = self.setup_runner(root)
            path = root / "web" / "diagnostico_perfis_01_001.json"
            payload = json.loads(path.read_text())
            payload["scenario_key"] = "wrong-alternative"
            path.write_text(json.dumps(payload))
            manifest_path = root / "web" / "rows_web_manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["outputs"][0]["profile_summary"].update(sha256=file_sha256(path), size_bytes=path.stat().st_size)
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(RuntimeError, "summary lineage mismatch"):
                runner._publish_row_web({"id": "run"}, source, terrain, "CF0_CONTINUOUS")
            self.assertEqual(runner._artifact.call_count, 1)
