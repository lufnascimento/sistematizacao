from __future__ import annotations

import copy
import hashlib
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from terraflux_api.scenario_results import (
    ScenarioResultError,
    aggregate_e0_scenarios,
    c1_artifact_records,
    cf0_artifact_records,
    summarize_c1_screening,
    summarize_cf0_candidates,
    validate_c1_result,
    validate_cf0_result,
    validate_e0_result,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def e0_row(scenario_id: str, field: str, *, line_km: float, segments: int, loss: tuple[float, float, float]) -> dict:
    return {
        "scenario_id": scenario_id,
        "scenario_name": scenario_id,
        "selection_strategy": "balanced",
        "field_code": field,
        "release_level": "E0_topographic_screening",
        "hydraulic_status": "not_evaluated_missing_soil_rainfall_receivers_and_structures",
        "gross_area_ha": 12.0,
        "usable_area_ha": 10.0,
        "total_line_km": line_km,
        "segment_count": segments,
        "fragmentation_count": 2,
        "segment_length_p90_m": 300.0,
        "cross_grade_p95_weighted_percent": 8.0,
        "absolute_grade_p95_weighted_percent": 3.0,
        "coverage_proxy_percent": 98.0,
        "candidate_conservation_loss": loss[0],
        "candidate_harvestability_loss": loss[1],
        "candidate_performance_loss": loss[2],
    }


def e0_manifest() -> dict:
    return {
        "status": "E0_topographic_sulcation_geometry_screening",
        "source_files_unchanged": True,
        "maximum_output_delivery_level": "E0_TRIAGEM",
        "requested_level_achieved": True,
        "not_authorized_for": ["machine guidance", "hydraulic approval"],
        "generation_request": {
            "mode": "VALIDATED_PROJECT_REQUEST",
            "project_id": "project-1",
            "request_id": "request-1",
            "request_sha256": SHA_A,
        },
        "scenario_definitions": [
            {"id": "E0A", "name": "Conservacao", "selection": "conservation"},
            {"id": "E0B", "name": "Operacao", "selection": "performance"},
        ],
        "scenario_summary": [
            e0_row("E0A", "F1", line_km=2.0, segments=10, loss=(0.1, 0.4, 0.5)),
            e0_row("E0A", "F2", line_km=3.0, segments=15, loss=(0.1, 0.4, 0.5)),
            e0_row("E0B", "F1", line_km=4.0, segments=10, loss=(0.4, 0.1, 0.0)),
            e0_row("E0B", "F2", line_km=6.0, segments=15, loss=(0.4, 0.1, 0.0)),
        ],
        "output_integrity": {
            "geopackage": {"path": "out/e0.gpkg", "size_bytes": 10, "sha256": SHA_B},
            "map": {"path": "out/e0.png", "size_bytes": 20, "sha256": SHA_C},
        },
    }


def cf0_candidate(candidate: str, field: str, block: str, status: str) -> dict:
    passed = status == "GEOMETRIC_PASS"
    return {
        "candidate_id": candidate,
        "field_id": field,
        "work_block_id": block,
        "geometric_status": status,
        "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
        "row_count": 12 if passed else 0,
        "total_length_m": 1200.0 if passed else 0.0,
        "blocker_codes": [] if passed else ["NO_FEASIBLE_GEOMETRY"],
    }


def cf0_manifest() -> dict:
    return {
        "schema_version": "1.2.0",
        "manifest_type": "CONTINUOUS_FAMILY_STAGE_RESULT",
        "release": "CF0_GEOMETRIC_SCREENING",
        "stage_status": "HYDRAULIC_UNCONFIRMED",
        "release_limitations": ["CF0_NOT_FOR_GUIDANCE", "HYDRAULIC_UNCONFIRMED"],
        "project_request_ref": {"id": "request-1", "sha256": SHA_A},
        "candidates": [
            cf0_candidate("CF0A", "F1", "B1", "GEOMETRIC_PASS"),
            cf0_candidate("CF0A", "F2", "B2", "NO_FEASIBLE_FAMILY"),
            cf0_candidate("CF0B", "F1", "B1", "GEOMETRIC_PASS"),
        ],
        "qa": {
            "candidate_field_count": 3,
            "geometric_pass_count": 2,
            "no_feasible_family_count": 1,
            "hydraulic_unconfirmed_count": 3,
        },
        "outputs": {
            "geopackage": {"path": "out/cf0.gpkg", "size_bytes": 100, "sha256": SHA_B, "role": "CF0_VECTOR_PACKAGE"},
            "map": {"path": "out/cf0.png", "size_bytes": 200, "sha256": SHA_C, "role": "CF0_COMPARATIVE_MAP"},
            "rasters": [
                {
                    "path": "out/phase.tif",
                    "size_bytes": 300,
                    "sha256": SHA_A,
                    "candidate_id": "CF0A",
                    "field_id": "F1",
                    "work_block_id": "B1",
                    "raster_role": "phase",
                }
            ],
        },
    }


def c1_candidate(field: str, interval: float, offset: float) -> dict:
    interval_id = f"{interval:.3f}".rstrip("0").rstrip(".").replace(".", "P")
    offset_id = f"{offset:.2f}".replace(".", "P")
    return {
        "candidate_id": f"C1E0_TI_{field}_VI{interval_id}_O{offset_id}",
        "field_id": field,
        "variant": "EMBUTIDA_TI",
        "vertical_interval_m": interval,
        "offset_fraction": offset,
        "screening_status": "GEOMETRIC_PRECURSOR",
        "eligibility_status": "GEOMETRIC_PRECURSOR",
        "axis_count": 2,
        "strip_count": 3,
        "diagnostic_row_segment_count": 4,
        "partition_qa": {
            "method": "AXIS_BUFFER_TOPOLOGY_GAP_NOT_CROSS_SECTION",
            "topology_gap_half_width_m": 0.25,
            "topology_gap_area_m2": 10.0,
            "retained_strip_count": 3,
            "excluded_small_component_count": 0,
            "excluded_small_component_area_m2": 0.0,
            "partitioned_area_m2": 10_000.0,
            "scope_note": "Numerical partition only.",
        },
        "metrics": {
            "terrace_total_length_m": 500.0,
            "topology_gap_area_m2": 10.0,
            "partitioned_area_m2": 10_000.0,
            "source_cf0c_row_count": 4,
            "source_cf0c_total_length_m": 800.0,
            "diagnostic_segment_count": 4,
            "diagnostic_total_length_m": 700.0,
            "diagnostic_length_p05_m": 50.0,
            "diagnostic_length_p50_m": 100.0,
            "diagnostic_length_p95_m": 250.0,
            "diagnostic_length_max_m": 300.0,
            "split_source_row_count": 2,
        },
        "pce_status": "NOT_EVALUATED",
        "pcx_status": "NOT_EVALUATED",
        "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
        "construction_status": "NOT_EVALUATED",
        "operational_status": "DIAGNOSTIC_ONLY_NOT_ROUTED",
        "guidance_status": "NOT_AUTHORIZED",
        "blocker_codes": [
            "C1_E0_CONCEPT_ALIGNMENT_NOT_DIMENSIONED",
            "EMBEDDED_SECTION_NOT_APPLIED",
            "GUIDANCE_NOT_AUTHORIZED",
            "PCE_SOLVER_NOT_RUN",
            "PCX_SOLVER_NOT_RUN",
            "PROPOSED_DTM_NOT_GENERATED",
            "ROW_CANDIDATES_DERIVED_FROM_CF0C_NOT_RESOLVED_PER_STRIP",
            "TI_HYDRAULIC_FUNCTION_NOT_CONFIRMED",
            "VERTICAL_ACCURACY_NOT_VALIDATED",
        ],
    }


def c1_manifest() -> dict:
    fields = ("F1", "F2")
    intervals = (2.0, 4.0, 6.0)
    offsets = (0.0, 0.25, 0.5, 0.75)
    candidates = [
        c1_candidate(field, interval, offset)
        for field in fields
        for interval in intervals
        for offset in offsets
    ]
    td = [
        {
            "candidate_id": f"C1E0_TD_{field}_NOT_GENERATED",
            "field_id": field,
            "variant": "EMBUTIDA_TD",
            "status": "NOT_GENERATED_RECEIVER_MISSING",
            "geometry_count": 0,
            "blocker_codes": [
                "TD_RECEIVER_MISSING",
                "TD_GRADE_RULE_MISSING",
                "VERTICAL_ACCURACY_NOT_VALIDATED",
                "PCE_SOLVER_NOT_RUN",
                "PCX_SOLVER_NOT_RUN",
                "EMBEDDED_SECTION_NOT_APPLIED",
                "PROPOSED_DTM_NOT_GENERATED",
                "GUIDANCE_NOT_AUTHORIZED",
            ],
        }
        for field in fields
    ]
    wkt = 'PROJCS["Synthetic projected CRS"]'
    return {
        "schema_version": "1.0.0",
        "manifest_type": "EMBEDDED_TERRACE_SCREENING_STAGE_RESULT",
        "release": "C1_E0_CONCEPT_ALIGNMENT_NOT_DIMENSIONED",
        "generated_at": "2026-08-31T12:00:00Z",
        "project_request_ref": {"id": "request-1", "path": "request.json", "sha256": SHA_A},
        "sensitivity_request_ref": {"id": "sensitivity-1", "path": "sensitivity.json", "sha256": SHA_B},
        "stage_status": "SCREENING_ONLY_PCE_PCX_UNCONFIRMED",
        "crs": {
            "type": "PROJECTED",
            "authority": "EPSG",
            "code": 31982,
            "horizontal_unit": "m",
            "wkt": wkt,
            "wkt_sha256": hashlib.sha256(wkt.encode("utf-8")).hexdigest(),
        },
        "inputs": {
            "terrain_dtm": {"path": "dtm.tif", "size_bytes": 1, "sha256": SHA_A, "role": "DTM_E0_SCREENING"},
            "field_boundary": {"path": "fields.gpkg", "size_bytes": 2, "sha256": SHA_B, "role": "FIELD_BOUNDARY"},
            "cf0_manifest": {"path": "cf0.json", "size_bytes": 3, "sha256": SHA_C, "role": "CF0_STAGE_MANIFEST"},
            "cf0_geopackage": {"path": "cf0.gpkg", "size_bytes": 4, "sha256": SHA_A, "role": "CF0_VECTOR_SOURCE"},
        },
        "assumptions": {
            "parameter_class": "E0_ASSUMPTION",
            "selection_meaning": "GEOMETRIC_SENSITIVITY_GRID_ONLY",
            "selection_rule": "DATASET_RELIEF_LEGIBILITY_GRID_V1",
            "interval_role": "ELEVATION_ISOLINE_SAMPLING_NOT_PCE_SPACING",
            "vertical_accuracy_status": "NOT_VALIDATED",
            "vertical_interval_candidates_m": list(intervals),
            "offset_fractions": list(offsets),
            "topology_gap_half_width_m": 0.25,
            "topology_gap_role": "NUMERICAL_PARTITION_SUPPORT_NOT_SECTION_WIDTH",
            "row_source_candidate_id": "CF0C_OPERACAO",
            "row_source_method": "CLIP_EXISTING_CF0C_ROWS_BY_TOPOLOGY_ONLY_INTERTERRACE_STRIPS",
        },
        "variant_status": {
            "EMBUTIDA_TI": {
                "status": "GENERATED_SCREENING_ONLY_NOT_DIMENSIONED",
                "geometry_role": "TERRAIN_ISOLINE_SENSITIVITY_NOT_APPROVED_TI",
                "pce_status": "NOT_EVALUATED",
                "pcx_status": "NOT_EVALUATED",
                "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
            },
            "EMBUTIDA_TD": {
                "status": "NOT_GENERATED_RECEIVER_MISSING",
                "reason": "VERIFIED_RECEIVER_AND_LONGITUDINAL_GRADE_RULE_REQUIRED",
                "geometry_count": 0,
                "pce_status": "NOT_EVALUATED",
                "pcx_status": "NOT_EVALUATED",
                "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
            },
        },
        "release_limitations": [
            "NOT_A_C1_PROJECT",
            "NOT_PCE_SPACING",
            "NOT_PCX_DIMENSIONING",
            "NOT_TI_APPROVAL",
            "TD_NOT_GENERATED",
            "EMBEDDED_SECTION_NOT_APPLIED",
            "NO_PROPOSED_DTM_OR_EARTHWORK",
            "ROWS_ARE_CF0C_DIAGNOSTIC_CLIPS_NOT_STRIP_SOLUTIONS",
            "NOT_FOR_GUIDANCE",
            "VERTICAL_ACCURACY_NOT_VALIDATED",
            "FIELD_AND_PROFESSIONAL_VALIDATION_REQUIRED",
        ],
        "candidates": candidates,
        "td_not_generated": td,
        "qa": {
            "field_count": 2,
            "ti_candidate_count": 24,
            "td_not_generated_count": 2,
            "axis_count": 48,
            "strip_count": 72,
            "diagnostic_row_segment_count": 96,
            "invalid_geometry_count": 0,
            "non_3d_line_count": 0,
            "forbidden_td_geometry_count": 0,
            "hydraulic_pass_claim_count": 0,
            "guidance_authorized_claim_count": 0,
            "general_constraint_inventory_status": "NOT_REVIEWED",
            "power_inventory_status": "DECLARED_NONE",
            "power_constraint_effect": "NOT_APPLICABLE_DECLARED_NONE",
        },
        "layer_counts": {
            "terrace_alignment_candidates": 48,
            "interterrace_strips": 72,
            "row_candidates": 96,
            "candidate_summary": 26,
        },
        "outputs": {
            "geopackage": {"path": "c1.gpkg", "size_bytes": 5, "sha256": SHA_B, "role": "C1_E0_SCREENING_VECTOR_PACKAGE"},
            "map": {"path": "c1.png", "size_bytes": 6, "sha256": SHA_C, "role": "C1_E0_SCREENING_MAP"},
        },
    }


class ScenarioResultTests(unittest.TestCase):
    def test_e0_validation_and_aggregation_are_lineage_bound(self) -> None:
        manifest = e0_manifest()
        validated = validate_e0_result(
            manifest,
            expected_project_id="project-1",
            expected_request_id="request-1",
            expected_request_sha256=SHA_A,
        )
        self.assertEqual(validated["status"], "E0_topographic_sulcation_geometry_screening")

        records = aggregate_e0_scenarios(
            manifest,
            run_id="run-1",
            project_id="project-1",
            objective_weights={"conservation": 1, "harvestability": 1, "performance": 3},
        )
        self.assertEqual(["E0A", "E0B"], [item["code"] for item in records])
        e0a, e0b = records
        self.assertEqual(e0a["metrics"]["average_shot_m"], 200.0)
        self.assertIsNone(e0a["metrics"]["p95_shot_m"])
        self.assertIsNone(e0a["metrics"]["maneuvers_per_ha"])
        self.assertEqual(e0a["metrics"]["conservation_score"], 90.0)
        self.assertTrue(e0b["recommended"])
        self.assertFalse(e0b["guidance_authorized"])
        self.assertIn("NOT_AUTHORIZED", e0b["status"])

    def test_e0_rejects_approval_and_lineage_drift(self) -> None:
        approved = e0_manifest()
        approved["guidance_authorized"] = True
        with self.assertRaisesRegex(ScenarioResultError, "cannot authorize"):
            validate_e0_result(approved)
        with self.assertRaisesRegex(ScenarioResultError, "request SHA-256 differs"):
            validate_e0_result(e0_manifest(), expected_request_sha256=SHA_B)

    def test_e0_rejects_unknown_hydraulic_release_and_duplicates(self) -> None:
        hydraulic = e0_manifest()
        hydraulic["scenario_summary"][0]["hydraulic_status"] = "HYDRAULIC_PASS"
        with self.assertRaisesRegex(ScenarioResultError, "hydraulic approval claim"):
            validate_e0_result(hydraulic)
        duplicate = e0_manifest()
        duplicate["scenario_summary"].append(copy.deepcopy(duplicate["scenario_summary"][0]))
        with self.assertRaisesRegex(ScenarioResultError, "duplicate E0 summary"):
            validate_e0_result(duplicate)

    def test_e0_diagnostic_geometry_with_hard_blockers_is_never_recommended(self) -> None:
        manifest = e0_manifest()
        for row in manifest["scenario_summary"]:
            if row["scenario_id"] == "E0B":
                row["continuity_blocker_codes"] = "MINIMUM_RADIUS_VIOLATION"
                row["radius_violation_line_count"] = 2
                row["internal_endpoint_count"] = 0

        records = aggregate_e0_scenarios(
            manifest,
            run_id="run-1",
            project_id="project-1",
            objective_weights={"conservation": 1, "harvestability": 1, "performance": 10},
        )

        e0a, e0b = records
        self.assertTrue(e0a["recommended"])
        self.assertTrue(e0a["geometry_eligible"])
        self.assertFalse(e0b["recommended"])
        self.assertFalse(e0b["geometry_eligible"])
        self.assertEqual(e0b["metrics"]["radius_violation_line_count"], 4)
        self.assertIn("DIAGNOSTIC_ONLY", e0b["status"])

    def test_cf0_summary_keeps_partial_candidate_fail_closed(self) -> None:
        manifest = cf0_manifest()
        validate_cf0_result(manifest, expected_request_id="request-1", expected_request_sha256=SHA_A)
        summaries = summarize_cf0_candidates(manifest, run_id="run-1", project_id="project-1")
        self.assertEqual(2, len(summaries))
        self.assertEqual("CF0_PARTIAL_GEOMETRIC_SCREENING", summaries[0]["status"])
        self.assertEqual(1, summaries[0]["metrics"]["geometric_pass_count"])
        self.assertEqual(1.2, summaries[0]["metrics"]["total_line_km"])
        self.assertFalse(summaries[0]["recommended"])
        self.assertFalse(summaries[0]["guidance_authorized"])
        self.assertEqual("HYDRAULIC_UNCONFIRMED", summaries[0]["hydraulic_status"])

    def test_cf0_artifacts_are_normalized_without_previewing_geotiff(self) -> None:
        artifacts = cf0_artifact_records(cf0_manifest())
        self.assertEqual(3, len(artifacts))
        self.assertEqual("CF0_VECTOR_PACKAGE", artifacts[0]["artifact_type"])
        self.assertTrue(artifacts[1]["previewable"])
        self.assertEqual("CF0_DIAGNOSTIC_RASTER", artifacts[2]["artifact_type"])
        self.assertFalse(artifacts[2]["previewable"])
        self.assertEqual("phase", artifacts[2]["role"])

    def test_cf0_rejects_hydraulic_claim_and_failed_rows(self) -> None:
        approved = cf0_manifest()
        approved["candidates"][0]["hydraulic_status"] = "HYDRAULIC_PASS"
        with self.assertRaisesRegex(ScenarioResultError, "cannot claim hydraulic approval"):
            validate_cf0_result(approved)
        released_failure = cf0_manifest()
        released_failure["candidates"][1]["row_count"] = 1
        with self.assertRaisesRegex(ScenarioResultError, "failed but exposes released rows"):
            validate_cf0_result(released_failure)

    def test_cf0_no_feasible_family_is_a_publishable_fail_closed_result(self) -> None:
        manifest = cf0_manifest()
        manifest["stage_status"] = "NO_FEASIBLE_FAMILY"
        manifest["candidates"] = [
            cf0_candidate("CF0A", "F1", "B1", "NO_FEASIBLE_FAMILY"),
            cf0_candidate("CF0B", "F1", "B1", "NO_FEASIBLE_FAMILY"),
        ]
        manifest["qa"] = {
            "candidate_field_count": 2,
            "geometric_pass_count": 0,
            "no_feasible_family_count": 2,
            "hydraulic_unconfirmed_count": 2,
        }

        validate_cf0_result(manifest)
        summaries = summarize_cf0_candidates(
            manifest,
            run_id="run-1",
            project_id="project-1",
        )

        self.assertEqual(2, len(summaries))
        self.assertTrue(all(item["status"] == "CF0_NO_FEASIBLE_FAMILY" for item in summaries))
        self.assertTrue(all(item["recommended"] is False for item in summaries))

    def test_cf0_rejects_qa_counts_that_disagree_with_candidates(self) -> None:
        manifest = cf0_manifest()
        manifest["qa"]["geometric_pass_count"] = 99
        with self.assertRaisesRegex(ScenarioResultError, "qa.geometric_pass_count is inconsistent"):
            validate_cf0_result(manifest)

    def test_c1_complete_matrix_is_concept_only_and_td_remains_blocked(self) -> None:
        manifest = c1_manifest()
        validated = validate_c1_result(
            manifest,
            expected_project_request_id="request-1",
            expected_project_request_sha256=SHA_A,
            expected_sensitivity_request_id="sensitivity-1",
            expected_sensitivity_request_sha256=SHA_B,
            expected_cf0_manifest_sha256=SHA_C,
            expected_cf0_geopackage_sha256=SHA_A,
        )
        self.assertEqual(24, validated["qa"]["ti_candidate_count"])

        records = summarize_c1_screening(manifest, run_id="run-1", project_id="project-1")
        ti = [item for item in records if item["variant"] == "EMBUTIDA_TI"]
        td = [item for item in records if item["variant"] == "EMBUTIDA_TD"]
        self.assertEqual(24, len(ti))
        self.assertEqual(2, len(td))
        self.assertTrue(all(item["status"] == "CONCEPT_ONLY" for item in ti))
        self.assertTrue(all(item["status"] == "BLOCKED" for item in td))
        self.assertTrue(all(item["release_level"] == "CONCEPT_ONLY" for item in records))
        self.assertTrue(all(item["recommended"] is False for item in records))
        self.assertTrue(all(item["guidance_authorized"] is False for item in records))
        self.assertTrue(all(item["pce_status"] == "NOT_EVALUATED" for item in records))
        self.assertTrue(all(item["pcx_status"] == "NOT_EVALUATED" for item in records))
        self.assertTrue(all(item["hydraulic_evaluation_status"] == "NOT_EVALUATED" for item in records))
        self.assertIsNone(ti[0]["metrics"]["pce_spacing_m"])
        self.assertIsNone(ti[0]["metrics"]["pcx_section"])
        self.assertIsNone(ti[0]["metrics"]["hydraulic_capacity"])

    def test_c1_rejects_incomplete_matrix_and_release_promotion(self) -> None:
        incomplete = c1_manifest()
        incomplete["candidates"].pop()
        with self.assertRaisesRegex(ScenarioResultError, "sensitivity matrix is incomplete"):
            validate_c1_result(incomplete)

        hydraulic = c1_manifest()
        hydraulic["candidates"][0]["hydraulic_status"] = "HYDRAULIC_PASS"
        with self.assertRaisesRegex(ScenarioResultError, "hydraulic_status must be HYDRAULIC_UNCONFIRMED"):
            validate_c1_result(hydraulic)

        td_geometry = c1_manifest()
        td_geometry["td_not_generated"][0]["geometry_count"] = 1
        with self.assertRaisesRegex(ScenarioResultError, "forbidden TD geometry"):
            validate_c1_result(td_geometry)

    def test_c1_rejects_qa_and_cf0_dependency_drift(self) -> None:
        drifted = c1_manifest()
        with self.assertRaisesRegex(ScenarioResultError, "CF0 manifest SHA-256 differs"):
            validate_c1_result(drifted, expected_cf0_manifest_sha256=SHA_A)

        qa_drift = c1_manifest()
        qa_drift["qa"]["axis_count"] = 47
        with self.assertRaisesRegex(ScenarioResultError, "qa.axis_count is inconsistent"):
            validate_c1_result(qa_drift)

    def test_c1_artifacts_remain_concept_only(self) -> None:
        artifacts = c1_artifact_records(c1_manifest())
        self.assertEqual(2, len(artifacts))
        self.assertEqual("C1_CONCEPT_VECTOR_PACKAGE", artifacts[0]["artifact_type"])
        self.assertFalse(artifacts[0]["previewable"])
        self.assertTrue(artifacts[1]["previewable"])
        self.assertTrue(all(item["delivery_level"] == "CONCEPT_ONLY" for item in artifacts))
        self.assertTrue(all(item["guidance_authorized"] is False for item in artifacts))


if __name__ == "__main__":
    unittest.main()
