from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from terraflux_api.catalog import ENGINE_CATALOG, PRODUCTS
from terraflux_api.models import ProjectConfiguration, RunCreate
from terraflux_api.services import build_readiness
from terraflux_api.storage import LocalStore


class ProductReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = LocalStore(Path(self.temp.name))
        self.project = {
            "id": "project_test",
            "configuration": ProjectConfiguration().model_dump(),
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def add_asset(self, role: str, filename: str, extension: str) -> None:
        asset_id = f"asset_{role}_{filename}"
        self.store.insert(
            "assets",
            {
                "id": asset_id,
                "project_id": self.project["id"],
                "role": role,
                "status": "STORED",
                "original_filename": filename,
                "extension": extension,
                "created_at": "2026-08-26T00:00:00Z",
            },
        )

    def add_minimum_inputs(self) -> None:
        self.add_asset("FIELD_BOUNDARY", "talhoes.geojson", ".geojson")
        self.add_asset("POINT_CLOUD", "levantamento.laz", ".laz")

    @staticmethod
    def product(readiness: dict, product_id: str) -> dict:
        return next(item for item in readiness["products"] if item["product_id"] == product_id)

    def test_topography_only_requires_boundary_and_elevation(self) -> None:
        self.add_minimum_inputs()
        readiness = build_readiness(self.store, self.project)

        topography = self.product(readiness, "TOPOGRAPHY_E0")
        self.assertTrue(topography["client_data_generation_available"])
        self.assertEqual(topography["client_engine"], "project_topography")
        self.assertEqual(topography["blockers"], [])
        self.assertEqual(readiness["summary"]["blocking_count"], 0)
        self.assertIn("TOPOGRAPHY_E0", readiness["summary"]["available_product_ids"])

    def test_pcx1_requires_complete_explicit_event_configuration(self) -> None:
        readiness = build_readiness(self.store, self.project)
        product = self.product(readiness, "PCX1_RUNOFF_SCREENING")
        self.assertFalse(product["client_data_generation_available"])
        self.assertIn("PCX1_CONFIGURATION_NOT_ENABLED", product["blockers"])

        self.project["configuration"]["hydrology_screening"] = {
            "enabled": True,
            "method": "NRCS_CURVE_NUMBER_EVENT_SCREENING",
            "catchment_area_ha": 20.0,
            "curve_number": 82.0,
            "initial_abstraction_ratio": 0.2,
            "parameter_evidence_state": "PROJECT_EVIDENCE",
            "parameter_source_id": "soil-survey-2026",
            "rainfall_intervals": [{"duration_s": 900.0, "rainfall_mm": 25.0}],
        }
        readiness = build_readiness(self.store, self.project)
        product = self.product(readiness, "PCX1_RUNOFF_SCREENING")
        self.assertTrue(product["client_data_generation_available"])
        self.assertEqual(product["client_engine"], "project_hydrology_screening")
        self.assertEqual(product["blockers"], [])

    def test_e0_and_cf0_require_field_id_and_declared_absence_of_power_network(self) -> None:
        self.add_minimum_inputs()
        readiness = build_readiness(self.store, self.project)

        for product_id in ("SULCATION_E0", "CF0_CONTINUOUS"):
            product = self.product(readiness, product_id)
            self.assertFalse(product["client_data_generation_available"])
            self.assertIn("FIELD_ID_COLUMN_REQUIRED", product["blockers"])
            self.assertIn("POWER_NETWORK_UNRESOLVED", product["blockers"])

        self.project["configuration"]["topography"]["field_id_column"] = "field_id"
        self.project["configuration"]["constraints"]["power_network_state"] = "DECLARED_NONE"
        readiness = build_readiness(self.store, self.project)
        for product_id in ("SULCATION_E0", "CF0_CONTINUOUS"):
            product = self.product(readiness, product_id)
            self.assertTrue(product["client_data_generation_available"])
            self.assertEqual(product["client_engine"], "project_pipeline_e0")
            self.assertEqual(product["blockers"], [])

    def test_uploaded_power_network_remains_blocked_until_barrier_stage(self) -> None:
        self.add_minimum_inputs()
        self.add_asset("POWER_NETWORK", "rede.geojson", ".geojson")
        self.project["configuration"]["topography"]["field_id_column"] = "field_id"
        self.project["configuration"]["constraints"]["power_network_state"] = "UPLOADED"
        readiness = build_readiness(self.store, self.project)

        power = next(item for item in readiness["evidence"] if item["id"] == "POWER_NETWORK")
        self.assertEqual(power["status"], "AVAILABLE_PENDING_BARRIER_QA")
        for product_id in ("SULCATION_E0", "CF0_CONTINUOUS"):
            product = self.product(readiness, product_id)
            self.assertFalse(product["client_data_generation_available"])
            self.assertIn("POWER_BARRIER_STAGE_REQUIRED", product["blockers"])

    def test_scenario_products_reject_a_terrain_grid_coarser_than_their_geometry(self) -> None:
        self.add_minimum_inputs()
        config = self.project["configuration"]
        config["topography"]["field_id_column"] = "field_id"
        config["constraints"]["power_network_state"] = "DECLARED_NONE"
        config["topography"]["resolution_m"] = 3.4

        readiness = build_readiness(self.store, self.project)

        self.assertIn(
            "TERRAIN_RESOLUTION_TOO_COARSE_FOR_ROWS",
            self.product(readiness, "SULCATION_E0")["blockers"],
        )
        self.assertIn(
            "TERRAIN_RESOLUTION_TOO_COARSE_FOR_CF0",
            self.product(readiness, "CF0_CONTINUOUS")["blockers"],
        )
        self.assertIn(
            "TERRAIN_RESOLUTION_TOO_COARSE_FOR_CF0",
            self.product(readiness, "C1_EMBEDDED_SCREENING")["blockers"],
        )
        self.assertTrue(self.product(readiness, "TOPOGRAPHY_E0")["client_data_generation_available"])

        config["topography"]["resolution_m"] = 1.6
        readiness = build_readiness(self.store, self.project)
        self.assertIn(
            "TERRAIN_RESOLUTION_TOO_COARSE_FOR_CF0",
            self.product(readiness, "CF0_CONTINUOUS")["blockers"],
        )

    def test_unsupported_continuity_requests_are_explicit_blockers(self) -> None:
        self.add_minimum_inputs()
        config = self.project["configuration"]
        config["topography"]["field_id_column"] = "field_id"
        config["constraints"]["power_network_state"] = "DECLARED_NONE"
        config["sulcation"]["allow_cross_field"] = True
        config["sulcation"]["allow_cross_property"] = True
        readiness = build_readiness(self.store, self.project)

        blockers = self.product(readiness, "SULCATION_E0")["blockers"]
        self.assertIn("CROSS_FIELD_CONTINUITY_NOT_SUPPORTED", blockers)
        self.assertIn("CROSS_PROPERTY_CONTINUITY_NOT_SUPPORTED", blockers)

    def test_c1_requires_cf0_and_is_available_only_as_a_concept_stage(self) -> None:
        self.add_minimum_inputs()
        config = self.project["configuration"]
        config["topography"]["field_id_column"] = "field_id"
        config["constraints"]["power_network_state"] = "DECLARED_NONE"

        readiness = build_readiness(self.store, self.project)
        c1 = self.product(readiness, "C1_EMBEDDED_SCREENING")
        self.assertFalse(c1["client_data_generation_available"])
        self.assertIn("CF0_CONTINUOUS_DEPENDENCY_REQUIRED", c1["blockers"])
        self.assertNotIn("CLIENT_ENGINE_NOT_AVAILABLE", c1["blockers"])

        config["selected_product_ids"] = [
            "TOPOGRAPHY_E0",
            "CF0_CONTINUOUS",
            "C1_EMBEDDED_SCREENING",
        ]
        readiness = build_readiness(self.store, self.project)
        c1 = self.product(readiness, "C1_EMBEDDED_SCREENING")
        self.assertTrue(c1["client_data_generation_available"])
        self.assertEqual(c1["client_engine"], "project_pipeline_e0")
        self.assertEqual(c1["blockers"], [])

    def test_unimplemented_products_and_auto_dossier_report_honest_boundaries(self) -> None:
        self.add_minimum_inputs()
        readiness = build_readiness(self.store, self.project)
        unavailable = {"C2_BROAD_BASE", "C3_ESD", "POA_STATIC"}

        for product_id in unavailable:
            product = self.product(readiness, product_id)
            self.assertFalse(product["client_data_generation_available"])
            self.assertIn("CLIENT_ENGINE_NOT_AVAILABLE", product["blockers"])

        dossier = self.product(readiness, "COMPLETE_DOSSIER")
        self.assertFalse(dossier["client_data_generation_available"])
        self.assertTrue(dossier["auto_generated"])
        self.assertEqual(dossier["input_status"], "AUTO_GENERATED_WITH_RUN")
        self.assertNotIn("CLIENT_ENGINE_NOT_AVAILABLE", dossier["blockers"])

        expected_product_ids = {
            "TOPOGRAPHY_E0",
            "SULCATION_E0",
            "CF0_CONTINUOUS",
            "C1_EMBEDDED_SCREENING",
            "PCX1_RUNOFF_SCREENING",
            "PCX2_HYDROGRAPH_SCREENING",
            "PCX3_REACH_ROUTING_SCREENING",
            "PCX4_SECTION_CAPACITY_SCREENING",
            *unavailable,
            "COMPLETE_DOSSIER",
        }
        self.assertEqual(expected_product_ids, set(PRODUCTS))

    def test_evidence_reports_completed_e0_spatial_qa(self) -> None:
        self.add_minimum_inputs()
        for asset in self.store.list("assets"):
            self.store.update(
                "assets",
                asset["id"],
                {"spatial_qa_status": "PASSED_E0_ENGINE"},
            )
        readiness = build_readiness(self.store, self.project)
        statuses = {item["id"]: item["status"] for item in readiness["evidence"]}
        self.assertEqual(statuses["FIELD_BOUNDARY"], "PASSED_E0_ENGINE")
        self.assertEqual(statuses["ELEVATION_SOURCE"], "PASSED_E0_ENGINE")

    def test_pipeline_engine_is_part_of_the_strict_public_contract(self) -> None:
        self.assertIn("project_pipeline_e0", ENGINE_CATALOG)
        parsed = RunCreate(engine_id="project_pipeline_e0", product_ids=["SULCATION_E0"])
        self.assertEqual(parsed.engine_id, "project_pipeline_e0")


if __name__ == "__main__":
    unittest.main()
