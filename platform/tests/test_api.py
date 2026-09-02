from __future__ import annotations

import io
import json
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from terraflux_api.api import create_app


class PlatformApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.data_root = root / "state"
        self.workspace = root / "workspace"
        (self.workspace / "config").mkdir(parents=True)
        (self.workspace / "dataset" / "derived").mkdir(parents=True)
        (self.workspace / "config" / "catalogo_presets_sistema.json").write_text(
            json.dumps({"schema_version": "test", "package_profiles": []}), encoding="utf-8"
        )
        self.app = create_app(self.data_root, self.workspace, max_upload_bytes=1024 * 1024)
        self.client_context = TestClient(self.app)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temp.cleanup()

    def create_project(self, name: str = "Fazenda Teste") -> dict:
        response = self.client.post("/api/projects", json={"name": name, "crs": "EPSG:31982"})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def upload(self, project_id: str, role: str, filename: str, content: bytes):
        return self.client.post(
            f"/api/projects/{project_id}/assets",
            data={"role": role},
            files={"file": (filename, content, "application/octet-stream")},
        )

    def wait_run(self, run_id: str, timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/runs/{run_id}")
            self.assertEqual(response.status_code, 200)
            run = response.json()
            if run["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                return run
            time.sleep(0.02)
        self.fail("run did not become terminal")

    def test_project_configuration_upload_readiness_and_validation_run(self) -> None:
        project = self.create_project()
        project_id = project["id"]

        configuration = self.client.get(f"/api/projects/{project_id}/configuration").json()
        configuration["constraints"]["power_network_state"] = "DECLARED_NONE"
        response = self.client.put(f"/api/projects/{project_id}/configuration", json=configuration)
        self.assertEqual(response.status_code, 200, response.text)

        boundary = {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {"id": "T1"}, "geometry": {"type": "Polygon", "coordinates": []}}],
        }
        response = self.upload(project_id, "FIELD_BOUNDARY", "../talhoes.geojson", json.dumps(boundary).encode())
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["stored_filename"].split("_", 1)[1], "talhoes.geojson")
        self.assertNotIn("path", response.json())

        response = self.upload(project_id, "POINT_CLOUD", "levantamento.laz", b"LASF" + b"\x00" * 64)
        self.assertEqual(response.status_code, 201, response.text)

        readiness = self.client.get(f"/api/projects/{project_id}/readiness").json()
        self.assertTrue(readiness["summary"]["minimum_inputs_present"])
        self.assertEqual(readiness["summary"]["blocking_count"], 0)
        power = next(item for item in readiness["evidence"] if item["id"] == "POWER_NETWORK")
        self.assertEqual(power["status"], "NOT_APPLICABLE")

        response = self.client.post(
            f"/api/projects/{project_id}/runs", json={"engine_id": "validate_uploads", "product_ids": []}
        )
        self.assertEqual(response.status_code, 202, response.text)
        run = self.wait_run(response.json()["id"])
        self.assertEqual(run["status"], "SUCCEEDED")
        artifacts = self.client.get(f"/api/runs/{run['id']}/artifacts").json()["items"]
        self.assertEqual(len(artifacts), 1)
        downloaded = self.client.get(artifacts[0]["download_url"])
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.json()["result"], "PASS_MINIMUM_UPLOAD_PACKAGE_PENDING_SPATIAL_QA")

    def test_orthomosaic_alone_does_not_claim_elevation_readiness(self) -> None:
        project_id = self.create_project()["id"]
        boundary = json.dumps({"type": "FeatureCollection", "features": []}).encode()
        self.assertEqual(self.upload(project_id, "FIELD_BOUNDARY", "talhoes.geojson", boundary).status_code, 201)
        little_tiff = b"II*\x00" + b"\x00" * 32
        self.assertEqual(self.upload(project_id, "ORTHOMOSAIC", "orto.tif", little_tiff).status_code, 201)
        readiness = self.client.get(f"/api/projects/{project_id}/readiness").json()
        self.assertFalse(readiness["summary"]["minimum_inputs_present"])
        blocker = next(item for item in readiness["blockers"] if item["code"] == "ELEVATION_SOURCE_MISSING")
        self.assertIn("LAS/LAZ", blocker["message"])

    def test_presets_and_immutable_request_snapshot(self) -> None:
        project = self.create_project()
        project_id = project["id"]
        preset_response = self.client.post(
            f"/api/projects/{project_id}/presets",
            json={
                "requested_delivery_level": "E0_TRIAGEM",
                "package_selections": [{"package_id": "AGRONOMIC_CONFIGURATION", "mode": "SYSTEM_MODELS"}],
            },
        )
        self.assertEqual(preset_response.status_code, 201, preset_response.text)
        preset = preset_response.json()
        self.assertEqual(len(preset["sha256"]), 64)

        request_response = self.client.post(
            f"/api/projects/{project_id}/requests",
            json={
                "name": "Cenarios iniciais",
                "product_ids": ["SULCATION_E0", "CF0_CONTINUOUS"],
                "preset_selection_id": preset["id"],
            },
        )
        self.assertEqual(request_response.status_code, 201, request_response.text)
        request = request_response.json()
        self.assertTrue(request["immutable"])
        self.assertEqual(len(request["sha256"]), 64)
        self.assertEqual(request["configuration_snapshot"]["topography"]["resolution_m"], 1.0)
        self.assertEqual(request["configuration_snapshot"]["sulcation"]["row_spacing_m"], 1.5)

    def test_pcx1_configuration_runs_from_immutable_request_and_publishes_products(self) -> None:
        project_id = self.create_project()["id"]
        configuration = self.client.get(f"/api/projects/{project_id}/configuration").json()
        configuration["hydrology_screening"] = {
            "enabled": True,
            "method": "NRCS_CURVE_NUMBER_EVENT_SCREENING",
            "catchment_area_ha": 10,
            "curve_number": 100,
            "initial_abstraction_ratio": 0.2,
            "parameter_evidence_state": "SYNTHETIC_TEST_ONLY",
            "parameter_source_id": "analytic-cn100-test",
            "rainfall_intervals": [
                {"duration_s": 600, "rainfall_mm": 5},
                {"duration_s": 600, "rainfall_mm": 10},
            ],
        }
        response = self.client.put(f"/api/projects/{project_id}/configuration", json=configuration)
        self.assertEqual(response.status_code, 200, response.text)
        request_response = self.client.post(
            f"/api/projects/{project_id}/requests",
            json={"name": "PCX1 sintetico", "product_ids": ["PCX1_RUNOFF_SCREENING"]},
        )
        self.assertEqual(request_response.status_code, 201, request_response.text)
        request = request_response.json()
        run_response = self.client.post(
            f"/api/projects/{project_id}/runs",
            json={
                "engine_id": "project_hydrology_screening",
                "request_id": request["id"],
                "product_ids": ["PCX1_RUNOFF_SCREENING"],
            },
        )
        self.assertEqual(run_response.status_code, 202, run_response.text)
        run = self.wait_run(run_response.json()["id"], timeout=20.0)
        self.assertEqual(run["status"], "SUCCEEDED", run)
        self.assertEqual(run["result_summary"]["total_rainfall_excess_mm"], 15)
        self.assertFalse(run["result_summary"]["guidance_authorized"])
        artifacts = self.client.get(f"/api/runs/{run['id']}/artifacts").json()["items"]
        self.assertEqual({item["filename"] for item in artifacts}, {
            "resultado_chuva_escoamento.json", "serie_chuva_escoamento.csv"
        })
        manifest_artifact = next(item for item in artifacts if item["filename"].endswith(".json"))
        manifest = self.client.get(manifest_artifact["download_url"]).json()
        self.assertEqual(manifest["release"], "PCX1_NRCS_CN_RAINFALL_EXCESS_ONLY")
        self.assertIn("PCX_HYDROGRAPH_NOT_INCLUDED_IN_THIS_PRODUCT", manifest["blocker_codes"])

        missing_request = self.client.post(
            f"/api/projects/{project_id}/runs",
            json={"engine_id": "project_hydrology_screening", "product_ids": ["PCX1_RUNOFF_SCREENING"]},
        )
        self.assertEqual(missing_request.status_code, 409)

    def test_preliminary_hydrograph_runs_with_plain_language_products(self) -> None:
        project_id = self.create_project()["id"]
        configuration = self.client.get(f"/api/projects/{project_id}/configuration").json()
        configuration["hydrology_screening"] = {
            "enabled": True,
            "method": "NRCS_CURVE_NUMBER_EVENT_SCREENING",
            "catchment_area_ha": 10,
            "curve_number": 100,
            "initial_abstraction_ratio": 0.2,
            "parameter_evidence_state": "SYNTHETIC_TEST_ONLY",
            "parameter_source_id": "analytic-hydrograph-test",
            "rainfall_intervals": [
                {"duration_s": 600, "rainfall_mm": 5},
                {"duration_s": 600, "rainfall_mm": 10},
            ],
            "hydrograph_enabled": True,
            "catchment_lag_minutes": 30,
            "hydrograph_step_minutes": 1,
            "triangle_base_to_peak_ratio": 2.67,
            "routing_enabled": True,
            "routing_source_node_id": "ENTRADA",
            "routing_reaches": [
                {"id": "T1", "upstream_node_id": "ENTRADA", "downstream_node_id": "JUNCAO", "travel_time_minutes": 10},
                {"id": "T2", "upstream_node_id": "JUNCAO", "downstream_node_id": "SAIDA", "travel_time_minutes": 20},
            ],
            "capacity_enabled": True,
            "reach_sections": [
                {"id": "T1", "bottom_width_m": 0.5, "side_slope_h_to_v": 1.5, "slope_m_m": 0.005, "manning_n": 0.04, "maximum_flow_depth_m": 0.6},
                {"id": "T2", "bottom_width_m": 0.2, "side_slope_h_to_v": 1.0, "slope_m_m": 0.001, "manning_n": 0.05, "maximum_flow_depth_m": 0.2},
            ],
        }
        response = self.client.put(f"/api/projects/{project_id}/configuration", json=configuration)
        self.assertEqual(response.status_code, 200, response.text)
        product_ids = ["PCX1_RUNOFF_SCREENING", "PCX2_HYDROGRAPH_SCREENING", "PCX3_REACH_ROUTING_SCREENING", "PCX4_SECTION_CAPACITY_SCREENING"]
        request_response = self.client.post(
            f"/api/projects/{project_id}/requests",
            json={"name": "Resposta da chuva", "product_ids": product_ids},
        )
        self.assertEqual(request_response.status_code, 201, request_response.text)
        run_response = self.client.post(
            f"/api/projects/{project_id}/runs",
            json={
                "engine_id": "project_hydrology_screening",
                "request_id": request_response.json()["id"],
                "product_ids": product_ids,
            },
        )
        self.assertEqual(run_response.status_code, 202, run_response.text)
        run = self.wait_run(run_response.json()["id"], timeout=20.0)
        self.assertEqual(run["status"], "SUCCEEDED", run)
        self.assertGreater(run["result_summary"]["peak_flow_m3_s"], 0)
        self.assertEqual(run["result_summary"]["hydrograph_mass_balance_status"], "PASS")
        self.assertEqual(run["result_summary"]["routing_mass_balance_status"], "PASS")
        self.assertEqual(run["result_summary"]["routed_reach_count"], 2)
        self.assertEqual(run["result_summary"]["capacity_within_count"], 1)
        self.assertEqual(run["result_summary"]["capacity_exceeded_count"], 1)
        self.assertNotIn("PCX_SECTION_CAPACITY_NOT_EVALUATED", run["result_summary"]["blocker_codes"])
        logs = self.client.get(f"/api/runs/{run['id']}/logs").json()["items"]
        self.assertTrue(any("somente por escoamento uniforme" in item["message"] for item in logs))
        artifacts = self.client.get(f"/api/runs/{run['id']}/artifacts").json()["items"]
        self.assertEqual(
            {item["filename"] for item in artifacts},
            {
                "resultado_chuva_escoamento.json",
                "serie_chuva_escoamento.csv",
                "hidrograma_preliminar.json",
                "hidrograma_preliminar.csv",
                "grafico_hidrograma_preliminar.png",
                "propagacao_preliminar_rede.json",
                "picos_por_trecho.csv",
                "verificacao_preliminar_capacidade.json",
                "capacidade_por_trecho.csv",
            },
        )
        hydrograph_artifact = next(item for item in artifacts if item["filename"] == "hidrograma_preliminar.json")
        hydrograph = self.client.get(hydrograph_artifact["download_url"]).json()
        self.assertEqual(hydrograph["mass_balance_status"], "PASS")
        self.assertFalse(hydrograph["guidance_authorized"])
        self.assertNotIn("PCX_HYDROGRAPH_NOT_INCLUDED_IN_THIS_PRODUCT", run["result_summary"]["blocker_codes"])

    def test_demo_only_publishes_whitelisted_existing_artifacts(self) -> None:
        project = self.create_project()
        demo_pdf = self.workspace / "dataset" / "derived" / "Dossie_Completo_Sistematizacao_E0_CF0_C1_Opcoes_2026-08-24.pdf"
        demo_pdf.write_bytes(b"%PDF-1.4\n% demo\n")
        response = self.client.post(
            f"/api/projects/{project['id']}/runs",
            json={"engine_id": "demo_current_dataset", "product_ids": ["COMPLETE_DOSSIER"]},
        )
        self.assertEqual(response.status_code, 202, response.text)
        run = self.wait_run(response.json()["id"])
        self.assertEqual(run["status"], "SUCCEEDED")
        artifact = self.client.get(f"/api/runs/{run['id']}/artifacts").json()["items"][0]
        self.assertEqual(artifact["source"], "DEMO_CURRENT_DATASET_REFERENCE")
        self.assertEqual(self.client.get(artifact["download_url"]).content, demo_pdf.read_bytes())

        rejected = self.client.post(
            f"/api/projects/{project['id']}/runs",
            json={"engine_id": "demo_current_dataset", "product_ids": ["C3_ESD"]},
        )
        self.assertEqual(rejected.status_code, 409)

        c1_without_cf0 = self.client.post(
            f"/api/projects/{project['id']}/runs",
            json={
                "engine_id": "demo_current_dataset",
                "product_ids": ["C1_EMBEDDED_SCREENING"],
            },
        )
        self.assertEqual(c1_without_cf0.status_code, 409)
        self.assertEqual(
            c1_without_cf0.json()["detail"]["code"],
            "PRODUCT_DEPENDENCY_MISSING",
        )

    def test_upload_rejects_role_mismatch_bad_magic_and_unsafe_zip(self) -> None:
        project_id = self.create_project()["id"]
        mismatch = self.upload(project_id, "POINT_CLOUD", "cloud.pdf", b"%PDF-1.4")
        self.assertEqual(mismatch.status_code, 422)
        bad_laz = self.upload(project_id, "POINT_CLOUD", "cloud.laz", b"not-las")
        self.assertEqual(bad_laz.status_code, 422)

        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("../talhoes.shp", b"unsafe")
        unsafe = self.upload(project_id, "FIELD_BOUNDARY", "talhoes.zip", payload.getvalue())
        self.assertEqual(unsafe.status_code, 422)
        self.assertEqual(unsafe.json()["detail"]["code"], "UPLOAD_REJECTED")

    def test_unknown_engine_and_product_are_rejected(self) -> None:
        project_id = self.create_project()["id"]
        engine = self.client.post(
            f"/api/projects/{project_id}/runs", json={"engine_id": "rm -rf", "product_ids": []}
        )
        self.assertEqual(engine.status_code, 422)
        product = self.client.post(
            f"/api/projects/{project_id}/requests", json={"name": "Teste", "product_ids": ["INVENTED"]}
        )
        self.assertEqual(product.status_code, 422)

        request_required = self.client.post(
            f"/api/projects/{project_id}/runs",
            json={"engine_id": "project_topography", "product_ids": ["TOPOGRAPHY_E0"]},
        )
        self.assertEqual(request_required.status_code, 409)

        mismatch = self.client.post(
            f"/api/projects/{project_id}/runs",
            json={"engine_id": "project_topography", "product_ids": ["SULCATION_E0"]},
        )
        self.assertEqual(mismatch.status_code, 409)

        pipeline_request_required = self.client.post(
            f"/api/projects/{project_id}/runs",
            json={"engine_id": "project_pipeline_e0", "product_ids": ["SULCATION_E0"]},
        )
        self.assertEqual(pipeline_request_required.status_code, 409)

        request = self.client.post(
            f"/api/projects/{project_id}/requests",
            json={"name": "Cenarios", "product_ids": ["SULCATION_E0"]},
        ).json()
        snapshot_mismatch = self.client.post(
            f"/api/projects/{project_id}/runs",
            json={
                "engine_id": "project_pipeline_e0",
                "request_id": request["id"],
                "product_ids": ["CF0_CONTINUOUS"],
            },
        )
        self.assertEqual(snapshot_mismatch.status_code, 409)
        self.assertEqual(snapshot_mismatch.json()["detail"]["code"], "REQUEST_PRODUCT_SNAPSHOT_MISMATCH")

        c1_without_cf0 = self.client.post(
            f"/api/projects/{project_id}/requests",
            json={"name": "C1 sem dependencia", "product_ids": ["C1_EMBEDDED_SCREENING"]},
        )
        self.assertEqual(c1_without_cf0.status_code, 409)
        self.assertEqual(
            c1_without_cf0.json()["detail"]["code"],
            "PRODUCT_DEPENDENCY_MISSING",
        )
        c1_with_cf0 = self.client.post(
            f"/api/projects/{project_id}/requests",
            json={
                "name": "C1 conceitual",
                "product_ids": ["CF0_CONTINUOUS", "C1_EMBEDDED_SCREENING"],
            },
        )
        self.assertEqual(c1_with_cf0.status_code, 201, c1_with_cf0.text)

    def test_catalog_and_health_expose_execution_boundary(self) -> None:
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["client_data_execution"], "E0_PIPELINE_AVAILABLE_WITH_READINESS_GATES")
        self.assertEqual(
            health.json()["client_data_products"],
            [
                "TOPOGRAPHY_E0",
                "SULCATION_E0",
                "CF0_CONTINUOUS",
                "C1_EMBEDDED_SCREENING",
                "PCX1_RUNOFF_SCREENING",
                "PCX2_HYDROGRAPH_SCREENING",
                "PCX3_REACH_ROUTING_SCREENING",
                "PCX4_SECTION_CAPACITY_SCREENING",
            ],
        )
        catalog = self.client.get("/api/catalog").json()
        self.assertEqual(
            {item["id"] for item in catalog["engines"]},
            {"validate_uploads", "project_topography", "project_pipeline_e0", "project_hydrology_screening", "demo_current_dataset"},
        )
        self.assertIn("C3_ESD", {item["id"] for item in catalog["products"]})
        pipeline = next(item for item in catalog["engines"] if item["id"] == "project_pipeline_e0")
        self.assertIn("C1_EMBEDDED_SCREENING", pipeline["supported_product_ids"])
        hydrology = next(item for item in catalog["engines"] if item["id"] == "project_hydrology_screening")
        self.assertEqual(hydrology["supported_product_ids"], ["PCX1_RUNOFF_SCREENING", "PCX2_HYDROGRAPH_SCREENING", "PCX3_REACH_ROUTING_SCREENING", "PCX4_SECTION_CAPACITY_SCREENING"])
        self.assertEqual(self.client.get("/api/openapi.json").status_code, 200)
        self.assertEqual(self.client.get("/api/docs").status_code, 200)


if __name__ == "__main__":
    unittest.main()
