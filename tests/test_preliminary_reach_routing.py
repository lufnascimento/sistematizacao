from __future__ import annotations

import unittest
import json
from pathlib import Path

from scripts.preliminary_reach_routing import route_hydrographs_by_lag, validate_routing_release


def triangle(volume_scale: float = 1.0):
    return [
        {"time_s": 0, "flow_m3_s": 0},
        {"time_s": 10, "flow_m3_s": 2 * volume_scale},
        {"time_s": 20, "flow_m3_s": 0},
    ]


class PreliminaryReachRoutingTests(unittest.TestCase):
    def test_committed_schema_keeps_capacity_blocked(self) -> None:
        schema = json.loads(
            (Path(__file__).resolve().parents[1] / "schemas" / "preliminary-reach-routing-stage.schema.json").read_text(encoding="utf-8")
        )
        properties = schema["properties"]
        self.assertEqual(properties["release"]["const"], "PRELIMINARY_DAG_LAG_ROUTING_ONLY")
        for key in ("attenuation_evaluated", "backwater_evaluated", "hydraulic_capacity_evaluated", "failure_path_evaluated", "guidance_authorized"):
            self.assertIs(properties[key]["const"], False)

    def test_single_reach_shifts_peak_and_preserves_volume(self) -> None:
        result = route_hydrographs_by_lag(
            [{"id": "R1", "upstream_node_id": "A", "downstream_node_id": "B", "travel_time_s": 30}],
            {"A": triangle()},
        )
        self.assertEqual(result["reaches"][0]["time_to_peak_s"], 40)
        self.assertAlmostEqual(result["input_volume_m3"], 20)
        self.assertAlmostEqual(result["outlet_volume_m3"], 20)
        validate_routing_release(result)

    def test_confluence_sums_independent_inflows(self) -> None:
        result = route_hydrographs_by_lag(
            [
                {"id": "R1", "upstream_node_id": "A", "downstream_node_id": "C", "travel_time_s": 10},
                {"id": "R2", "upstream_node_id": "B", "downstream_node_id": "C", "travel_time_s": 20},
                {"id": "R3", "upstream_node_id": "C", "downstream_node_id": "D", "travel_time_s": 5},
            ],
            {"A": triangle(), "B": triangle(0.5)},
        )
        self.assertAlmostEqual(result["input_volume_m3"], 30)
        self.assertAlmostEqual(result["outlet_volume_m3"], 30)
        self.assertGreater(next(item for item in result["reaches"] if item["id"] == "R3")["peak_flow_m3_s"], 0)

    def test_cycle_and_unallocated_split_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "acyclic"):
            route_hydrographs_by_lag(
                [
                    {"id": "R1", "upstream_node_id": "A", "downstream_node_id": "B", "travel_time_s": 1},
                    {"id": "R2", "upstream_node_id": "B", "downstream_node_id": "A", "travel_time_s": 1},
                ],
                {"A": triangle()},
            )
        with self.assertRaisesRegex(ValueError, "flow splits"):
            route_hydrographs_by_lag(
                [
                    {"id": "R1", "upstream_node_id": "A", "downstream_node_id": "B", "travel_time_s": 1},
                    {"id": "R2", "upstream_node_id": "A", "downstream_node_id": "C", "travel_time_s": 1},
                ],
                {"A": triangle()},
            )

    def test_release_promotion_is_rejected(self) -> None:
        result = route_hydrographs_by_lag(
            [{"id": "R1", "upstream_node_id": "A", "downstream_node_id": "B", "travel_time_s": 1}],
            {"A": triangle()},
        )
        result["hydraulic_capacity_evaluated"] = True
        with self.assertRaisesRegex(ValueError, "hydraulic_capacity_evaluated"):
            validate_routing_release(result)


if __name__ == "__main__":
    unittest.main()
