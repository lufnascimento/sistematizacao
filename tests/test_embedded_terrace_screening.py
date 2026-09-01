import unittest

import numpy as np
from shapely.geometry import LineString, box

from scripts.embedded_terrace_screening import (
    ScreeningPolicy,
    clip_rows_to_strips,
    extract_ti_sensitivity_axes,
    partition_interterrace_strips,
    scenario_id,
    segment_length_metrics,
)


class EmbeddedTerraceScreeningTests(unittest.TestCase):
    def test_policy_rejects_non_increasing_or_non_positive_intervals(self):
        with self.assertRaisesRegex(ValueError, "unique and increasing"):
            ScreeningPolicy(vertical_interval_candidates_m=(10.0, 5.0)).validate()
        with self.assertRaisesRegex(ValueError, "finite and positive"):
            ScreeningPolicy(vertical_interval_candidates_m=(0.0,)).validate()

    def test_analytic_plane_produces_repeatable_elevation_isolines(self):
        coordinates = np.arange(0.0, 21.0, 1.0)
        _, y_grid = np.meshgrid(coordinates, coordinates)
        elevation = y_grid.copy()
        mask = np.ones(elevation.shape, dtype=bool)
        usable = box(0.0, 0.0, 20.0, 20.0)

        first = extract_ti_sensitivity_axes(
            elevation,
            mask,
            coordinates,
            coordinates,
            usable,
            vertical_interval_m=5.0,
            offset_fraction=0.0,
            minimum_axis_length_m=5.0,
        )
        second = extract_ti_sensitivity_axes(
            elevation,
            mask,
            coordinates,
            coordinates,
            usable,
            vertical_interval_m=5.0,
            offset_fraction=0.0,
            minimum_axis_length_m=5.0,
        )

        self.assertEqual(
            [item["target_elevation_m"] for item in first],
            [5.0, 10.0, 15.0],
        )
        self.assertEqual(
            [item["geometry"].wkb for item in first],
            [item["geometry"].wkb for item in second],
        )
        for item in first:
            self.assertAlmostEqual(item["geometry"].length, 20.0, places=8)

    def test_topology_gap_partitions_without_claiming_section_width(self):
        usable = box(0.0, 0.0, 20.0, 20.0)
        axes = [
            LineString([(0.0, 5.0), (20.0, 5.0)]),
            LineString([(0.0, 10.0), (20.0, 10.0)]),
        ]

        strips, qa = partition_interterrace_strips(
            usable,
            axes,
            topology_gap_half_width_m=0.25,
            minimum_strip_area_m2=1.0,
        )

        self.assertEqual(len(strips), 3)
        self.assertEqual(qa["method"], "AXIS_BUFFER_TOPOLOGY_GAP_NOT_CROSS_SECTION")
        self.assertIn("not embedded-section width", qa["scope_note"])
        self.assertAlmostEqual(
            sum(strip.area for strip in strips) + qa["topology_gap_area_m2"],
            usable.area,
            places=7,
        )

    def test_cf0_row_is_clipped_into_diagnostic_strip_segments(self):
        usable = box(0.0, 0.0, 20.0, 20.0)
        strips, _ = partition_interterrace_strips(
            usable,
            [
                LineString([(0.0, 5.0), (20.0, 5.0)]),
                LineString([(0.0, 10.0), (20.0, 10.0)]),
            ],
            topology_gap_half_width_m=0.25,
            minimum_strip_area_m2=1.0,
        )
        records = clip_rows_to_strips(
            [
                {
                    "source_row_id": "R1",
                    "source_family_id": "CF0C:F1",
                    "geometry": LineString([(4.0, 0.0), (4.0, 20.0)]),
                }
            ],
            strips,
            minimum_segment_length_m=1.0,
        )

        self.assertEqual(len(records), 3)
        self.assertEqual({record["source_row_id"] for record in records}, {"R1"})
        self.assertLess(sum(record["length_m"] for record in records), 20.0)
        metrics = segment_length_metrics(records)
        self.assertEqual(metrics["segment_count"], 3)
        self.assertGreater(metrics["length_p95_m"], metrics["length_p05_m"])

    def test_scenario_identifier_is_stable_and_explicit(self):
        self.assertEqual(scenario_id("FIELD-A", 10.0, 0.25), "C1E0_TI_FIELD-A_VI10_O0P25")


if __name__ == "__main__":
    unittest.main()
