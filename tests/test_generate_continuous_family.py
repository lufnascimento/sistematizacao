import unittest
import warnings
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
from osgeo import ogr, osr
from shapely.geometry import LineString, MultiPolygon, Polygon

try:
    from generate_continuous_family import (
        Cf0Policy,
        assemble_connected_work_blocks,
        complete_phase_offset_attempts,
        extend_line_to_physical_boundary,
        final_normal_ray_spacing,
        finite_solver_gradients,
        make_solver_policy,
        north_up_to_solver_grid,
        phase_offset_invariant_blockers,
        pruned_phase_offset_attempt,
        publish_outputs,
        select_phase_offset_attempt,
        solve_mask_connectivity_qa,
        smooth_line_c2,
        solver_to_north_up_grid,
        validate_staged_geopackage,
        write_geopackage,
    )
except ModuleNotFoundError:
    from scripts.generate_continuous_family import (
        Cf0Policy,
        assemble_connected_work_blocks,
        complete_phase_offset_attempts,
        extend_line_to_physical_boundary,
        final_normal_ray_spacing,
        finite_solver_gradients,
        make_solver_policy,
        north_up_to_solver_grid,
        phase_offset_invariant_blockers,
        pruned_phase_offset_attempt,
        publish_outputs,
        select_phase_offset_attempt,
        solve_mask_connectivity_qa,
        smooth_line_c2,
        solver_to_north_up_grid,
        validate_staged_geopackage,
        write_geopackage,
    )


class ContinuousFamilyRunnerTests(unittest.TestCase):
    @staticmethod
    def phase_attempt(
        fraction,
        *,
        status="GEOMETRIC_PASS",
        blockers=None,
        minimum_radius=10.0,
        spacing_outside=0.01,
        spacing_p95=0.02,
        coverage=1.0,
        spline_failures=0,
    ):
        return {
            "phase_offset_fraction": float(fraction),
            "phase_offset_m": float(fraction) * 1.5,
            "geometric_status": status,
            "blocker_codes": list(blockers or []),
            "metric_lines": [{}],
            "total_length_m": 100.0,
            "minimum_radius_order_value": minimum_radius,
            "metrics": {
                "minimum_radius_m": minimum_radius,
                "radius_status": "PASS",
                "spacing_error_p95_m": spacing_p95,
                "coverage_proxy_ratio": coverage,
                "internal_endpoint_count": 0,
                "intersection_count": 0,
                "self_intersection_count": 0,
                "loop_count": 0,
                "solver_gate_metrics": {
                    "final_spacing_outside_tolerance_fraction": spacing_outside,
                    "spline_failure_count": spline_failures,
                },
            },
        }

    def test_phase_offset_selection_filters_failures_before_lexicographic_order(self):
        failed_with_large_radius = self.phase_attempt(
            0.0,
            status="NO_FEASIBLE_FAMILY",
            blockers=["ROW_SPLINE_FIT_FAILED"],
            minimum_radius=999.0,
            spline_failures=1,
        )
        passing = self.phase_attempt(0.25, minimum_radius=8.0)

        selected, record = select_phase_offset_attempt(
            [failed_with_large_radius, passing], Cf0Policy()
        )

        self.assertIs(selected, passing)
        self.assertEqual(record["selection_status"], "SELECTED_GEOMETRIC_PASS")
        self.assertEqual(record["selected_phase_offset_fraction"], 0.25)
        self.assertEqual(len(record["attempted_offsets"]), 2)

    def test_phase_offset_selection_is_reproducible_and_lexicographic(self):
        attempts = [
            self.phase_attempt(0.0, minimum_radius=8.0, spacing_outside=0.001),
            self.phase_attempt(0.25, minimum_radius=12.0, spacing_outside=0.02),
            self.phase_attempt(0.5, minimum_radius=12.0, spacing_outside=0.01),
            self.phase_attempt(0.75, minimum_radius=12.0, spacing_outside=0.01),
        ]

        first, first_record = select_phase_offset_attempt(attempts, Cf0Policy())
        second, second_record = select_phase_offset_attempt(attempts, Cf0Policy())

        self.assertEqual(first["phase_offset_fraction"], 0.5)
        self.assertEqual(second["phase_offset_fraction"], 0.5)
        self.assertEqual(first_record, second_record)

    def test_no_feasible_phase_offset_materializes_zero_as_diagnostic_only(self):
        attempts = [
            self.phase_attempt(
                fraction,
                status="NO_FEASIBLE_FAMILY",
                blockers=["MINIMUM_WORK_PATH_RADIUS_VIOLATION"],
            )
            for fraction in (0.0, 0.25, 0.5, 0.75)
        ]

        diagnostic, record = select_phase_offset_attempt(attempts, Cf0Policy())

        self.assertEqual(diagnostic["phase_offset_fraction"], 0.0)
        self.assertEqual(record["selection_status"], "NO_FEASIBLE_PHASE_OFFSET")
        self.assertEqual(record["phase_offset_role"], "DIAGNOSTIC_ONLY")
        self.assertIsNone(record["selected_phase_offset_m"])
        self.assertEqual(record["diagnostic_phase_offset_fraction"], 0.0)

    def test_r00895_regression_selects_passing_quarter_spacing_phase(self):
        attempts = [
            self.phase_attempt(
                0.0,
                status="NO_FEASIBLE_FAMILY",
                blockers=["ROW_SPLINE_FIT_FAILED"],
                minimum_radius=None,
                spline_failures=1,
            ),
            self.phase_attempt(0.25, minimum_radius=0.3740),
            self.phase_attempt(0.5, minimum_radius=0.2222),
            self.phase_attempt(0.75, minimum_radius=0.06728),
        ]

        selected, record = select_phase_offset_attempt(attempts, Cf0Policy())

        self.assertEqual(selected["phase_offset_fraction"], 0.25)
        self.assertEqual(record["selected_phase_offset_m"], 0.375)
        self.assertEqual(
            record["attempted_offsets"][0]["blocker_codes"],
            ["ROW_SPLINE_FIT_FAILED"],
        )

    def test_invariant_solver_gate_prunes_only_offsets_that_cannot_recover(self):
        policy = Cf0Policy()
        passing_inputs = phase_offset_invariant_blockers(
            {"blocker_codes": []},
            {"gradient_estimation_status": "PASS"},
            solver_spacing_error_m=0.05,
            solver_spacing_outside_fraction=0.01,
            eikonal_p95=0.05,
            integrability_p95=0.05,
            orientation_p95_deg=5.0,
            row_spacing_m=1.5,
            policy=policy,
        )
        blocked_inputs = phase_offset_invariant_blockers(
            {"blocker_codes": ["NONINTEGRABLE_ORIENTATION_RESIDUAL"]},
            {"gradient_estimation_status": "PASS"},
            solver_spacing_error_m=0.05,
            solver_spacing_outside_fraction=0.01,
            eikonal_p95=0.05,
            integrability_p95=0.05,
            orientation_p95_deg=5.0,
            row_spacing_m=1.5,
            policy=policy,
        )

        self.assertEqual(passing_inputs, [])
        self.assertEqual(blocked_inputs, ["NONINTEGRABLE_ORIENTATION_RESIDUAL"])

    def test_pruned_offsets_keep_four_trial_contract_and_zero_diagnostic_selected(self):
        diagnostic = self.phase_attempt(
            0.0,
            status="NO_FEASIBLE_FAMILY",
            blockers=["NONINTEGRABLE_ORIENTATION_RESIDUAL"],
            minimum_radius=None,
        )
        attempts = [diagnostic]
        attempts.extend(
            pruned_phase_offset_attempt(
                fraction,
                row_spacing_m=1.5,
                minimum_radius_m=12.0,
                invariant_blockers=["NONINTEGRABLE_ORIENTATION_RESIDUAL"],
                extraction_qa={"gradient_estimation_status": "PASS"},
            )
            for fraction in (0.25, 0.5, 0.75)
        )

        selected, record = select_phase_offset_attempt(attempts, Cf0Policy())

        self.assertIs(selected, diagnostic)
        self.assertEqual(record["selection_status"], "NO_FEASIBLE_PHASE_OFFSET")
        self.assertEqual(len(record["attempted_offsets"]), 4)
        for trial in record["attempted_offsets"][1:]:
            self.assertEqual(trial["extracted_row_count"], 0)
            self.assertEqual(trial["radius_status"], "FAIL")
            self.assertIn(
                "PHASE_OFFSET_PRUNED_BY_INVARIANT_GATE",
                trial["blocker_codes"],
            )

    def test_invariant_gate_skips_all_expensive_nonzero_offset_evaluations(self):
        diagnostic = self.phase_attempt(
            0.0,
            status="NO_FEASIBLE_FAMILY",
            blockers=["EIKONAL_RESIDUAL"],
            minimum_radius=None,
        )
        evaluated = []

        attempts = complete_phase_offset_attempts(
            diagnostic,
            policy=Cf0Policy(),
            row_spacing_m=1.5,
            minimum_radius_m=None,
            invariant_blockers=["EIKONAL_RESIDUAL"],
            extraction_qa={"gradient_estimation_status": "PASS"},
            evaluator=lambda fraction: evaluated.append(fraction),
        )

        self.assertEqual(evaluated, [])
        self.assertEqual(
            [attempt["phase_offset_fraction"] for attempt in attempts],
            [0.0, 0.25, 0.5, 0.75],
        )

        recoverable_evaluated = []
        recoverable_attempts = complete_phase_offset_attempts(
            diagnostic,
            policy=Cf0Policy(),
            row_spacing_m=1.5,
            minimum_radius_m=None,
            invariant_blockers=[],
            extraction_qa={"gradient_estimation_status": "PASS"},
            evaluator=lambda fraction: (
                recoverable_evaluated.append(fraction) or self.phase_attempt(fraction)
            ),
        )
        self.assertEqual(recoverable_evaluated, [0.25, 0.5, 0.75])
        self.assertEqual(len(recoverable_attempts), 4)

    def test_north_up_conversion_makes_row_index_world_y_increasing(self):
        north_up_world_y = np.asarray([[30.0], [20.0], [10.0]])
        (solver_world_y,) = north_up_to_solver_grid(north_up_world_y)
        self.assertTrue(np.all(np.diff(solver_world_y[:, 0]) > 0))
        (round_trip,) = solver_to_north_up_grid(solver_world_y)
        np.testing.assert_array_equal(round_trip, north_up_world_y)

    def test_nan_outside_solver_mask_becomes_neutral_finite_sentinel(self):
        mask = np.asarray([[False, False], [True, True]])
        gx = np.asarray([[np.nan, np.nan], [0.2, 0.3]])
        gy = np.asarray([[np.nan, np.nan], [0.4, 0.5]])
        clean_x, clean_y = finite_solver_gradients(mask, gx, gy)
        self.assertTrue(np.isfinite(clean_x).all() and np.isfinite(clean_y).all())
        np.testing.assert_array_equal(clean_x[~mask], 0.0)
        np.testing.assert_array_equal(clean_y[~mask], 0.0)

    def test_nan_inside_solver_mask_is_rejected(self):
        mask = np.asarray([[False, False], [True, True]])
        gx = np.asarray([[np.nan, np.nan], [np.nan, 0.3]])
        gy = np.asarray([[np.nan, np.nan], [0.4, 0.5]])
        with self.assertRaisesRegex(ValueError, "inside the work mask"):
            finite_solver_gradients(mask, gx, gy)

    def test_disconnected_solve_mask_fails_closed(self):
        mask = np.asarray(
            [
                [True, True, False, False],
                [True, True, False, True],
                [False, False, False, True],
            ]
        )

        qa = solve_mask_connectivity_qa(mask)

        self.assertEqual(qa["solve_mask_component_count"], 2)
        self.assertEqual(qa["solve_mask_required_component_count"], 1)
        self.assertEqual(qa["solve_mask_component_connectivity"], 4)
        self.assertEqual(qa["status"], "FAIL_CLOSED")
        self.assertEqual(
            qa["blocker_codes"], ["WORK_BLOCK_SOLVE_MASK_DISCONNECTED"]
        )

    def test_diagonal_contact_is_not_a_four_connected_work_block(self):
        qa = solve_mask_connectivity_qa(
            np.asarray([[True, False], [False, True]])
        )
        self.assertEqual(qa["solve_mask_component_count"], 2)
        self.assertEqual(qa["status"], "FAIL_CLOSED")

    def test_endpoint_extension_reaches_first_physical_boundary(self):
        usable = Polygon(
            [(0, 0), (10, 0), (10, 10), (0, 10)],
            holes=[[(4, 4), (6, 4), (6, 6), (4, 6)]],
        )
        line = LineString([(0.8, 5.0), (2.0, 5.0), (3.5, 5.0)])
        extended, supported = extend_line_to_physical_boundary(
            line,
            usable,
            maximum_extension_m=2.0,
            snap_tolerance_m=0.01,
        )
        self.assertTrue(supported)
        self.assertAlmostEqual(extended.coords[0][0], 0.0, places=8)
        self.assertAlmostEqual(extended.coords[-1][0], 4.0, places=8)
        self.assertLessEqual(usable.boundary.distance(extended.boundary), 1e-8)

    def test_endpoint_extension_fails_closed_when_tangent_misses(self):
        usable = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
        line = LineString([(1.0, 4.0), (1.0, 5.0)])

        extended, supported = extend_line_to_physical_boundary(
            line,
            usable,
            maximum_extension_m=2.0,
            snap_tolerance_m=0.01,
        )

        self.assertFalse(supported)
        self.assertEqual(extended, line)

    def test_c2_spline_preserves_endpoints_and_deviation_budget(self):
        usable = Polygon([(0, 0), (30, 0), (30, 20), (0, 20)])
        line = LineString([(0, 5), (6, 5.4), (12, 6.5), (18, 8.0), (24, 9.0), (30, 9.2)])
        smoothed, qa = smooth_line_c2(
            line,
            usable,
            sample_step_m=1.0,
            maximum_deviation_m=0.20,
            endpoint_tolerance_m=0.01,
        )
        self.assertIsNotNone(smoothed)
        self.assertEqual(qa["status"], "PASS_C2_PARAMETRIC_SPLINE")
        self.assertLessEqual(qa["maximum_deviation_m"], 0.20 + 1e-9)
        self.assertEqual(smoothed.coords[0], line.coords[0])
        self.assertEqual(smoothed.coords[-1], line.coords[-1])
        self.assertGreater(qa["minimum_radius_m"], 0.0)
        self.assertLessEqual(qa["minimum_radius_m"], qa["analytic_minimum_radius_m"])
        if qa["sampled_minimum_radius_m"] is not None:
            self.assertLessEqual(qa["minimum_radius_m"], qa["sampled_minimum_radius_m"])
        self.assertEqual(qa["input_vertex_count"], len(line.coords))
        self.assertLessEqual(
            qa["representation_chord_error_m"],
            qa["representation_tolerance_m"] + 1e-12,
        )

    def test_c2_spline_runtime_warning_fails_closed(self):
        usable = Polygon([(0, 0), (30, 0), (30, 20), (0, 20)])
        line = LineString([(0, 5), (6, 5.4), (12, 6.5), (18, 8.0), (24, 9.0), (30, 9.2)])
        module = import_module(smooth_line_c2.__module__)

        def warned_fit(*_args, **_kwargs):
            warnings.warn("FITPACK did not converge", RuntimeWarning)

        with patch.object(module, "splprep", side_effect=warned_fit):
            smoothed, qa = smooth_line_c2(
                line,
                usable,
                sample_step_m=1.0,
                maximum_deviation_m=0.20,
                endpoint_tolerance_m=0.01,
            )

        self.assertIsNone(smoothed)
        self.assertEqual(qa["status"], "FAIL_C2_SPLINE_TOLERANCE_OR_DOMAIN")
        self.assertEqual(qa["blocker_code"], "ROW_SPLINE_FIT_FAILED")

    def test_final_spacing_is_measured_on_local_normal_rays(self):
        records = []
        for level, y in enumerate((2.0, 3.5, 5.0, 6.5)):
            records.append(
                {
                    "level_id": level,
                    "geometry": LineString([(0.0, y), (20.0, y)]),
                }
            )
        qa = final_normal_ray_spacing(records, 1.5, 0.20)
        self.assertGreater(qa["sample_count"], 0)
        self.assertEqual(qa["unpaired_sample_count"], 0)
        self.assertAlmostEqual(qa["spacing_error_p95_m"], 0.0, places=8)
        self.assertAlmostEqual(qa["outside_tolerance_percent"], 0.0, places=8)

    def test_isolated_phase_level_counts_as_unpaired(self):
        records = [{"level_id": 4, "geometry": LineString([(0.0, 2.0), (20.0, 2.0)])}]
        qa = final_normal_ray_spacing(records, 1.5, 0.20)
        self.assertEqual(qa["sample_count"], 0)
        self.assertGreater(qa["unpaired_sample_count"], 0)
        self.assertAlmostEqual(qa["outside_tolerance_percent"], 100.0, places=8)

    def test_each_connected_component_becomes_its_own_work_block(self):
        west = Polygon([(0, 0), (4, 0), (4, 4), (0, 4)])
        east = Polygon([(10, 0), (13, 0), (13, 3), (10, 3)])
        blocks, excluded, filter_contract = assemble_connected_work_blocks(
            [
                {
                    "code": "42",
                    "usable": MultiPolygon([east, west]),
                    "usable_area_ha": (west.area + east.area) / 10_000,
                }
            ],
            policy=Cf0Policy(grid_resolution_m=1.0),
            row_spacing_m=1.0,
        )
        self.assertEqual([block["work_block_id"] for block in blocks], ["WB_42_C001", "WB_42_C002"])
        self.assertEqual([block["usable"].bounds for block in blocks], [west.bounds, east.bounds])
        self.assertEqual(excluded, [])
        self.assertEqual(filter_contract["effective_minimum_area_m2"], 9.0)
        self.assertEqual(filter_contract["effective_minimum_width_m"], 2.0)

    def test_work_block_filter_excludes_area_and_width_slivers_without_renumbering(self):
        viable = Polygon([(0, 0), (20, 0), (20, 20), (0, 20)])
        narrow = Polygon([(30, 0), (50, 0), (50, 1), (30, 1)])
        tiny = Polygon([(60, 0), (62, 0), (62, 2), (60, 2)])
        blocks, excluded, contract = assemble_connected_work_blocks(
            [
                {
                    "code": "7",
                    "usable": MultiPolygon([tiny, narrow, viable]),
                    "usable_area_ha": (viable.area + narrow.area + tiny.area) / 10_000,
                }
            ],
            policy=Cf0Policy(grid_resolution_m=1.0),
            row_spacing_m=1.0,
        )
        self.assertEqual([block["work_block_id"] for block in blocks], ["WB_7_C001"])
        self.assertEqual(
            [(item["component_index"], item["reason_codes"]) for item in excluded],
            [
                (2, ["WIDTH_BELOW_CF0_WORK_BLOCK_MINIMUM"]),
                (3, ["AREA_BELOW_CF0_WORK_BLOCK_MINIMUM"]),
            ],
        )
        self.assertEqual(contract["revision"], "CF0_WORK_BLOCK_SUPPORT_V1")

    def test_effective_solver_thresholds_are_mapped_and_random_seed_is_absent(self):
        policy = Cf0Policy(
            maximum_low_coherence_fraction=0.07,
            maximum_abrupt_edge_fraction=0.03,
            maximum_cycle_conflict_fraction=0.01,
            minimum_terrain_gradient=0.002,
            maximum_critical_fraction=0.04,
            maximum_cut_locus_fraction=0.06,
        )
        solver_policy = make_solver_policy(policy, 1.5, 0.55, 18.0)
        self.assertAlmostEqual(solver_policy.maximum_low_coherence_fraction, 0.07)
        self.assertAlmostEqual(solver_policy.maximum_abrupt_edge_fraction, 0.03)
        self.assertAlmostEqual(solver_policy.maximum_cycle_conflict_fraction, 0.01)
        self.assertAlmostEqual(solver_policy.minimum_terrain_gradient, 0.002)
        self.assertAlmostEqual(solver_policy.maximum_critical_fraction, 0.04)
        self.assertAlmostEqual(solver_policy.maximum_cut_locus_fraction, 0.06)
        self.assertNotIn("random_seed", Cf0Policy.__dataclass_fields__)

    def test_diagnostic_rows_are_persisted_as_xyz_not_approved(self):
        spatial_ref = osr.SpatialReference()
        spatial_ref.ImportFromEPSG(31982)
        spatial_ref.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        diagnostic = {
            "row_id": "R1",
            "family_id": "CF0A_CONSERVACAO:WB_7_C001",
            "candidate_id": "CF0A_CONSERVACAO",
            "field_id": "7",
            "work_block_id": "WB_7_C001",
            "row_index": 1,
            "phase_level_index": -4,
            "phase_level_m": 10.0,
            "phase_offset_m": 0.375,
            "phase_offset_fraction": 0.25,
            "length_m": 10.0,
            "geometry_status": "NO_FEASIBLE_FAMILY",
            "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
            "topology_status": "BLOCKED",
            "diagnostic_status": "NOT_APPROVED",
            "guidance_status": "NOT_AUTHORIZED",
            "blocker_codes": ["SYNTHETIC_BLOCKER"],
            "geometry": LineString([(0.0, 0.0, 100.0), (10.0, 0.0, 99.0)]),
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostic.gpkg"
            write_geopackage(path, spatial_ref.ExportToWkt(), [], [diagnostic], [], [])
            qa = validate_staged_geopackage(
                path,
                {
                    "continuous_rows": 0,
                    "diagnostic_rows": 1,
                    "family_summary": 0,
                    "hydraulic_precheck": 0,
                },
            )
            self.assertEqual(qa["invalid_geometry_count"], 0)
            source = ogr.Open(str(path))
            layer = source.GetLayerByName("diagnostic_rows")
            feature = layer.GetNextFeature()
            self.assertEqual(feature["diagnostic_status"], "NOT_APPROVED")
            self.assertEqual(feature["guidance_status"], "NOT_AUTHORIZED")
            self.assertAlmostEqual(feature["phase_offset_m"], 0.375)
            self.assertAlmostEqual(feature["phase_offset_fraction"], 0.25)
            self.assertEqual(feature["phase_level_index"], -4)
            self.assertEqual(feature.GetGeometryRef().GetCoordinateDimension(), 3)
            source = None

    def test_publish_removes_only_stale_cf0_managed_rasters(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            staged = root / "staged"
            staged_rasters = staged / "rasters"
            output_rasters = root / "output" / "rasters"
            staged_rasters.mkdir(parents=True)
            output_rasters.mkdir(parents=True)

            staged_gpkg = staged / "new.gpkg"
            staged_map = staged / "new.png"
            staged_manifest = staged / "new.json"
            staged_gpkg.write_text("gpkg", encoding="ascii")
            staged_map.write_text("map", encoding="ascii")
            staged_manifest.write_text("manifest", encoding="ascii")
            current_name = "cf0a__field__wb_c001__phase.tif"
            (staged_rasters / current_name).write_text("current", encoding="ascii")
            stale_name = "cf0a__field__wb_c002__phase.tif"
            (output_rasters / stale_name).write_text("stale", encoding="ascii")
            unrelated_name = "customer_orthomosaic.tif"
            (output_rasters / unrelated_name).write_text("keep", encoding="ascii")

            publish_outputs(
                staged_gpkg=staged_gpkg,
                staged_map=staged_map,
                staged_manifest=staged_manifest,
                staged_raster_dir=staged_rasters,
                output_gpkg=root / "output" / "package.gpkg",
                output_map=root / "output" / "map.png",
                output_manifest=root / "output" / "manifest.json",
                output_raster_dir=output_rasters,
            )

            self.assertTrue((output_rasters / current_name).is_file())
            self.assertFalse((output_rasters / stale_name).exists())
            self.assertTrue((output_rasters / unrelated_name).is_file())


if __name__ == "__main__":
    unittest.main()
