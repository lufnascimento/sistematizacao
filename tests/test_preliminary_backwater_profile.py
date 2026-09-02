from __future__ import annotations

import math
import json
import unittest
from pathlib import Path

from scripts.preliminary_backwater_profile import calculate_standard_step_profiles, validate_profile_release
from scripts.preliminary_section_capacity import normal_depth_m


class PreliminaryBackwaterProfileTests(unittest.TestCase):
    def test_committed_schema_pins_profile_release_boundary(self) -> None:
        schema = json.loads((Path(__file__).parents[1] / "schemas" / "preliminary-water-surface-profile.schema.json").read_text(encoding="utf-8"))
        properties = schema["properties"]
        self.assertEqual(properties["release"]["const"], "PRELIMINARY_STEADY_SUBCRITICAL_STANDARD_STEP_ONLY")
        for field in ("mixed_or_supercritical_flow_evaluated", "structures_evaluated", "unsteady_flow_evaluated", "receiver_approved", "project_executive_authorized", "guidance_authorized"):
            self.assertFalse(properties[field]["const"])

    def base(self) -> dict:
        return {"id": "T1", "condition_state": "CURRENT", "peak_flow_m3_s": 0.5, "length_m": 500.0, "bottom_width_m": 1.0, "side_slope_h_to_v": 1.5, "slope_m_m": 0.002, "manning_n": 0.04, "profile_step_count": 20}

    def test_normal_depth_boundary_produces_uniform_profile(self) -> None:
        values = self.base()
        values["downstream_water_depth_m"] = normal_depth_m(values["peak_flow_m3_s"], values["bottom_width_m"], values["side_slope_h_to_v"], values["slope_m_m"], values["manning_n"])
        result = calculate_standard_step_profiles([values])
        depths = [point["water_depth_m"] for point in result["profiles"][0]["profile"]]
        self.assertTrue(all(math.isclose(depth, depths[0], abs_tol=1e-8) for depth in depths))
        validate_profile_release(result)

    def test_deep_downstream_boundary_creates_subcritical_backwater_profile(self) -> None:
        values = self.base()
        values["downstream_water_depth_m"] = 1.5
        result = calculate_standard_step_profiles([values])
        profile = result["profiles"][0]
        self.assertEqual(len(profile["profile"]), 21)
        self.assertLess(profile["profile"][-1]["water_depth_m"], profile["profile"][0]["water_depth_m"])
        self.assertLess(profile["maximum_froude_number"], 1.0)

    def test_critical_boundary_and_release_promotion_fail_closed(self) -> None:
        values = self.base()
        values["downstream_water_depth_m"] = 0.01
        with self.assertRaisesRegex(ValueError, "SUPERCRITICAL_OR_CRITICAL"):
            calculate_standard_step_profiles([values])
        values["downstream_water_depth_m"] = 1.5
        result = calculate_standard_step_profiles([values])
        result["structures_evaluated"] = True
        with self.assertRaisesRegex(ValueError, "structures_evaluated"):
            validate_profile_release(result)


if __name__ == "__main__":
    unittest.main()
