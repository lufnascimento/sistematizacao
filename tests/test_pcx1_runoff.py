from __future__ import annotations

import math
import json
import unittest
from pathlib import Path

from scripts.pcx1_runoff import (
    RainfallInterval,
    calculate_pcx1_rainfall_excess,
    cumulative_runoff_depth_mm,
    potential_retention_mm,
    validate_pcx1_release,
)


class Pcx1RunoffTests(unittest.TestCase):
    def test_committed_schema_pins_the_non_design_boundary(self) -> None:
        schema = json.loads(
            (Path(__file__).resolve().parents[1] / "schemas" / "pcx1-rainfall-excess-stage.schema.json").read_text(encoding="utf-8")
        )
        properties = schema["properties"]
        self.assertEqual(properties["release"]["const"], "PCX1_NRCS_CN_RAINFALL_EXCESS_ONLY")
        for key in (
            "hydrograph_generated", "peak_flow_evaluated",
            "hydraulic_capacity_evaluated", "receiver_approved", "guidance_authorized"
        ):
            self.assertIs(properties[key]["const"], False)

    def test_cn_100_converts_all_rainfall_to_excess(self) -> None:
        result = calculate_pcx1_rainfall_excess(
            [RainfallInterval(600, 5), RainfallInterval(600, 10)],
            catchment_area_ha=2,
            curve_number=100,
            initial_abstraction_ratio=0.2,
        )
        self.assertEqual(result["total_rainfall_excess_mm"], 15)
        self.assertEqual(result["total_rainfall_excess_volume_m3"], 300)
        self.assertEqual([row["rainfall_excess_mm"] for row in result["intervals"]], [5, 10])
        validate_pcx1_release(result)

    def test_incremental_depths_reconcile_to_cumulative_equation(self) -> None:
        result = calculate_pcx1_rainfall_excess(
            [RainfallInterval(900, 8), RainfallInterval(900, 17), RainfallInterval(900, 25)],
            catchment_area_ha=10,
            curve_number=80,
            initial_abstraction_ratio=0.2,
        )
        expected = cumulative_runoff_depth_mm(50, 80, 0.2)
        observed = sum(row["rainfall_excess_mm"] for row in result["intervals"])
        self.assertTrue(math.isclose(observed, expected, rel_tol=0, abs_tol=1e-12))
        self.assertTrue(all(row["rainfall_excess_mm"] >= 0 for row in result["intervals"]))

    def test_rain_before_initial_abstraction_has_zero_excess(self) -> None:
        retention = potential_retention_mm(75)
        result = calculate_pcx1_rainfall_excess(
            [RainfallInterval(3600, 0.2 * retention)],
            catchment_area_ha=1,
            curve_number=75,
            initial_abstraction_ratio=0.2,
        )
        self.assertEqual(result["total_rainfall_excess_mm"], 0)

    def test_invalid_inputs_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "curve_number"):
            potential_retention_mm(0)
        with self.assertRaisesRegex(ValueError, "initial_abstraction_ratio"):
            cumulative_runoff_depth_mm(50, 80, 0.5)
        with self.assertRaisesRegex(ValueError, "intervals cannot be empty"):
            calculate_pcx1_rainfall_excess(
                [], catchment_area_ha=1, curve_number=80, initial_abstraction_ratio=0.2
            )

    def test_release_promotion_is_rejected(self) -> None:
        result = calculate_pcx1_rainfall_excess(
            [RainfallInterval(60, 1)],
            catchment_area_ha=1,
            curve_number=100,
            initial_abstraction_ratio=0.2,
        )
        result["peak_flow_evaluated"] = True
        with self.assertRaisesRegex(ValueError, "peak_flow_evaluated"):
            validate_pcx1_release(result)


if __name__ == "__main__":
    unittest.main()
