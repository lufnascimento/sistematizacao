import unittest

from scripts.generate_c1_screening_report import (
    ReportError,
    assert_reportable_source,
    build_page_plan,
    candidate_diagnostics,
)


def candidate(field_id="FIELD-A", interval=5.0, offset=0.0):
    return {
        "candidate_id": f"C1E0_TI_{field_id}_VI{interval}_O{offset}",
        "field_id": field_id,
        "variant": "EMBUTIDA_TI",
        "vertical_interval_m": interval,
        "offset_fraction": offset,
        "pce_status": "NOT_EVALUATED",
        "pcx_status": "NOT_EVALUATED",
        "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
        "guidance_status": "NOT_AUTHORIZED",
        "metrics": {
            "source_cf0c_row_count": 10,
            "diagnostic_segment_count": 16,
            "source_cf0c_total_length_m": 1000.0,
            "diagnostic_total_length_m": 800.0,
            "split_source_row_count": 4,
        },
    }


def manifest(candidates=None):
    values = candidates if candidates is not None else [candidate()]
    return {
        "release": "C1_E0_CONCEPT_ALIGNMENT_NOT_DIMENSIONED",
        "stage_status": "SCREENING_ONLY_PCE_PCX_UNCONFIRMED",
        "assumptions": {
            "parameter_class": "E0_ASSUMPTION",
            "selection_meaning": "GEOMETRIC_SENSITIVITY_GRID_ONLY",
            "row_source_candidate_id": "CF0C_OPERACAO",
            "vertical_interval_candidates_m": sorted({float(item["vertical_interval_m"]) for item in values}) or [5.0],
            "offset_fractions": sorted({float(item["offset_fraction"]) for item in values}) or [0.0],
        },
        "variant_status": {
            "EMBUTIDA_TI": {
                "status": "GENERATED_SCREENING_ONLY_NOT_DIMENSIONED",
                "pce_status": "NOT_EVALUATED",
                "pcx_status": "NOT_EVALUATED",
                "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
            },
            "EMBUTIDA_TD": {
                "status": "NOT_GENERATED_RECEIVER_MISSING",
                "geometry_count": 0,
                "pce_status": "NOT_EVALUATED",
                "pcx_status": "NOT_EVALUATED",
                "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
            },
        },
        "qa": {
            "invalid_geometry_count": 0,
            "non_3d_line_count": 0,
            "forbidden_td_geometry_count": 0,
            "hydraulic_pass_claim_count": 0,
            "guidance_authorized_claim_count": 0,
            "power_inventory_status": "DECLARED_NONE",
            "power_constraint_effect": "NOT_APPLICABLE_DECLARED_NONE",
        },
        "candidates": values,
    }


class C1ScreeningReportGeneratorTests(unittest.TestCase):
    def test_candidate_diagnostics_are_explicit_ratios(self):
        result = candidate_diagnostics(candidate())
        self.assertAlmostEqual(result["fragmentation_factor"], 1.6)
        self.assertAlmostEqual(result["retained_length_pct"], 80.0)
        self.assertAlmostEqual(result["split_source_row_pct"], 40.0)

    def test_page_plan_covers_every_interval_and_offset(self):
        candidates = [
            candidate(field_id, interval, offset)
            for field_id in ("FIELD-A", "FIELD-B")
            for interval in (5.0, 10.0)
            for offset in (0.0, 0.25, 0.5, 0.75, 0.9)
        ]
        pages = build_page_plan(manifest(candidates), candidates_per_page=7, offsets_per_page=4)
        self.assertEqual([item["page"] for item in pages], list(range(1, len(pages) + 1)))
        map_pages = [item for item in pages if item["kind"] == "interval_maps"]
        self.assertEqual(len(map_pages), 4)
        covered = {(item["vertical_interval_m"], offset) for item in map_pages for offset in item["offsets"]}
        self.assertEqual(covered, {(interval, offset) for interval in (5.0, 10.0) for offset in (0.0, 0.25, 0.5, 0.75, 0.9)})
        table_ids = [candidate_id for item in pages if item["kind"] == "candidate_table" for candidate_id in item["candidate_ids"]]
        self.assertEqual(set(table_ids), {item["candidate_id"] for item in candidates})

    def test_source_boundary_rejects_hydraulic_promotion(self):
        value = manifest()
        value["variant_status"]["EMBUTIDA_TI"]["hydraulic_status"] = "PASS"
        with self.assertRaises(ReportError):
            assert_reportable_source(value)

    def test_declared_none_power_must_remain_non_blocking(self):
        value = manifest()
        value["qa"]["power_constraint_effect"] = "PENDING_NOT_REVIEWED"
        with self.assertRaises(ReportError):
            assert_reportable_source(value)


if __name__ == "__main__":
    unittest.main()
