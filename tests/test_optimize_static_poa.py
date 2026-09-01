"""Deterministic tests for the first static POA optimizer."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.optimize_static_poa import (  # noqa: E402
    EXIT_BLOCKED,
    EXIT_INVALID_REQUEST,
    EXIT_OK,
    MODE_E0,
    execute_request_file,
    make_self_test_fixture,
    make_self_test_request,
    optimize_request,
    run_self_test,
)


class StaticPoaOptimizerTest(unittest.TestCase):
    def test_known_optimum_and_absolute_power_barrier(self) -> None:
        result = run_self_test()
        self.assertEqual(result["status"], "SELF_TEST_PASSED")
        self.assertEqual(result["known_optimum"]["selected_poa_ids"], ["POA_A"])
        self.assertAlmostEqual(result["known_optimum"]["objective_tonne_m"], 70.0)
        self.assertTrue(result["power_barrier"]["candidate_inside_barrier_ineligible"])
        self.assertTrue(result["power_barrier"]["road_edges_clipped"])
        self.assertTrue(result["power_barrier"]["assignment_routes_clear"])
        self.assertFalse(result["power_barrier"]["automatic_crossing"])
        self.assertTrue(result["manifest"]["project_request_ref_preserved"])
        self.assertFalse(result["manifest"]["automatic_translation"])

    def test_point_candidate_downgrades_routed_result(self) -> None:
        with tempfile.TemporaryDirectory(prefix="poa_point_test_") as temporary:
            directory = Path(temporary)
            fixture = make_self_test_fixture(directory)
            request = make_self_test_request(
                directory,
                fixture,
                candidate_layer="point_candidates",
            )

            result, _, exit_code = optimize_request(request, directory)

            self.assertEqual(exit_code, EXIT_OK)
            self.assertEqual(result["status"], "GEOMETRIC_SCREENING")
            self.assertEqual(result["distance_basis"], "NETWORKX_ROUTED_GRAPH")
            self.assertFalse(result["metrics"]["operational_metrics_available"])
            self.assertFalse(result["metrics"]["gain_claimed"])
            self.assertIn(
                "POINT_POA_IS_NOT_A_VALIDATED_YARD",
                {warning["code"] for warning in result["warnings"]},
            )

    def test_missing_network_is_execution_blocker(self) -> None:
        with tempfile.TemporaryDirectory(prefix="poa_blocker_test_") as temporary:
            directory = Path(temporary)
            fixture = make_self_test_fixture(directory)
            request = make_self_test_request(directory, fixture)
            request["inputs"].pop("road_network")

            result, _, exit_code = optimize_request(request, directory)

            self.assertEqual(exit_code, EXIT_BLOCKED)
            self.assertEqual(result["status"], "BLOCKED")
            self.assertTrue(result["blockers"]["execution"])
            self.assertFalse(result["blockers"]["result"])

    def test_wrong_run_field_is_execution_blocker(self) -> None:
        with tempfile.TemporaryDirectory(prefix="poa_run_id_test_") as temporary:
            directory = Path(temporary)
            fixture = make_self_test_fixture(directory)
            request = make_self_test_request(directory, fixture)
            request["inputs"]["worked_lines"]["shot_id_field"] = "segment_id"

            result, _, exit_code = optimize_request(request, directory)

            self.assertEqual(exit_code, EXIT_BLOCKED)
            messages = [item["message"] for item in result["blockers"]["execution"]]
            self.assertTrue(any("operational_run_id" in message for message in messages))

    def test_e0_is_explicit_non_operational_screening(self) -> None:
        with tempfile.TemporaryDirectory(prefix="poa_e0_test_") as temporary:
            directory = Path(temporary)
            fixture = make_self_test_fixture(directory)
            request = make_self_test_request(directory, fixture)
            request["mode"] = MODE_E0
            request["inputs"].pop("road_network")
            request.pop("productivity")

            result, _, exit_code = optimize_request(request, directory)

            self.assertEqual(exit_code, EXIT_OK)
            self.assertEqual(result["status"], "GEOMETRIC_SCREENING")
            self.assertEqual(result["distance_basis"], "EUCLIDEAN_E0_SCREENING_ONLY")
            self.assertFalse(result["metrics"]["operational_metrics_available"])
            self.assertFalse(result["metrics"]["gain_claimed"])

    def test_invalid_stage_manifest_returns_exit_two(self) -> None:
        with tempfile.TemporaryDirectory(prefix="poa_invalid_test_") as temporary:
            request_path = Path(temporary) / "invalid.json"
            request_path.write_text("{}\n", encoding="utf-8")

            result, exit_code = execute_request_file(request_path)

            self.assertEqual(exit_code, EXIT_INVALID_REQUEST)
            self.assertEqual(result["status"], "INVALID_REQUEST")
            self.assertFalse(result["request_validation"]["validated"])


if __name__ == "__main__":
    unittest.main()
