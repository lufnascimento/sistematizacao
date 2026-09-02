from __future__ import annotations

import json
import math
import unittest
from pathlib import Path

from scripts.pcx2_hydrograph import (
    calculate_pcx2_triangular_hydrograph,
    validate_pcx2_release,
)


class Pcx2HydrographTests(unittest.TestCase):
    def test_committed_schema_keeps_hydraulic_design_blocked(self) -> None:
        schema = json.loads(
            (Path(__file__).resolve().parents[1] / "schemas" / "preliminary-hydrograph-stage.schema.json").read_text(encoding="utf-8")
        )
        properties = schema["properties"]
        self.assertEqual(properties["release"]["const"], "PCX2_TRIANGULAR_UNIT_HYDROGRAPH_SCREENING_ONLY")
        self.assertEqual(properties["mass_balance_status"]["const"], "PASS")
        for key in ("channel_routing_evaluated", "structure_routing_evaluated", "hydraulic_capacity_evaluated", "receiver_approved", "guidance_authorized"):
            self.assertIs(properties[key]["const"], False)

    def test_single_pulse_preserves_volume_and_peak(self) -> None:
        result = calculate_pcx2_triangular_hydrograph(
            [{"duration_s": 600, "rainfall_excess_mm": 10}],
            catchment_area_ha=1,
            lag_time_s=900,
            output_step_s=60,
            base_to_peak_time_ratio=2.5,
        )
        self.assertTrue(math.isclose(result["input_rainfall_excess_volume_m3"], 100, abs_tol=1e-12))
        self.assertTrue(math.isclose(result["hydrograph_volume_m3"], 100, abs_tol=1e-10))
        self.assertTrue(math.isclose(result["peak_flow_m3_s"], 200 / 3000, abs_tol=1e-12))
        self.assertEqual(result["time_to_peak_s"], 1200)
        validate_pcx2_release(result)

    def test_superposition_preserves_all_pulse_volume(self) -> None:
        result = calculate_pcx2_triangular_hydrograph(
            [
                {"duration_s": 300, "rainfall_excess_mm": 2},
                {"duration_s": 300, "rainfall_excess_mm": 3},
                {"duration_s": 300, "rainfall_excess_mm": 0},
            ],
            catchment_area_ha=4,
            lag_time_s=600,
            output_step_s=77,
        )
        self.assertTrue(math.isclose(result["hydrograph_volume_m3"], 200, abs_tol=1e-9))
        self.assertEqual(result["mass_balance_status"], "PASS")
        self.assertGreater(result["peak_flow_m3_s"], 0)

    def test_zero_excess_returns_zero_hydrograph(self) -> None:
        result = calculate_pcx2_triangular_hydrograph(
            [{"duration_s": 60, "rainfall_excess_mm": 0}],
            catchment_area_ha=1,
            lag_time_s=120,
            output_step_s=30,
        )
        self.assertEqual(result["peak_flow_m3_s"], 0)
        self.assertEqual(result["hydrograph_volume_m3"], 0)
        validate_pcx2_release(result)

    def test_invalid_shape_parameters_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "must exceed 1"):
            calculate_pcx2_triangular_hydrograph(
                [{"duration_s": 60, "rainfall_excess_mm": 1}],
                catchment_area_ha=1,
                lag_time_s=120,
                output_step_s=30,
                base_to_peak_time_ratio=1,
            )

    def test_release_promotion_is_rejected(self) -> None:
        result = calculate_pcx2_triangular_hydrograph(
            [{"duration_s": 60, "rainfall_excess_mm": 1}],
            catchment_area_ha=1,
            lag_time_s=120,
            output_step_s=30,
        )
        result["hydraulic_capacity_evaluated"] = True
        with self.assertRaisesRegex(ValueError, "hydraulic_capacity_evaluated"):
            validate_pcx2_release(result)


if __name__ == "__main__":
    unittest.main()
