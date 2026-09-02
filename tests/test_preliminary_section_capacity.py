from __future__ import annotations

import json
import math
import unittest
from pathlib import Path

from scripts.preliminary_section_capacity import (
    check_reach_capacities,
    manning_discharge_m3_s,
    normal_depth_m,
    validate_capacity_release,
)


class PreliminarySectionCapacityTests(unittest.TestCase):
    def test_committed_schema_pins_preliminary_release_boundary(self) -> None:
        schema = json.loads(
            (Path(__file__).parents[1] / "schemas" / "preliminary-section-capacity-stage.schema.json").read_text(
                encoding="utf-8"
            )
        )
        properties = schema["properties"]
        self.assertEqual(properties["release"]["const"], "PRELIMINARY_TRAPEZOIDAL_MANNING_CAPACITY_ONLY")
        for field in (
            "backwater_evaluated",
            "unsteady_flow_evaluated",
            "receiver_approved",
            "project_executive_authorized",
            "guidance_authorized",
        ):
            self.assertFalse(properties[field]["const"])
        self.assertEqual(properties["admissible_velocity_or_shear_evaluated"]["type"], "boolean")
        self.assertFalse(properties["reaches"]["items"]["properties"]["erosion_safety_approved"]["const"])
        self.assertFalse(properties["reaches"]["items"]["properties"]["overflow_path_approved"]["const"])
        self.assertFalse(properties["overflow_paths_approved"]["const"])

    def test_rectangular_manning_discharge_and_inverse_depth(self) -> None:
        discharge = manning_discharge_m3_s(1.0, 2.0, 0.0, 0.01, 0.03)
        expected = (1 / 0.03) * 2.0 * (0.5 ** (2 / 3)) * 0.1
        self.assertTrue(math.isclose(discharge, expected, rel_tol=1e-12))
        self.assertTrue(math.isclose(normal_depth_m(discharge, 2.0, 0.0, 0.01, 0.03), 1.0, abs_tol=1e-12))

    def test_capacity_pass_and_exceedance_are_reported(self) -> None:
        base = {"bottom_width_m": 0.5, "side_slope_h_to_v": 1.5, "slope_m_m": 0.005, "manning_n": 0.04, "maximum_flow_depth_m": 0.6}
        capacity = manning_discharge_m3_s(0.6, 0.5, 1.5, 0.005, 0.04)
        result = check_reach_capacities([
            {"id": "OK", "peak_flow_m3_s": capacity * 0.8, **base},
            {"id": "FALHA", "peak_flow_m3_s": capacity * 1.2, **base},
        ])
        self.assertEqual(result["within_capacity_count"], 1)
        self.assertEqual(result["exceeded_capacity_count"], 1)
        self.assertFalse(result["reaches"][0]["erosion_safety_approved"])
        validate_capacity_release(result)

    def test_condition_states_and_declared_stability_limits_are_compared(self) -> None:
        base = {"id": "T1", "peak_flow_m3_s": 0.5, "bottom_width_m": 0.5, "side_slope_h_to_v": 1.5, "slope_m_m": 0.005, "manning_n": 0.04, "maximum_flow_depth_m": 0.6, "stability_limit_source_id": "regional-pack", "stability_limit_evidence_state": "SYSTEM_REFERENCE"}
        result = check_reach_capacities([
            {**base, "condition_state": "NEW", "maximum_admissible_velocity_m_s": 100, "maximum_admissible_shear_pa": 100000},
            {**base, "condition_state": "DEGRADED", "maximum_flow_depth_m": 0.2, "maximum_admissible_velocity_m_s": 0.01, "maximum_admissible_shear_pa": 0.01},
        ])
        self.assertEqual(result["condition_counts"], {"NEW": 1, "CURRENT": 0, "DEGRADED": 1})
        self.assertEqual(result["stability_evaluated_count"], 2)
        self.assertEqual(result["stability_exceeded_count"], 1)
        self.assertEqual(result["reaches"][0]["preliminary_stability_status"], "WITHIN_DECLARED_LIMITS")
        self.assertEqual(result["reaches"][1]["preliminary_stability_status"], "EXCEEDS_BOTH_DECLARED_LIMITS")
        self.assertTrue(result["admissible_velocity_or_shear_evaluated"])
        self.assertTrue(all(item["erosion_safety_approved"] is False for item in result["reaches"]))
        validate_capacity_release(result)

    def test_declared_stability_limit_requires_lineage(self) -> None:
        with self.assertRaisesRegex(ValueError, "source lineage"):
            check_reach_capacities([{"id": "T1", "peak_flow_m3_s": 0.5, "bottom_width_m": 0.5, "side_slope_h_to_v": 1.5, "slope_m_m": 0.005, "manning_n": 0.04, "maximum_flow_depth_m": 0.6, "maximum_admissible_velocity_m_s": 1.0}])

    def test_freeboard_overtopping_and_overflow_path_are_reported(self) -> None:
        base = {"peak_flow_m3_s": 0.5, "bottom_width_m": 0.5, "side_slope_h_to_v": 1.5, "slope_m_m": 0.005, "manning_n": 0.04, "maximum_flow_depth_m": 0.6}
        result = check_reach_capacities([
            {"id": "OK", **base, "bankfull_depth_m": 1.0, "required_freeboard_m": 0.2},
            {"id": "TRANSBORDA", **base, "bankfull_depth_m": 0.7, "required_freeboard_m": 0.1, "peak_flow_m3_s": 5.0, "overflow_path_state": "DECLARED_NOT_REVIEWED", "overflow_receiver_id": "BACIA_01"},
        ])
        self.assertEqual(result["freeboard_evaluated_count"], 2)
        self.assertEqual(result["overtopping_count"], 1)
        self.assertEqual(result["overflow_path_declared_count"], 1)
        self.assertEqual(result["reaches"][0]["preliminary_freeboard_status"], "WITHIN_DECLARED_FREEBOARD")
        self.assertEqual(result["reaches"][1]["preliminary_freeboard_status"], "OVERTOPS_DECLARED_BANK")
        validate_capacity_release(result)

    def test_overflow_path_requires_receiver_and_freeboard_pair(self) -> None:
        base = {"id": "T1", "peak_flow_m3_s": 0.5, "bottom_width_m": 0.5, "side_slope_h_to_v": 1.5, "slope_m_m": 0.005, "manning_n": 0.04, "maximum_flow_depth_m": 0.6}
        with self.assertRaisesRegex(ValueError, "declared together"):
            check_reach_capacities([{**base, "bankfull_depth_m": 1.0}])
        with self.assertRaisesRegex(ValueError, "declarations disagree"):
            check_reach_capacities([{**base, "overflow_path_state": "DECLARED_NOT_REVIEWED"}])

    def test_invalid_inputs_and_release_promotion_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            manning_discharge_m3_s(1, 1, 1, 0, 0.03)
        result = check_reach_capacities([{"id": "R", "peak_flow_m3_s": 1, "bottom_width_m": 1, "side_slope_h_to_v": 1, "slope_m_m": 0.01, "manning_n": 0.03, "maximum_flow_depth_m": 1}])
        result["receiver_approved"] = True
        with self.assertRaisesRegex(ValueError, "receiver_approved"):
            validate_capacity_release(result)


if __name__ == "__main__":
    unittest.main()
