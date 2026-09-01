import json
import tempfile
import unittest
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from pypdf import PdfReader

from scripts.generate_platform_run_dossier import (
    DossierError,
    aggregate_cf0,
    aggregate_e0,
    assert_safe_source,
    generate_dossier,
)


class PlatformRunDossierTests(unittest.TestCase):
    def test_e0_aggregation_is_length_and_segment_weighted(self):
        rows = aggregate_e0({"scenario_summary": [
            {"scenario_id": "A", "scenario_name": "A", "field_code": "1", "total_line_km": 1.0, "segment_count": 10, "coverage_proxy_percent": 90.0},
            {"scenario_id": "A", "scenario_name": "A", "field_code": "2", "total_line_km": 3.0, "segment_count": 10, "coverage_proxy_percent": 100.0},
        ]})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["field_count"], 2)
        self.assertAlmostEqual(rows[0]["mean_shot_m"], 200.0)
        self.assertAlmostEqual(rows[0]["coverage_pct"], 97.5)
        self.assertEqual(rows[0]["eligibility_status"], "NAO AVALIADA")

    def test_e0_aggregation_reports_radius_violations_and_partial_eligibility(self):
        rows = aggregate_e0({"scenario_summary": [
            {
                "scenario_id": "A", "field_code": "1", "total_line_km": 1.0, "segment_count": 10,
                "radius_violation_line_count": 0,
                "operational_continuity_status": "E0_GEOMETRY_ONLY_PENDING_FLEET_AND_MANEUVER_REVIEW",
                "continuity_blocker_codes": "",
            },
            {
                "scenario_id": "A", "field_code": "2", "total_line_km": 1.0, "segment_count": 10,
                "radius_violation_line_count": 3,
                "operational_continuity_status": "FAIL_GEOMETRY",
                "continuity_blocker_codes": "MINIMUM_RADIUS_VIOLATION",
            },
        ]})
        self.assertEqual(rows[0]["radius_violation_line_count"], 3)
        self.assertEqual(rows[0]["eligible_field_count"], 1)
        self.assertEqual(rows[0]["eligibility_status"], "PARCIALMENTE ELEGIVEL E0")

    def test_positive_guidance_claim_is_rejected(self):
        with self.assertRaises(DossierError):
            assert_safe_source({"guidance_authorized": True}, "unsafe")

    def test_positive_hydraulic_status_is_rejected(self):
        with self.assertRaises(DossierError):
            assert_safe_source({"hydraulic_status": "PASS"}, "unsafe")

    def test_cf0_geometric_eligibility_is_separated_from_diagnostics(self):
        rows = aggregate_cf0({"candidates": [
            {
                "candidate_id": "CF0A", "geometric_status": "GEOMETRIC_PASS",
                "row_count": 12, "total_length_m": 1500.0, "diagnostic_row_count": 0,
                "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
            },
            {
                "candidate_id": "CF0A", "geometric_status": "NO_FEASIBLE_FAMILY",
                "row_count": 0, "diagnostic_row_count": 9, "diagnostic_total_length_m": 750.0,
                "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
            },
        ]})
        self.assertEqual(rows[0]["eligible_block_count"], 1)
        self.assertEqual(rows[0]["eligible_row_count"], 12)
        self.assertAlmostEqual(rows[0]["eligible_total_length_km"], 1.5)
        self.assertEqual(rows[0]["diagnostic_block_count"], 1)
        self.assertEqual(rows[0]["diagnostic_row_count"], 9)
        self.assertAlmostEqual(rows[0]["diagnostic_total_length_km"], 0.75)
        self.assertEqual(rows[0]["hydraulic_status"], "HYDRAULIC_UNCONFIRMED")

    def test_minimal_subset_generates_searchable_pdf_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = {
                "project": {"id": "p1", "name": "Projeto teste", "crs": "EPSG:31982"},
                "run": {"id": "r1", "project_id": "p1", "request_id": "q1", "status": "SUCCEEDED"},
                "request": {"request_id": "q1", "project_id": "p1", "requested_delivery_level": "E0_TRIAGEM", "scope": {"field_ids": ["1"]}},
                "config": {"sulcation": {"row_spacing_m": 1.5, "minimum_radius_m": 12.0}},
            }
            paths = {}
            for name, value in values.items():
                path = root / f"{name}.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                paths[name] = path
            pdf = root / "dossier.pdf"
            manifest_path = root / "dossier.json"
            manifest = generate_dossier(
                project_json=paths["project"], run_json=paths["run"], request_json=paths["request"],
                config_json=paths["config"], output_pdf=pdf, output_manifest=manifest_path,
            )
            self.assertEqual(manifest["page_count"], 6)
            self.assertFalse(manifest["guidance_authorized"])
            self.assertEqual(manifest["preflight"]["status"], "PASS")
            self.assertEqual(manifest["pdf"]["sha256"], __import__("hashlib").sha256(pdf.read_bytes()).hexdigest())
            text = "\n".join(page.extract_text() or "" for page in PdfReader(str(pdf)).pages)
            self.assertIn("NAO USAR PARA GUIAMENTO", text)
            self.assertIn("Produto nao fornecido", text)

    def test_slope_and_conceptual_c1_add_pages_scope_and_missing_requested_products(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = {
                "project": {"id": "p1", "name": "Projeto teste", "crs": "EPSG:31982"},
                "run": {
                    "id": "r1", "project_id": "p1", "request_id": "q1", "status": "SUCCEEDED",
                    "product_ids": ["TOPOGRAPHY_E0", "C1_CURVA_EMBUTIDA", "C2_BASE_LARGA_PASSANTE", "C3_ESD", "POA_SITE"],
                },
                "request": {"request_id": "q1", "project_id": "p1", "scope": {"field_ids": []}},
                "config": {"sulcation": {"row_spacing_m": 1.5}},
                "topography": {"project_id": "p1", "release": "E0", "scope": {"field_ids": ["FIELD-A", "FIELD-B"], "field_count": 2}},
                "c1": {
                    "release": "C1_E0_CONCEPT_ALIGNMENT_NOT_DIMENSIONED",
                    "stage_status": "SCREENING_ONLY_PCE_PCX_UNCONFIRMED",
                    "variant_status": {"EMBUTIDA_TI": {
                        "pce_status": "NOT_EVALUATED", "pcx_status": "NOT_EVALUATED",
                        "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
                    }},
                    "candidates": [
                        {"candidate_id": "C1A", "axis_count": 2, "strip_count": 3, "guidance_status": "NOT_AUTHORIZED"},
                    ],
                },
            }
            paths = {}
            for name, value in values.items():
                path = root / f"{name}.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                paths[name] = path
            map_path = root / "map.png"
            plt.imsave(map_path, np.ones((40, 80, 3), dtype=float) * np.array([0.2, 0.6, 0.3]))
            pdf = root / "dossier.pdf"
            manifest_path = root / "dossier.json"
            manifest = generate_dossier(
                project_json=paths["project"], run_json=paths["run"], request_json=paths["request"],
                config_json=paths["config"], topography_manifest=paths["topography"],
                topography_map=map_path, slope_map=map_path, c1_manifest=paths["c1"], c1_map=map_path,
                output_pdf=pdf, output_manifest=manifest_path,
            )
            self.assertEqual(manifest["page_count"], 8)
            self.assertTrue(manifest["available_products"]["SLOPE_MAP"])
            self.assertTrue(manifest["available_products"]["C1_CONCEPT_NOT_DIMENSIONED"])
            self.assertEqual(manifest["scenario_counts"]["c1_concept"], 1)
            self.assertEqual(manifest["scope"]["field_ids"], ["FIELD-A", "FIELD-B"])
            self.assertEqual(manifest["scope"]["field_id_source"], "TOPOGRAPHY_MANIFEST_FALLBACK")
            self.assertEqual(
                manifest["requested_missing_products"],
                ["C2_BASE_LARGA_PASSANTE", "C3_ESD", "POA_LOGISTICS"],
            )
            sections = [page["section"] for page in manifest["pages"]]
            self.assertIn("SLOPE_E0", sections)
            self.assertIn("C1_CONCEPT", sections)
            c1_page = next(page for page in manifest["pages"] if page["section"] == "C1_CONCEPT")
            self.assertFalse(c1_page["dimensioned"])
            text = "\n".join(page.extract_text() or "" for page in PdfReader(str(pdf)).pages)
            self.assertIn("C1 curva embutida | conceito E0", text)
            self.assertIn("NAO DIMENSIONADA", text)
            self.assertIn("C2_BASE_LARGA_PASSANTE", text)
            self.assertIn("POA_LOGISTICS", text)

    def test_c1_without_explicit_conceptual_release_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payloads = {
                "project": {"id": "p1"},
                "run": {"id": "r1", "project_id": "p1", "request_id": "q1"},
                "request": {"request_id": "q1", "project_id": "p1"},
                "config": {},
                "c1": {"release": "C1_DIMENSIONED"},
            }
            paths = {}
            for name, value in payloads.items():
                path = root / f"{name}.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                paths[name] = path
            pdf, manifest = root / "out.pdf", root / "out.json"
            with self.assertRaises(DossierError):
                generate_dossier(
                    project_json=paths["project"], run_json=paths["run"], request_json=paths["request"],
                    config_json=paths["config"], c1_manifest=paths["c1"],
                    output_pdf=pdf, output_manifest=manifest,
                )
            self.assertFalse(pdf.exists())
            self.assertFalse(manifest.exists())

    def test_mismatched_run_project_is_rejected_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payloads = ({"id": "p1"}, {"id": "r1", "project_id": "other"}, {"request_id": "q1", "project_id": "p1"}, {})
            paths = []
            for index, value in enumerate(payloads):
                path = root / f"{index}.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                paths.append(path)
            pdf, manifest = root / "out.pdf", root / "out.json"
            with self.assertRaises(DossierError):
                generate_dossier(project_json=paths[0], run_json=paths[1], request_json=paths[2], config_json=paths[3], output_pdf=pdf, output_manifest=manifest)
            self.assertFalse(pdf.exists())
            self.assertFalse(manifest.exists())


if __name__ == "__main__":
    unittest.main()
