import math
import unittest

import numpy as np
from shapely import affinity
from shapely.geometry import LineString, Point, box
from shapely.ops import linemerge, unary_union

from scripts.continuous_family import (
    ContinuousFamilyPolicy,
    _contour_segment_coordinates_by_level,
    _phase_edge_target,
    _phase_matrix,
    _lines_from_merged,
    _snap_segment_to_local_grid,
    _triangle_segment,
    axial_from_angles,
    curvature_radius_diagnostics,
    extrapolate_phase_halo,
    extract_phase_contours,
    lift_axial_normal,
    phase_contours,
    project_integrable_phase,
    solve_continuous_phase,
)


def _spaced_level_values(phase, level_mask, spacing_m, phase_offset_m):
    minimum = float(np.min(phase[level_mask]))
    maximum = float(np.max(phase[level_mask]))
    tolerance = max(
        spacing_m * 1e-5,
        max(1.0, abs(minimum), abs(maximum)) * 1e-10,
    )
    first = math.ceil((minimum + tolerance - phase_offset_m) / spacing_m)
    last = math.floor((maximum - tolerance - phase_offset_m) / spacing_m)
    return (
        np.arange(first, last + 1, dtype=float) * spacing_m + phase_offset_m,
        np.arange(first, last + 1, dtype=int),
    )


def _scalar_contour_reference(
    phase,
    mask,
    xs,
    ys,
    level_values,
    level_ids,
    minimum_length_m=0.0,
):
    """Former scalar traversal retained only as an equivalence oracle."""

    phase = np.asarray(phase, dtype=float)
    usable = np.asarray(mask, dtype=bool) & np.isfinite(phase)
    x_coordinates = np.asarray(xs, dtype=float)
    y_coordinates = np.asarray(ys, dtype=float)
    coordinate_step = min(
        float(np.min(np.diff(x_coordinates))),
        float(np.min(np.diff(y_coordinates))),
    )
    snap_tolerance = max(coordinate_step * 1e-5, 1e-10)
    cell_valid = (
        usable[:-1, :-1]
        & usable[:-1, 1:]
        & usable[1:, 1:]
        & usable[1:, :-1]
    )
    cell_minimum = np.minimum.reduce(
        (
            phase[:-1, :-1],
            phase[:-1, 1:],
            phase[1:, 1:],
            phase[1:, :-1],
        )
    )
    cell_maximum = np.maximum.reduce(
        (
            phase[:-1, :-1],
            phase[:-1, 1:],
            phase[1:, 1:],
            phase[1:, :-1],
        )
    )
    results = []
    for level_id, level_value in zip(level_ids, level_values):
        segments = []
        candidate_cells = np.argwhere(
            cell_valid
            & (cell_minimum <= float(level_value))
            & (cell_maximum >= float(level_value))
        )
        for row, column in candidate_cells:
            row, column = int(row), int(column)
            corners = (
                (row, column),
                (row, column + 1),
                (row + 1, column + 1),
                (row + 1, column),
            )
            points = tuple(
                (float(x_coordinates[column_index]), float(y_coordinates[row_index]))
                for row_index, column_index in corners
            )
            values = tuple(float(phase[index]) for index in corners)
            for triangle in ((0, 1, 2), (0, 2, 3)):
                segment = _triangle_segment(
                    tuple(points[index] for index in triangle),
                    tuple(values[index] for index in triangle),
                    float(level_value),
                )
                if segment is None:
                    continue
                snapped = _snap_segment_to_local_grid(
                    segment,
                    float(x_coordinates[0]),
                    float(y_coordinates[0]),
                    snap_tolerance,
                )
                if snapped is not None:
                    segments.append(snapped)
        if not segments:
            continue
        merged_input = unary_union(segments)
        try:
            merged = linemerge(merged_input)
        except ValueError:
            merged = merged_input
        parts = sorted(
            _lines_from_merged(merged),
            key=lambda line: (
                round(line.bounds[0], 9),
                round(line.bounds[1], 9),
                round(line.bounds[2], 9),
                round(line.bounds[3], 9),
                round(line.length, 9),
            ),
        )
        for segment_index, line in enumerate(parts):
            if line.length + 1e-9 < minimum_length_m:
                continue
            results.append(
                {
                    "level_id": int(level_id),
                    "phase_level_m": float(level_value),
                    "segment_index": int(segment_index),
                    "geometry": line,
                    "length_m": float(line.length),
                }
            )
    return results


class ContinuousFamilyTests(unittest.TestCase):
    def policy(self, **overrides):
        parameters = {
            "grid_resolution_m": 1.0,
            "row_spacing_m": 4.0,
            "orientation_smoothing_m": 0.0,
            "minimum_contour_length_m": 1.0,
            "maximum_integrability_residual_rms": 0.20,
            "maximum_integrability_angular_error_p95_deg": 25.0,
            "maximum_cut_locus_fraction": 0.10,
        }
        parameters.update(overrides)
        return ContinuousFamilyPolicy(**parameters)

    def assert_contours_equal_exactly(self, expected, actual):
        self.assertEqual(len(expected), len(actual))
        for expected_record, actual_record in zip(expected, actual):
            self.assertEqual(expected_record["level_id"], actual_record["level_id"])
            self.assertEqual(
                expected_record["phase_level_m"], actual_record["phase_level_m"]
            )
            self.assertEqual(
                expected_record["segment_index"], actual_record["segment_index"]
            )
            self.assertAlmostEqual(
                expected_record["length_m"], actual_record["length_m"], places=12
            )
            self.assertTrue(
                expected_record["geometry"].equals_exact(
                    actual_record["geometry"], 1e-12
                ),
                (
                    expected_record["geometry"].wkt,
                    actual_record["geometry"].wkt,
                ),
            )

    def test_theta_plus_180_has_identical_axial_encoding(self):
        angles = np.asarray([0.0, 17.5, 89.0, 143.25])
        first = axial_from_angles(angles, degrees=True)
        second = axial_from_angles(angles + 180.0, degrees=True)
        np.testing.assert_allclose(first[0], second[0], atol=1e-14)
        np.testing.assert_allclose(first[1], second[1], atol=1e-14)

    def test_vectorized_phase_topology_preserves_scalar_edge_order_and_matrix(self):
        mask = np.asarray(
            [
                [True, True, False, True],
                [True, True, False, True],
                [False, True, True, False],
            ],
            dtype=bool,
        )
        resolution = 1.25
        matrix, node_ids, edge_a, edge_b, axes, anchors = _phase_matrix(
            mask, resolution
        )

        expected_a = []
        expected_b = []
        expected_axes = []
        for row, column in np.argwhere(mask):
            row, column = int(row), int(column)
            if column + 1 < mask.shape[1] and mask[row, column + 1]:
                expected_a.append((row, column))
                expected_b.append((row, column + 1))
                expected_axes.append(0)
            if row + 1 < mask.shape[0] and mask[row + 1, column]:
                expected_a.append((row, column))
                expected_b.append((row + 1, column))
                expected_axes.append(1)

        np.testing.assert_array_equal(edge_a, np.asarray(expected_a))
        np.testing.assert_array_equal(edge_b, np.asarray(expected_b))
        np.testing.assert_array_equal(axes, np.asarray(expected_axes))
        dense = matrix.toarray()
        inverse_resolution = 1.0 / resolution
        for equation, (first, second) in enumerate(zip(expected_a, expected_b)):
            self.assertEqual(dense[equation, node_ids[first]], -inverse_resolution)
            self.assertEqual(dense[equation, node_ids[second]], inverse_resolution)
            self.assertEqual(np.count_nonzero(dense[equation]), 2)
        self.assertEqual(anchors, [(0, 0), (0, 3)])

    def test_vectorized_phase_edge_target_matches_scalar_reference(self):
        mask = np.asarray(
            [[True, True, True], [True, False, True], [True, True, True]],
            dtype=bool,
        )
        _, _, edge_a, edge_b, axes, _ = _phase_matrix(mask, 1.0)
        rows, columns = np.indices(mask.shape)
        scale = 0.7 + 0.1 * rows + 0.03 * columns
        nx = 0.2 + 0.05 * rows - 0.02 * columns
        ny = -0.4 + 0.01 * rows + 0.04 * columns
        expected = []
        for index, (first, second) in enumerate(zip(edge_a, edge_b)):
            first = tuple(first)
            second = tuple(second)
            component = nx if axes[index] == 0 else ny
            expected.append(
                0.5
                * (
                    scale[first] * component[first]
                    + scale[second] * component[second]
                )
            )
        np.testing.assert_array_equal(
            _phase_edge_target(scale, nx, ny, edge_a, edge_b, axes),
            np.asarray(expected),
        )

    def test_contour_coordinates_are_translation_invariant(self):
        xs = np.linspace(0.0, 30.0, 31)
        ys = np.linspace(0.0, 20.0, 21)
        x_grid, y_grid = np.meshgrid(xs, ys)
        phase = 0.6 * x_grid + 0.8 * y_grid
        mask = np.ones(phase.shape, dtype=bool)
        original = phase_contours(phase, mask, xs, ys, 4.0, 1.0)
        translated = phase_contours(phase, mask, xs + 1234.5, ys - 987.0, 4.0, 1.0)
        self.assertEqual(len(original), len(translated))
        for first, second in zip(original, translated):
            expected = affinity.translate(first["geometry"], xoff=1234.5, yoff=-987.0)
            self.assertLess(expected.hausdorff_distance(second["geometry"]), 1e-8)
            self.assertEqual(first["level_id"], second["level_id"])

    def test_phase_halo_extrapolates_without_changing_solved_level_range(self):
        rows, columns = np.indices((9, 11))
        phase = columns.astype(float)
        mask = np.zeros(phase.shape, dtype=bool)
        mask[2:7, 2:9] = True
        phase[~mask] = np.nan

        extended, support, qa = extrapolate_phase_halo(phase, mask, 1.0, 1)

        self.assertEqual(
            qa["method"], "FIRST_ORDER_LOCAL_LSQ_GRADIENT_FAIL_CLOSED"
        )
        self.assertEqual(qa["halo_cells"], 1)
        self.assertEqual(qa["gradient_estimation_status"], "PASS")
        self.assertEqual(qa["unsupported_halo_cell_count"], 0)
        self.assertTrue(np.isfinite(extended[support]).all())
        np.testing.assert_allclose(extended[2:7, 1], 1.0)
        np.testing.assert_allclose(extended[2:7, 9], 9.0)
        contours = extract_phase_contours(
            extended,
            support,
            np.arange(phase.shape[1], dtype=float),
            np.arange(phase.shape[0], dtype=float),
            spacing_m=2.0,
            minimum_length_m=0.0,
            level_range_mask=mask,
        )
        self.assertEqual(sorted({item["phase_level_m"] for item in contours}), [4.0, 6.0])

    def test_phase_halo_uses_rank_two_lsq_at_a_narrow_tip(self):
        rows, columns = np.indices((8, 8))
        truth = 0.7 * columns - 0.4 * rows + 3.0
        mask = np.zeros(truth.shape, dtype=bool)
        mask[2:6, 2:6] = True
        mask[1, 3] = True
        phase = np.where(mask, truth, np.nan)

        extended, support, qa = extrapolate_phase_halo(
            phase, mask, 1.0, 1, gradient_lsq_max_radius_cells=2
        )

        self.assertEqual(qa["gradient_estimation_status"], "PASS")
        self.assertGreater(qa["missing_gradient_component_count"], 0)
        self.assertGreater(qa["lsq_gradient_source_count"], 0)
        self.assertEqual(qa["unestimable_gradient_source_count"], 0)
        self.assertTrue(support[0, 3])
        self.assertAlmostEqual(extended[0, 3], truth[0, 3], places=10)
        self.assertLessEqual(qa["gradient_lsq_residual_rms_max"], 1e-12)
        self.assertLessEqual(
            qa["gradient_lsq_relative_residual_max"],
            qa["gradient_lsq_max_relative_residual"],
        )
        self.assertLessEqual(
            qa["gradient_lsq_condition_number_max"],
            qa["gradient_lsq_max_condition_number"],
        )

    def test_phase_halo_fails_closed_for_rank_two_oscillatory_support(self):
        rows, columns = np.indices((8, 8))
        mask = np.zeros((8, 8), dtype=bool)
        mask[2:6, 2:6] = True
        mask[1, 3] = True
        phase = np.where(
            mask,
            100.0 * ((rows + columns) % 2) + 0.7 * columns - 0.4 * rows + 3.0,
            np.nan,
        )

        _, permissive_support, permissive_qa = extrapolate_phase_halo(
            phase,
            mask,
            1.0,
            1,
            gradient_lsq_max_radius_cells=2,
            gradient_lsq_max_relative_residual=10.0,
        )
        extended, support, qa = extrapolate_phase_halo(
            phase,
            mask,
            1.0,
            1,
            gradient_lsq_max_radius_cells=2,
            gradient_lsq_max_relative_residual=0.30,
        )

        self.assertEqual(permissive_qa["gradient_estimation_status"], "PASS")
        self.assertEqual(permissive_qa["lsq_gradient_source_count"], 1)
        self.assertGreater(
            permissive_qa["gradient_lsq_relative_residual_max"], 0.30
        )
        self.assertLessEqual(
            permissive_qa["gradient_lsq_condition_number_max"], 100.0
        )
        self.assertTrue(permissive_support[0, 3])
        self.assertEqual(qa["gradient_estimation_status"], "FAIL_CLOSED")
        self.assertGreater(qa["unestimable_gradient_source_count"], 0)
        self.assertGreater(qa["unsupported_halo_cell_count"], 0)
        self.assertFalse(support[0, 3])
        self.assertTrue(np.isnan(extended[0, 3]))

    def test_phase_halo_relative_residual_uses_resolution_floor(self):
        rows, columns = np.indices((8, 8))
        mask = np.zeros((8, 8), dtype=bool)
        mask[2:6, 2:6] = True
        mask[1, 3] = True
        phase = np.where(
            mask,
            0.1 * ((rows + columns) % 2)
            + 0.007 * columns
            - 0.004 * rows,
            np.nan,
        )

        _, _, qa = extrapolate_phase_halo(
            phase,
            mask,
            1.0,
            1,
            gradient_lsq_max_radius_cells=2,
            gradient_lsq_max_relative_residual=1.0,
        )

        neighbor_rows = np.asarray([2, 2, 2])
        neighbor_columns = np.asarray([2, 3, 4])
        response = (
            phase[neighbor_rows, neighbor_columns] - phase[1, 3]
        )
        response_rms = float(np.sqrt(np.mean(response * response)))
        self.assertLess(response_rms, 1.0)
        self.assertEqual(qa["lsq_gradient_source_count"], 1)
        self.assertAlmostEqual(
            qa["gradient_lsq_relative_residual_max"],
            qa["gradient_lsq_residual_rms_max"] / max(1.0, response_rms),
            places=12,
        )

    def test_phase_halo_fails_closed_for_rank_one_gradient_support(self):
        rows, columns = np.indices((7, 9))
        truth = 0.8 * columns + 0.2 * rows
        mask = np.zeros(truth.shape, dtype=bool)
        mask[3, 2:7] = True
        phase = np.where(mask, truth, np.nan)

        extended, support, qa = extrapolate_phase_halo(
            phase, mask, 1.0, 1, gradient_lsq_max_radius_cells=2
        )

        self.assertEqual(qa["gradient_estimation_status"], "FAIL_CLOSED")
        self.assertGreater(qa["unestimable_gradient_source_count"], 0)
        self.assertGreater(qa["unsupported_halo_cell_count"], 0)
        self.assertFalse(np.any(support & ~mask))
        self.assertTrue(np.isnan(extended[~support]).all())

    def test_phase_offset_preserves_family_under_a_gauge_translation(self):
        xs = np.arange(0.0, 13.0)
        ys = np.arange(0.0, 9.0)
        x_grid, _ = np.meshgrid(xs, ys)
        mask = np.ones(x_grid.shape, dtype=bool)
        spacing = 2.0
        original_offset = 0.5
        gauge_translation = 0.7
        translated_offset = (original_offset + gauge_translation) % spacing

        original = extract_phase_contours(
            x_grid,
            mask,
            xs,
            ys,
            spacing_m=spacing,
            minimum_length_m=0.0,
            phase_offset_m=original_offset,
        )
        translated = extract_phase_contours(
            x_grid + gauge_translation,
            mask,
            xs,
            ys,
            spacing_m=spacing,
            minimum_length_m=0.0,
            phase_offset_m=translated_offset,
        )

        self.assertEqual(len(original), len(translated))
        for first, second in zip(original, translated):
            self.assertEqual(first["level_id"], second["level_id"])
            self.assertLess(
                first["geometry"].hausdorff_distance(second["geometry"]), 1e-10
            )
            self.assertAlmostEqual(
                first["phase_level_m"],
                first["level_id"] * spacing + original_offset,
                places=12,
            )
            self.assertAlmostEqual(
                second["phase_level_m"],
                second["level_id"] * spacing + translated_offset,
                places=12,
            )

    def test_phase_offset_cannot_be_combined_with_explicit_levels(self):
        phase = np.tile(np.arange(6.0), (5, 1))
        mask = np.ones(phase.shape, dtype=bool)
        with self.assertRaisesRegex(ValueError, "explicit levels"):
            extract_phase_contours(
                phase,
                mask,
                np.arange(6.0),
                np.arange(5.0),
                levels=np.asarray([2.0]),
                phase_offset_m=0.5,
            )

    def test_vectorized_contours_match_scalar_reference_across_edge_cases(self):
        xs = 500_000.0 + np.arange(29, dtype=float) * 1.25
        ys = 7_500_000.0 + np.arange(25, dtype=float) * 1.25
        x_grid, y_grid = np.meshgrid(xs, ys)
        local_x = x_grid - xs[0]
        local_y = y_grid - ys[0]
        full_mask = np.ones(x_grid.shape, dtype=bool)
        rows, columns = np.indices(full_mask.shape)
        saddle = (
            (columns - 14.0) * (rows - 12.0) / 7.0
            + 0.13 * columns
            - 0.09 * rows
        )

        irregular_mask = (
            ((columns - 14.0) / 12.0) ** 2
            + ((rows - 12.0) / 10.0) ** 2
            <= 1.0
        )
        irregular_mask[10:13, 13:16] = False
        irregular_truth = 0.52 * local_x - 0.37 * local_y + 2.0
        irregular_phase = np.where(irregular_mask, irregular_truth, np.nan)
        extended, support, halo_qa = extrapolate_phase_halo(
            irregular_phase,
            irregular_mask,
            1.25,
            1,
            gradient_lsq_max_radius_cells=2,
        )
        self.assertEqual(halo_qa["gradient_estimation_status"], "PASS")

        cases = (
            (0.63 * local_x + 0.31 * local_y - 8.0, full_mask, full_mask),
            (saddle, full_mask, full_mask),
            (extended, support, irregular_mask),
        )
        spacing_m = 2.3
        for phase, extraction_mask, level_mask in cases:
            for phase_fraction in (0.0, 0.25, 0.5, 0.75):
                phase_offset_m = phase_fraction * spacing_m
                level_values, level_ids = _spaced_level_values(
                    phase,
                    level_mask & np.isfinite(phase),
                    spacing_m,
                    phase_offset_m,
                )
                expected = _scalar_contour_reference(
                    phase,
                    extraction_mask,
                    xs,
                    ys,
                    level_values,
                    level_ids,
                )
                actual = extract_phase_contours(
                    phase,
                    extraction_mask,
                    xs,
                    ys,
                    spacing_m=spacing_m,
                    minimum_length_m=0.0,
                    level_range_mask=level_mask,
                    phase_offset_m=phase_offset_m,
                )
                self.assert_contours_equal_exactly(expected, actual)

        explicit_levels = np.asarray([-7.25, 0.0, 8.75])
        expected = _scalar_contour_reference(
            saddle,
            full_mask,
            xs,
            ys,
            explicit_levels,
            np.arange(explicit_levels.size),
        )
        actual = extract_phase_contours(
            saddle,
            full_mask,
            xs,
            ys,
            levels=explicit_levels,
            minimum_length_m=0.0,
        )
        self.assert_contours_equal_exactly(expected, actual)

    def test_vectorized_contour_backend_reduces_structural_grid_scans(self):
        rows, columns = np.indices((80, 88))
        phase = (
            0.45 * columns
            + 0.21 * rows
            + 0.003 * (columns - 44.0) ** 2
        )
        mask = np.ones(phase.shape, dtype=bool)
        xs = np.arange(phase.shape[1], dtype=float)
        ys = np.arange(phase.shape[0], dtype=float)
        level_values = np.linspace(
            float(np.min(phase)) + 0.1,
            float(np.max(phase)) - 0.1,
            70,
        )
        snap_tolerance = 1e-5

        one_chunk, one_chunk_stats = _contour_segment_coordinates_by_level(
            phase,
            mask,
            xs,
            ys,
            level_values,
            snap_tolerance,
            cell_chunk_size=phase.size,
        )
        chunked, stats = _contour_segment_coordinates_by_level(
            phase,
            mask,
            xs,
            ys,
            level_values,
            snap_tolerance,
            cell_chunk_size=500,
        )

        for expected_chunks, actual_chunks in zip(one_chunk, chunked):
            expected = (
                np.concatenate(expected_chunks, axis=0)
                if expected_chunks
                else np.empty((0, 2, 2), dtype=float)
            )
            actual = (
                np.concatenate(actual_chunks, axis=0)
                if actual_chunks
                else np.empty((0, 2, 2), dtype=float)
            )
            np.testing.assert_array_equal(expected, actual)
        self.assertEqual(stats["grid_scan_count"], 1)
        self.assertEqual(stats["legacy_grid_scan_count"], level_values.size)
        self.assertEqual(
            stats["emitted_segment_count"],
            one_chunk_stats["emitted_segment_count"],
        )
        self.assertGreater(stats["chunk_count"], 1)
        self.assertGreater(stats["emitted_segment_count"], 0)
        self.assertLess(
            stats["candidate_triangle_evaluation_count"],
            stats["grid_cell_count"] * stats["level_count"] // 5,
        )

    def test_planar_rectangle_produces_parallel_boundary_to_boundary_family(self):
        mask = np.ones((25, 41), dtype=bool)
        nx = np.zeros(mask.shape, dtype=float)
        ny = np.ones(mask.shape, dtype=float)
        policy = self.policy()
        phase, qa = project_integrable_phase(mask, nx, ny, 1.0, policy)
        self.assertTrue(qa["accepted"], qa)
        self.assertEqual(
            qa["phase_gauge"]["method"],
            "ZERO_AT_LEXICOGRAPHIC_FIRST_VALID_CELL_PER_COMPONENT",
        )
        self.assertEqual(
            qa["phase_gauge"]["anchor_grid_indices"], [{"row": 0, "column": 0}]
        )
        self.assertLess(qa["eikonal_residual_p95"], 1e-4)
        self.assertIn("integrability_residual_p95", qa)
        self.assertIn("phase_gradient_min", qa)
        contours = extract_phase_contours(
            phase,
            mask,
            np.arange(mask.shape[1], dtype=float),
            np.arange(mask.shape[0], dtype=float),
            policy=policy,
        )
        self.assertGreaterEqual(len(contours), 5)
        outer = box(0.0, 0.0, 40.0, 24.0).boundary
        for record in contours:
            line = record["geometry"]
            self.assertLess(Point(line.coords[0]).distance(outer), 1e-8)
            self.assertLess(Point(line.coords[-1]).distance(outer), 1e-8)
            self.assertAlmostEqual(line.bounds[0], 0.0, places=8)
            self.assertAlmostEqual(line.bounds[2], 40.0, places=8)

    def test_integrable_circular_normal_recovers_radial_phase(self):
        coordinates = np.arange(-26.0, 27.0, 1.0)
        x_grid, y_grid = np.meshgrid(coordinates, coordinates)
        radius = np.hypot(x_grid, y_grid)
        mask = (radius >= 8.0) & (radius <= 24.0)
        true_nx = np.zeros(mask.shape, dtype=float)
        true_ny = np.zeros(mask.shape, dtype=float)
        true_nx[mask] = x_grid[mask] / radius[mask]
        true_ny[mask] = y_grid[mask] / radius[mask]
        qx, qy = axial_from_angles(np.arctan2(true_ny, true_nx))
        nx, ny, lift_qa = lift_axial_normal(mask, qx, qy, anchor=(2, 26), policy=self.policy())
        self.assertTrue(lift_qa["accepted"], lift_qa)
        phase, qa = project_integrable_phase(mask, nx, ny, 1.0, self.policy())
        self.assertTrue(qa["accepted"], qa)

        truth = radius[mask]
        fitted = phase[mask]
        slope = float(np.dot(fitted - fitted.mean(), truth - truth.mean()) / np.dot(truth - truth.mean(), truth - truth.mean()))
        intercept = float(np.mean(fitted - slope * truth))
        rmse = float(np.sqrt(np.mean((fitted - (slope * truth + intercept)) ** 2)))
        self.assertAlmostEqual(abs(slope), 1.0, delta=0.04)
        self.assertLess(rmse, 0.12)

    def test_nonintegrable_zero_to_ninety_transition_is_rejected(self):
        mask = np.ones((31, 41), dtype=bool)
        base_theta = np.zeros(mask.shape, dtype=float)
        base_theta[:, mask.shape[1] // 2 :] = 90.0
        zeros = np.zeros(mask.shape, dtype=float)
        result = solve_continuous_phase(
            mask,
            zeros,
            zeros,
            1.0,
            base_theta,
            0.0,
            self.policy(),
        )
        self.assertFalse(result["qa"]["accepted"])
        self.assertIn(
            "AXIAL_ORIENTATION_DISCONTINUITY",
            result["qa"]["blocker_codes"],
        )

    def test_preference_zone_boundary_never_clips_a_contour(self):
        mask = np.ones((41, 61), dtype=bool)
        base_theta = np.zeros(mask.shape, dtype=float)
        base_theta[:, 30:] = 18.0
        zeros = np.zeros(mask.shape, dtype=float)
        policy = self.policy(orientation_smoothing_m=5.0)
        result = solve_continuous_phase(
            mask,
            zeros,
            zeros,
            1.0,
            base_theta,
            0.0,
            policy,
        )
        self.assertEqual(result["qa"]["axial_lift"]["connected_component_count"], 1)
        contours = extract_phase_contours(
            result["phase"],
            mask,
            np.arange(mask.shape[1], dtype=float),
            np.arange(mask.shape[0], dtype=float),
            policy=policy,
        )
        self.assertGreater(len(contours), 4)
        physical_edge = box(0.0, 0.0, 60.0, 40.0).boundary
        for record in contours:
            line = record["geometry"]
            if line.is_ring:
                continue
            for coordinate in (line.coords[0], line.coords[-1]):
                self.assertLess(Point(coordinate).distance(physical_edge), 1e-7)
                self.assertGreater(abs(coordinate[0] - 30.0), 1e-6)

    def test_static_radius_diagnostic_uses_work_path_requirement(self):
        radius = 20.0
        arc = LineString(
            [
                (radius * math.cos(angle), radius * math.sin(angle))
                for angle in np.linspace(0.0, math.pi / 2.0, 13)
            ]
        )
        failing = curvature_radius_diagnostics([arc], required_minimum_radius_m=25.0)
        self.assertEqual(failing["radius_status"], "FAIL_STATIC_PATH_RADIUS")
        self.assertAlmostEqual(failing["radius_min_observed_m"], radius, places=6)
        passing = curvature_radius_diagnostics([arc], required_minimum_radius_m=15.0)
        self.assertEqual(passing["radius_status"], "PASS_STATIC_PATH_RADIUS_ONLY")


if __name__ == "__main__":
    unittest.main()
