from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch


import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from terraflux_api.jobs import JobRunner
from terraflux_api.services import canonical_sha256, file_sha256
from terraflux_api.storage import LocalStore


class JobGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.store = LocalStore(self.root / "state")
        self.runner = JobRunner(self.store, self.workspace)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_point_cloud_preference_is_not_overridden_by_available_dtm(self) -> None:
        terrain = {"id": "terrain", "created_at": "2026-01-01T00:00:00Z"}
        cloud = {"id": "cloud", "created_at": "2026-01-01T00:00:00Z"}
        selected_terrain, selected_cloud = self.runner._select_elevation_source(
            {"DTM_DEM": [terrain], "POINT_CLOUD": [cloud]}, "POINT_CLOUD"
        )
        self.assertIsNone(selected_terrain)
        self.assertEqual(cloud, selected_cloud)

    def test_worker_passes_sigma_without_using_legacy_radius(self) -> None:
        project = {"id": "prj-sigma", "name": "Sigma test"}
        self.store.insert("projects", project)
        for suffix, sulcation, expected in (
            ("zero", {"terrain_smoothing_sigma_m": 0, "terrain_smoothing_radius_m": 17}, "0"),
            ("custom", {"terrain_smoothing_sigma_m": 2.5, "reference_alert_grade_pct": 3.5}, "2.5"),
            ("legacy", {"terrain_smoothing_radius_m": 17}, "4.0"),
        ):
            with self.subTest(case=suffix):
                run = {"id": f"run-{suffix}", "project_id": project["id"],
                       "request_id": f"req-{suffix}", "product_ids": ["SULCATION_E0"]}
                self.store.insert("generation_requests", {
                    "id": run["request_id"], "project_id": project["id"],
                    "configuration_snapshot": {
                        "sulcation": sulcation, "topography": {"field_id_column": "field_id"},
                        "constraints": {"power_network_state": "DECLARED_NONE"},
                    },
                })
                output = self.store.project_dir(project["id"]) / "runs" / run["id"] / "products" / "topography_e0"
                output.mkdir(parents=True)
                (output / "topography_manifest.json").write_text(json.dumps({
                    "resolution_provenance": {"output_grid_resolution_m": 1.0},
                }), encoding="utf-8")
                with patch.object(self.runner, "_project_topography"), patch.object(
                    self.runner, "_run_process", side_effect=RuntimeError("captured-command")
                ) as execute:
                    with self.assertRaisesRegex(RuntimeError, "captured-command"):
                        self.runner._project_pipeline_e0(run)
                    command = execute.call_args.args[1]
                    self.assertEqual(command[command.index("--terrain-smoothing-sigma-m") + 1], expected)
                    self.assertNotIn("--terrain-smoothing-radius-m", command)
                    self.assertEqual(command[command.index("--reference-alert-grade-pct") + 1], str(sulcation.get("reference_alert_grade_pct", 5.0)))

    def test_immutable_request_hash_is_revalidated_before_execution(self) -> None:
        request = {"id": "req-1", "project_id": "prj-1", "product_ids": ["SULCATION_E0"]}
        request["sha256"] = canonical_sha256(request)
        self.runner._verify_request_snapshot(request)
        request["product_ids"] = ["CF0_CONTINUOUS"]
        with self.assertRaisesRegex(RuntimeError, "immutable generation request hash mismatch"):
            self.runner._verify_request_snapshot(request)

    def test_effective_resolution_blocks_upsampled_raster(self) -> None:
        run = {"product_ids": ["SULCATION_E0", "CF0_CONTINUOUS"]}
        configuration = {"sulcation": {"row_spacing_m": 1.5}}
        manifest = {
            "resolution_provenance": {
                "source_type": "DTM_DEM",
                "source_native_resolution_m": 3.4,
                "output_grid_resolution_m": 1.0,
                "effective_resolution_m": 3.4,
                "upsampling_detected": True,
            }
        }
        with self.assertRaisesRegex(ValueError, "TERRAIN_RESOLUTION_TOO_COARSE_FOR_ROWS"):
            self.runner._enforce_scenario_resolution_gate(run, manifest, configuration)

    def test_point_cloud_gate_requires_ground_class_2_and_uses_output_grid(self) -> None:
        run = {"product_ids": ["SULCATION_E0"]}
        configuration = {"sulcation": {"row_spacing_m": 1.5}}
        provenance = {
            "source_type": "POINT_CLOUD",
            "output_grid_resolution_m": 1.0,
            "effective_resolution_m": 1.0,
            "ground_classification_filter": "Classification[2:2]",
        }
        self.runner._enforce_scenario_resolution_gate(
            run, {"resolution_provenance": provenance}, configuration
        )
        provenance["ground_classification_filter"] = None
        with self.assertRaisesRegex(RuntimeError, "POINT_CLOUD_GROUND_FILTER_UNVERIFIED"):
            self.runner._enforce_scenario_resolution_gate(
                run, {"resolution_provenance": provenance}, configuration
            )

    def test_c1_inherits_the_cf0_effective_resolution_gate(self) -> None:
        run = {"product_ids": ["CF0_CONTINUOUS", "C1_EMBEDDED_SCREENING"]}
        configuration = {"sulcation": {"row_spacing_m": 1.5}}
        manifest = {
            "resolution_provenance": {
                "source_type": "DTM_DEM",
                "output_grid_resolution_m": 1.0,
                "effective_resolution_m": 2.0,
            }
        }
        with self.assertRaisesRegex(ValueError, "TERRAIN_RESOLUTION_TOO_COARSE_FOR_CF0"):
            self.runner._enforce_scenario_resolution_gate(run, manifest, configuration)

    def test_c1_request_payload_cannot_claim_design_or_guidance(self) -> None:
        project_request = self.workspace / "engine" / "project_request.json"
        cf0_manifest = self.workspace / "products" / "cf0.json"
        screening_request = self.workspace / "engine" / "c1.json"
        payload = self.runner._c1_screening_request_payload(
            screening_id="run-1-c1-e0",
            project_request_path=project_request,
            cf0_manifest_path=cf0_manifest,
            screening_request_path=screening_request,
        )

        self.assertEqual(payload["requested_release"], "C1_E0_CONCEPT_ALIGNMENT_NOT_DIMENSIONED")
        self.assertEqual(
            payload["screening_parameters"]["vertical_interval_candidates_m"],
            [2.0, 4.0, 6.0],
        )
        self.assertEqual(
            payload["variant_policy"]["EMBUTIDA_TD"],
            "NOT_GENERATED_WITHOUT_VERIFIED_RECEIVER_AND_GRADE_RULE",
        )
        self.assertIsNone(payload["variant_policy"]["receiver_dataset_ref"])
        for claim in (
            "agronomic_default",
            "pce_spacing_claim",
            "pcx_dimensioning_claim",
            "ti_approval_claim",
            "td_approval_claim",
            "guidance_claim",
        ):
            self.assertIs(payload["assumption_contract"][claim], False)

    def test_e0_output_integrity_is_bound_to_each_declared_path(self) -> None:
        output_dir = self.workspace / "run" / "e0"
        output_dir.mkdir(parents=True)
        gpkg = output_dir / "scenario.gpkg"
        map_path = output_dir / "map.png"
        gpkg.write_bytes(b"gpkg")
        map_path.write_bytes(b"png")
        integrity = {
            "geopackage": {
                "path": str(gpkg),
                "size_bytes": gpkg.stat().st_size,
                "sha256": file_sha256(gpkg),
            },
            "map": {
                "path": str(map_path),
                "size_bytes": map_path.stat().st_size,
                "sha256": file_sha256(map_path),
            },
        }
        self.runner._verify_known_outputs(
            output_dir, {"geopackage": gpkg, "map": map_path}, integrity
        )
        integrity["map"]["path"] = str(gpkg)
        with self.assertRaisesRegex(RuntimeError, "manifest path mismatch"):
            self.runner._verify_known_outputs(
                output_dir, {"geopackage": gpkg, "map": map_path}, integrity
            )

    def test_cf0_rejects_an_unsigned_extra_raster(self) -> None:
        output_dir = self.workspace / "run" / "cf0"
        raster_dir = output_dir / "rasters"
        raster_dir.mkdir(parents=True)
        gpkg = output_dir / "cf0.gpkg"
        map_path = output_dir / "cf0.png"
        raster = raster_dir / "signed.tif"
        extra = raster_dir / "unsigned.tif"
        for path, value in ((gpkg, b"g"), (map_path, b"m"), (raster, b"r"), (extra, b"x")):
            path.write_bytes(value)

        def record(path: Path) -> dict[str, object]:
            return {
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "sha256": file_sha256(path),
            }

        manifest = {
            "outputs": {
                "geopackage": record(gpkg),
                "map": record(map_path),
                "rasters": [record(raster)],
            }
        }
        with self.assertRaisesRegex(RuntimeError, "bundle differs"):
            self.runner._verify_cf0_outputs(
                output_dir, manifest, gpkg, map_path, raster_dir
            )

    def test_failed_run_publications_are_removed_from_api_state(self) -> None:
        run = {"id": "run-1", "project_id": "prj-1"}
        self.store.insert("runs", run)
        self.store.insert("artifacts", {"id": "art-1", "run_id": "run-1"})
        self.store.insert("scenarios", {"id": "run-1:E0A", "run_id": "run-1"})
        self.runner._discard_run_publications("run-1")
        self.assertEqual([], self.store.list("artifacts"))
        self.assertEqual([], self.store.list("scenarios"))

    def test_worker_restart_fails_orphaned_run_and_requeues_only_queued_work(self) -> None:
        self.store.insert(
            "runs",
            {
                "id": "run-running",
                "project_id": "prj-1",
                "status": "RUNNING",
                "progress": 68,
                "created_at": "2026-01-01T00:00:00Z",
            },
        )
        self.store.insert(
            "runs",
            {
                "id": "run-queued",
                "project_id": "prj-1",
                "status": "QUEUED",
                "created_at": "2026-01-02T00:00:00Z",
            },
        )
        self.store.insert("artifacts", {"id": "art-1", "run_id": "run-running"})
        self.store.insert("scenarios", {"id": "run-running:E0A", "run_id": "run-running"})

        queued = self.runner._recover_orphaned_runs()

        recovered = self.store.get("runs", "run-running")
        self.assertEqual(["run-queued"], queued)
        self.assertEqual("FAILED", recovered["status"])
        self.assertEqual("WORKER_PROCESS_INTERRUPTED", recovered["error_code"])
        self.assertEqual("INTERRUPTED_NO_PUBLISHED_PACKAGE", recovered["result"])
        self.assertTrue(recovered["result_summary"]["partial_publications_discarded"])
        self.assertEqual([], self.store.list("artifacts"))
        self.assertEqual([], self.store.list("scenarios"))


if __name__ == "__main__":
    unittest.main()
