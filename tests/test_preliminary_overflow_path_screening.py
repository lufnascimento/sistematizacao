import json
import unittest
from pathlib import Path

from scripts.preliminary_overflow_path_screening import screen_overflow_paths, validate_overflow_path_release


class OverflowPathScreeningTests(unittest.TestCase):
    def test_committed_schema_pins_non_approval_boundary(self):
        schema = json.loads((Path(__file__).parents[1] / "schemas" / "preliminary-overflow-path-screening.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["release"]["const"], "PRELIMINARY_DECLARED_OVERFLOW_PATH_SCREENING_ONLY")
        for field in ("terrain_derived", "overflow_routed", "receivers_approved", "project_executive_authorized", "guidance_authorized"):
            self.assertFalse(schema["properties"][field]["const"])

    def test_downhill_connected_path_is_clear_for_screening_only(self):
        result = screen_overflow_paths(
            [{"id": "P1", "reach_id": "T1", "receiver_id": "R1", "coordinates": [[0, 0, 10], [10, 0, 9], [20, 0, 8]]}],
            [{"id": "R1", "coordinate": [20, 0, 8]}],
        )
        self.assertEqual(result["screened_clear_count"], 1)
        self.assertFalse(result["paths"][0]["overflow_path_approved"])
        validate_overflow_path_release(result)

    def test_adverse_grade_and_power_barrier_require_review(self):
        result = screen_overflow_paths(
            [{"id": "P1", "reach_id": "T1", "receiver_id": "R1", "coordinates": [[0, 0, 10], [10, 0, 10.2], [20, 0, 9]]}],
            [{"id": "R1", "coordinate": [20, 0, 9]}],
            [{"id": "REDE", "type": "POWER_NETWORK", "coordinates": [[5, -5], [5, 5]], "buffer_m": 1}],
        )
        path = result["paths"][0]
        self.assertEqual(path["screening_status"], "REQUIRES_REVIEW")
        self.assertEqual(path["adverse_segment_indexes"], [0])
        self.assertEqual(path["crossed_barriers"][0]["id"], "REDE")

    def test_disconnected_receiver_requires_review(self):
        result = screen_overflow_paths(
            [{"id": "P1", "reach_id": "T1", "receiver_id": "R1", "coordinates": [[0, 0, 10], [5, 0, 9]]}],
            [{"id": "R1", "coordinate": [20, 0, 8]}], endpoint_tolerance_m=1,
        )
        self.assertEqual(result["paths"][0]["receiver_connection_status"], "NOT_CONNECTED")

    def test_densification_cannot_hide_an_adverse_rise(self):
        for vertices in (
            [[0, 0, 10], [4, 0, 10.12], [5, 0, 9]],
            [[0, 0, 10], [1, 0, 10.03], [2, 0, 10.06], [3, 0, 10.09], [4, 0, 10.12], [5, 0, 9]],
        ):
            with self.subTest(vertices=len(vertices)):
                result = screen_overflow_paths(
                    [{"id": "P", "reach_id": "T", "receiver_id": "R", "coordinates": vertices}],
                    [{"id": "R", "coordinate": [5, 0, 9]}],
                )
                self.assertEqual(result["paths"][0]["screening_status"], "REQUIRES_REVIEW")
                self.assertAlmostEqual(result["paths"][0]["maximum_accumulated_adverse_rise_m"], 0.12)

    def test_adverse_rise_uses_local_low_after_descent(self):
        result = screen_overflow_paths(
            [{"id": "P", "reach_id": "T", "receiver_id": "R", "coordinates": [[0, 0, 20], [1, 0, 10], [2, 0, 10.04], [3, 0, 10.08], [4, 0, 9]]}],
            [{"id": "R", "coordinate": [4, 0, 9]}],
        )
        self.assertEqual(result["paths"][0]["screening_status"], "REQUIRES_REVIEW")
        self.assertAlmostEqual(result["paths"][0]["maximum_accumulated_adverse_rise_m"], 0.08)

    def test_invalid_geometry_is_rejected(self):
        with self.assertRaises(ValueError):
            screen_overflow_paths([{"id": "P1", "reach_id": "T1", "receiver_id": "R1", "coordinates": [[0, 0, 1]]}], [{"id": "R1", "coordinate": [0, 0, 1]}])

    def test_non_finite_barrier_and_invalid_tolerance_are_rejected(self):
        path = [{"id": "P1", "reach_id": "T1", "receiver_id": "R1", "coordinates": [[0, 0, 1], [1, 0, 0]]}]
        receiver = [{"id": "R1", "coordinate": [1, 0, 0]}]
        with self.assertRaises(ValueError):
            screen_overflow_paths(path, receiver, endpoint_tolerance_m=0)
        with self.assertRaises(ValueError):
            screen_overflow_paths(path, receiver, [{"id": "B1", "coordinates": [[0, float("nan")], [1, 1]], "buffer_m": 0}])


if __name__ == "__main__":
    unittest.main()
