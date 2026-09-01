import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_platform_engine_request import (
    RequestBuildError,
    RequestInputs,
    build_request,
    main,
    write_validated_request,
)
from scripts.project_request import load_project_request


FIXED_TIME = "2026-08-26T12:00:00+00:00"


class PlatformEngineRequestBuilderTests(unittest.TestCase):
    def make_inputs(self, root: Path, **overrides) -> RequestInputs:
        dtm = root / "terrain.tif"
        boundary = root / "fields.geojson"
        dtm.write_bytes(b"test-dtm")
        boundary.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
        values = {
            "project_id": "project-alpha",
            "request_id": "request-alpha-001",
            "dtm_path": dtm,
            "boundary_path": boundary,
            "field_ids": ("field-a", "field-b"),
            "property_ids": ("property-a",),
            "crs": "EPSG:31982",
            "boundary_id_field": "field_id",
            "boundary_layer": None,
            "row_spacing_m": 1.5,
            "headland_m": 12.0,
            "minimum_work_path_radius_m": 25.0,
            "minimum_shot_length_m": 80.0,
            "nominal_speed_kmh": 6.0,
            "power_status": "DECLARED_NONE",
            "cross_field": False,
            "cross_property": False,
            "cross_property_permission": "NOT_APPLICABLE",
            "expected_yield_t_ha": 82.0,
            "created_at": FIXED_TIME,
            "input_captured_at": FIXED_TIME,
            "user_name": "Test User",
            "user_organization": "Test Farm",
            "user_role": "Agricultural Planner",
        }
        values.update(overrides)
        return RequestInputs(**values)

    def test_direct_inputs_build_and_validate_without_gis(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = build_request(self.make_inputs(root))
            output = root / "request.json"
            report = write_validated_request(request, output)
            resolved = load_project_request(output)

            self.assertEqual("request-alpha-001", resolved.request["request_id"])
            self.assertEqual("DECLARED_NONE", resolved.power_inventory_status)
            self.assertEqual([], report["warnings"])
            self.assertIn("CONSTRAINT_INVENTORY_INCOMPLETE", report["release_blockers"])
            self.assertEqual(12.0, resolved.engine_parameter_overrides()["outer_headland_m"])
            self.assertEqual(25.0, resolved.conservative_operation_limit("fleet.minimum_work_path_radius_m", "max"))
            self.assertEqual(82.0, resolved.value("agronomy.expected_yield_t_ha"))

            provenance = {
                item["parameter_id"]: item["provenance"]
                for item in request["parameter_values"]
            }
            self.assertTrue(provenance["agronomy.row_spacing_m"]["source_ref"].startswith("USER_CONFIG:"))
            self.assertEqual("DECLARED", provenance["agronomy.row_spacing_m"]["origin"])
            self.assertTrue(provenance["fleet.required_headland_width_m"]["source_ref"].startswith("SYSTEM_MODEL:"))
            self.assertEqual("CALCULATED", provenance["fleet.required_headland_width_m"]["origin"])
            self.assertTrue(provenance["e0.nominal_field_speed_kmh"]["source_ref"].startswith("SYSTEM_MODEL:"))
            self.assertEqual("E0_ASSUMPTION", provenance["e0.nominal_field_speed_kmh"]["origin"])

    def test_not_reviewed_power_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = build_request(
                self.make_inputs(root, power_status="NOT_REVIEWED", expected_yield_t_ha=None)
            )
            output = root / "request.json"
            report = write_validated_request(request, output)
            self.assertIn("OVERHEAD_POWER_LINE_NOT_REVIEWED", report["release_blockers"])
            self.assertNotIn(
                "agronomy.expected_yield_t_ha",
                {item["parameter_id"] for item in request["parameter_values"]},
            )

    def test_cross_property_requires_cross_field_permission_and_two_properties(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(RequestBuildError, "also requires cross-field"):
                build_request(
                    self.make_inputs(
                        root,
                        cross_property=True,
                        cross_property_permission="APPROVED",
                    )
                )
            with self.assertRaisesRegex(RequestBuildError, "at least two"):
                build_request(
                    self.make_inputs(
                        root,
                        cross_field=True,
                        cross_property=True,
                        cross_property_permission="APPROVED",
                    )
                )

    def test_cross_field_request_uses_continuous_guide_mode_and_validates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = build_request(self.make_inputs(root, cross_field=True))
            output = root / "cross-field-request.json"
            report = write_validated_request(request, output)

            self.assertEqual(["OC2_GUIA_CONTINUA"], request["scenario_request"]["connection_modes"])
            self.assertEqual("request-alpha-001", report["request_id"])

    def test_cli_reads_topography_manifest_and_writes_only_requested_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dtm = root / "dtm.tif"
            boundary = root / "fields.geojson"
            manifest = root / "topography_manifest.json"
            output = root / "generated-request.json"
            dtm.write_bytes(b"topography-dtm")
            boundary.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "generated_at": FIXED_TIME,
                        "inputs": {"boundary": {"path": str(boundary)}},
                        "outputs": {"dtm": {"path": str(dtm)}},
                        "configuration": {
                            "target_crs": "EPSG:31982",
                            "field_id_column": "field_id",
                        },
                        "scope": {"field_ids": ["field-a", "field-b"]},
                    }
                ),
                encoding="utf-8",
            )

            exit_code = main(
                [
                    "--project-id",
                    "project-manifest",
                    "--request-id",
                    "request-manifest-001",
                    "--topography-manifest",
                    str(manifest),
                    "--row-spacing-m",
                    "1.5",
                    "--headland-m",
                    "10",
                    "--minimum-work-path-radius-m",
                    "22",
                    "--minimum-shot-length-m",
                    "75",
                    "--nominal-speed-kmh",
                    "5.5",
                    "--power-status",
                    "DECLARED_NONE",
                    "--created-at",
                    FIXED_TIME,
                    "--output",
                    str(output),
                ]
            )

            self.assertEqual(0, exit_code)
            self.assertTrue(output.is_file())
            self.assertEqual(
                {"dtm.tif", "fields.geojson", "topography_manifest.json", "generated-request.json"},
                {path.name for path in root.iterdir()},
            )
            resolved = load_project_request(output)
            self.assertEqual(["field-a", "field-b"], resolved.request["scope"]["field_ids"])
            self.assertEqual("VALIDATED", resolved.dataset("project-manifest:dtm")["qa_status"])


if __name__ == "__main__":
    unittest.main()
