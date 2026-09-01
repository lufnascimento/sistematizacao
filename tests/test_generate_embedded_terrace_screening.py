import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from shapely.geometry import LineString, box

from scripts.generate_embedded_terrace_screening import (
    ContractError,
    DEFAULT_SCREENING_REQUEST,
    axis_profile_metrics,
    td_not_generated_record,
    validate_screening_request,
    validate_staged_geopackage,
    write_geopackage,
)


class EmbeddedTerraceScreeningRunnerTests(unittest.TestCase):
    def test_committed_screening_request_is_explicitly_non_agronomic(self):
        payload = json.loads(DEFAULT_SCREENING_REQUEST.read_text(encoding="utf-8"))
        policy = validate_screening_request(payload)
        self.assertEqual(policy.vertical_interval_candidates_m, (2.0, 4.0, 6.0))
        self.assertFalse(payload["assumption_contract"]["pce_spacing_claim"])
        self.assertEqual(
            payload["assumption_contract"]["interval_role"],
            "ELEVATION_ISOLINE_SAMPLING_NOT_PCE_SPACING",
        )
        self.assertEqual(payload["assumption_contract"]["vertical_accuracy_status"], "NOT_VALIDATED")
        self.assertIsNone(payload["variant_policy"]["receiver_dataset_ref"])

    def test_dataset_legibility_rule_rejects_a_different_numeric_grid(self):
        payload = json.loads(DEFAULT_SCREENING_REQUEST.read_text(encoding="utf-8"))
        payload["screening_parameters"]["vertical_interval_candidates_m"] = [2.0, 3.0, 4.0]
        with self.assertRaisesRegex(ContractError, "2/4/6 m geometric sampling grid"):
            validate_screening_request(payload)

    def test_td_without_receiver_materializes_status_but_no_geometry(self):
        record, summary = td_not_generated_record("FIELD-A")
        self.assertEqual(record["status"], "NOT_GENERATED_RECEIVER_MISSING")
        self.assertEqual(record["geometry_count"], 0)
        self.assertIn("TD_RECEIVER_MISSING", record["blocker_codes"])
        self.assertEqual(summary["guidance_status"], "NOT_AUTHORIZED")

    def test_axis_profile_reports_elevation_residual_and_grade(self):
        result = axis_profile_metrics(
            distances=[0.0, 5.0, 10.0],
            elevations=[100.0, 100.1, 100.0],
            target_elevation_m=100.0,
        )
        self.assertAlmostEqual(result["elevation_residual_p95_m"], 0.09, places=6)
        self.assertAlmostEqual(result["grade_max_pct"], 2.0, places=6)

    def test_staged_geopackage_preserves_fail_closed_line_statuses(self):
        blockers = (
            "C1_E0_CONCEPT_ALIGNMENT_NOT_DIMENSIONED;PCE_SOLVER_NOT_RUN;"
            "PCX_SOLVER_NOT_RUN;ROW_CANDIDATES_DERIVED_FROM_CF0C_NOT_RESOLVED_PER_STRIP"
        )
        axis = {
            "terrace_id": "C:AX1", "candidate_id": "C", "field_id": "F", "variant": "EMBUTIDA_TI",
            "vertical_interval_m": 5.0, "offset_fraction": 0.0, "level_index": 20,
            "target_elevation_m": 100.0, "length_m": 10.0, "elevation_residual_p95_m": 0.0,
            "grade_p95_pct": 0.0, "grade_max_pct": 0.0, "endpoint_status": "SUPPORTED_PHYSICAL_BOUNDARY",
            "axis_status": "SCREENING_ONLY_NOT_DIMENSIONED", "pce_status": "NOT_EVALUATED",
            "pcx_status": "NOT_EVALUATED", "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
            "guidance_status": "NOT_AUTHORIZED", "blocker_codes": blockers,
            "geometry": LineString([(0.0, 5.0, 100.0), (10.0, 5.0, 100.0)]),
        }
        strip = {
            "strip_id": "C:ST1", "candidate_id": "C", "field_id": "F", "variant": "EMBUTIDA_TI",
            "vertical_interval_m": 5.0, "offset_fraction": 0.0, "area_ha": 0.01,
            "partition_status": "TOPOLOGY_ONLY_NOT_SECTION", "section_status": "NOT_EVALUATED",
            "blocker_codes": blockers, "geometry": box(0.0, 0.0, 10.0, 4.75),
        }
        row = {
            "segment_id": "C:RW1", "candidate_id": "C", "field_id": "F", "strip_id": "C:ST1",
            "source_row_id": "R1", "source_family_id": "CF0C:F", "source_candidate_id": "CF0C_OPERACAO",
            "length_m": 4.0, "diagnostic_status": "NOT_APPROVED", "guidance_status": "NOT_AUTHORIZED",
            "hydraulic_status": "HYDRAULIC_UNCONFIRMED", "blocker_codes": blockers,
            "geometry": LineString([(2.0, 0.0, 99.0), (2.0, 4.0, 99.0)]),
        }
        summary = {
            "candidate_id": "C", "field_id": "F", "variant": "EMBUTIDA_TI", "vertical_interval_m": 5.0,
            "offset_fraction": 0.0, "screening_status": "GEOMETRIC_PRECURSOR", "axis_count": 1,
            "strip_count": 1, "diagnostic_row_segment_count": 1, "terrace_total_length_m": 10.0,
            "diagnostic_total_length_m": 4.0, "diagnostic_length_p50_m": 4.0,
            "diagnostic_length_p95_m": 4.0, "split_source_row_count": 0, "pce_status": "NOT_EVALUATED",
            "pcx_status": "NOT_EVALUATED", "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
            "construction_status": "NOT_EVALUATED", "operational_status": "DIAGNOSTIC_ONLY_NOT_ROUTED",
            "guidance_status": "NOT_AUTHORIZED", "blocker_codes": blockers,
        }
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "screening.gpkg"
            write_geopackage(
                path,
                'PROJCS["WGS 84 / UTM zone 22S",GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],PARAMETER["latitude_of_origin",0],PARAMETER["central_meridian",-51],PARAMETER["scale_factor",0.9996],PARAMETER["false_easting",500000],PARAMETER["false_northing",10000000],UNIT["metre",1],AUTHORITY["EPSG","32722"]]',
                [axis], [strip], [row], [summary],
            )
            qa = validate_staged_geopackage(
                path,
                {"terrace_alignment_candidates": 1, "interterrace_strips": 1, "row_candidates": 1, "candidate_summary": 1},
            )
            self.assertEqual(qa["invalid_geometry_count"], 0)
            self.assertEqual(qa["hydraulic_pass_claim_count"], 0)
            self.assertEqual(qa["guidance_authorized_claim_count"], 0)


if __name__ == "__main__":
    unittest.main()
