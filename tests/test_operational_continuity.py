import math
import unittest

from shapely.geometry import LineString, Point, box

from scripts.operational_continuity import ContinuityPolicy, analyze_operational_continuity


SCENARIO = {"id": "TEST"}


def record(line_id, geometry, level_id, field):
    return {
        "line_id": line_id,
        "geometry": geometry,
        "level_id": level_id,
        "field": field,
        "scenario": SCENARIO,
    }


def field_with_optional_hole(hole=None):
    outer = box(0, 0, 100, 50)
    usable = outer.difference(hole) if hole is not None else outer
    return {
        "code": "F1",
        "usable": usable,
        "headland_work_edge": outer.boundary,
        "unclassified_obstacle_edge": hole.boundary if hole is not None else None,
    }


class OperationalContinuityTests(unittest.TestCase):
    def policy(self, required_radius=None):
        return ContinuityPolicy(
            terminal_surface_tolerance_m=0.25,
            row_spacing_m=1.5,
            required_minimum_radius_m=required_radius,
        )

    def test_boundary_to_boundary_row_has_no_geometric_blocker(self):
        field = field_with_optional_hole()
        result = analyze_operational_continuity(
            [record("L1", LineString([(0, 10), (100, 10)]), 0, field)],
            [field],
            self.policy(),
        )
        diagnostic = result["line_diagnostics"]["L1"]
        self.assertEqual(diagnostic["blocker_codes"], [])
        self.assertEqual(diagnostic["start"]["surface_type"], "HEADLAND_WORK_EDGE")
        self.assertIn("PENDING_FLEET", diagnostic["operational_continuity_status"])

    def test_internal_endpoint_is_blocked(self):
        field = field_with_optional_hole()
        result = analyze_operational_continuity(
            [record("L1", LineString([(10, 10), (90, 10)]), 0, field)],
            [field],
            self.policy(),
        )
        self.assertIn(
            "INTERNAL_UNSUPPORTED_ENDPOINT",
            result["line_diagnostics"]["L1"]["blocker_codes"],
        )

    def test_obstacle_break_is_not_mistaken_for_authorized_maneuver(self):
        hole = Point(50, 25).buffer(10)
        field = field_with_optional_hole(hole)
        line = LineString([(0, 25), (40, 25)])
        result = analyze_operational_continuity([record("L1", line, 0, field)], [field], self.policy())
        diagnostic = result["line_diagnostics"]["L1"]
        self.assertEqual(diagnostic["end"]["surface_type"], "UNCLASSIFIED_OBSTACLE_EDGE")
        self.assertEqual(diagnostic["blocker_codes"], [])
        self.assertIn("OBSTACLE_ACCESS", ",".join(diagnostic["warning_codes"]))

    def test_crossing_rows_are_blocked(self):
        field = field_with_optional_hole()
        records = [
            record("L1", LineString([(0, 0), (100, 50)]), 0, field),
            record("L2", LineString([(0, 50), (100, 0)]), 1, field),
        ]
        result = analyze_operational_continuity(records, [field], self.policy())
        self.assertIn("ROW_CROSSING", result["line_diagnostics"]["L1"]["blocker_codes"])
        self.assertEqual(result["group_summaries"][("TEST", "F1")]["row_crossing_count"], 1)

    def test_closed_loop_without_entry_is_blocked(self):
        field = field_with_optional_hole()
        loop = Point(50, 25).buffer(10).boundary
        result = analyze_operational_continuity([record("L1", loop, 0, field)], [field], self.policy())
        self.assertIn(
            "CLOSED_LOOP_WITHOUT_APPROVED_ENTRY",
            result["line_diagnostics"]["L1"]["blocker_codes"],
        )

    def test_parallel_rows_are_not_joined_and_spacing_is_measured(self):
        field = field_with_optional_hole()
        records = [
            record("L1", LineString([(0, 10), (100, 10)]), 0, field),
            record("L2", LineString([(0, 11.5), (100, 11.5)]), 1, field),
        ]
        result = analyze_operational_continuity(records, [field], self.policy())
        summary = result["group_summaries"][("TEST", "F1")]
        self.assertEqual(summary["continuity_component_count"], 2)
        self.assertAlmostEqual(summary["geometric_spacing_p50_m"], 1.5, places=6)
        self.assertEqual(summary["row_touch_count"], 0)

    def test_unpaired_spacing_samples_count_as_outside_total(self):
        field = field_with_optional_hole()
        records = [
            record("L1", LineString([(0, 10), (100, 10)]), 0, field),
            record("L2", LineString([(0, 30), (100, 30)]), 1, field),
        ]
        result = analyze_operational_continuity(records, [field], self.policy())
        summary = result["group_summaries"][("TEST", "F1")]
        self.assertGreater(summary["geometric_spacing_unpaired_sample_count"], 0)
        self.assertEqual(summary["geometric_spacing_outside_tolerance_percent"], 100.0)
        self.assertIsNone(summary["geometric_spacing_paired_outside_tolerance_percent"])

    def test_invalid_sampling_policy_is_rejected(self):
        with self.assertRaises(ValueError):
            ContinuityPolicy(
                terminal_surface_tolerance_m=0.25,
                row_spacing_m=1.5,
                spacing_sample_interval_m=0.0,
            )

    def test_radius_gate_uses_supplied_fleet_value(self):
        radius = 10.0
        arc = LineString(
            [
                (radius * math.cos(angle), radius * math.sin(angle))
                for angle in [0.0, math.pi / 8, math.pi / 4, 3 * math.pi / 8, math.pi / 2]
            ]
        )
        field = {
            "code": "F1",
            "usable": arc.buffer(1),
            "headland_work_edge": Point(arc.coords[0]).union(Point(arc.coords[-1])),
            "unclassified_obstacle_edge": None,
        }
        result = analyze_operational_continuity(
            [record("L1", arc, 0, field)], [field], self.policy(required_radius=15.0)
        )
        self.assertEqual(
            result["line_diagnostics"]["L1"]["radius_status"],
            "FAIL_STATIC_PATH_RADIUS",
        )
        self.assertIn("MINIMUM_RADIUS_VIOLATION", result["line_diagnostics"]["L1"]["blocker_codes"])


if __name__ == "__main__":
    unittest.main()
