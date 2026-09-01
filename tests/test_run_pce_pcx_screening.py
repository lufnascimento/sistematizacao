import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts import run_pce_pcx_screening as runner


class PcePcxScreeningRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.request_path = runner.ROOT / "config" / "exemplo_pce_pcx_screening.json"
        cls.request = runner.load_json(cls.request_path)

    def test_public_example_is_lineage_bound_and_fail_closed(self):
        result = runner.build_result(copy.deepcopy(self.request), self.request_path)

        self.assertEqual(result["release"], "PCE0_PCX0_SCREENING_NOT_DESIGN")
        self.assertEqual(result["pce0"]["screening_status"], "WITHIN_DECLARED_TOLERANCE_SCREENING")
        self.assertEqual(result["pcx0"]["numeric_continuity_status"], "PASS")
        self.assertIn("PCE_PROJECT_EVIDENCE_INCOMPLETE", result["blocker_codes"])
        self.assertIn("PCX_PROJECT_EVIDENCE_INCOMPLETE", result["blocker_codes"])
        self.assertFalse(result["authorization_claims"]["guidance_authorized"])
        self.assertEqual(
            result["screening_request_ref"]["sha256"],
            runner.sha256_file(self.request_path),
        )

    def test_positive_authorization_claim_is_rejected(self):
        request = copy.deepcopy(self.request)
        request["authorization_claims"]["hydraulic_capacity"] = True
        with self.assertRaisesRegex(runner.ScreeningRequestError, "authorization claims"):
            runner.build_result(request, self.request_path)

    def test_project_request_hash_drift_is_rejected(self):
        request = copy.deepcopy(self.request)
        request["project_request_ref"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(runner.ScreeningRequestError, "SHA-256 changed"):
            runner.build_result(request, self.request_path)

    def test_unknown_request_field_is_rejected(self):
        request = copy.deepcopy(self.request)
        request["hydraulic_approved"] = False
        with self.assertRaisesRegex(runner.ScreeningRequestError, "request fields changed"):
            runner.build_result(request, self.request_path)

    def test_output_is_written_atomically_and_remains_searchable_json(self):
        result = runner.build_result(copy.deepcopy(self.request), self.request_path)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "screening-result.json"
            runner.write_json_atomic(output, result)
            loaded = json.loads(output.read_text(encoding="utf-8"))

            self.assertEqual(loaded["analysis_id"], result["analysis_id"])
            self.assertEqual(loaded["component_releases"]["pce0"], "PCE0_RUSLE_SCREENING_ONLY")
            self.assertEqual(loaded["component_releases"]["pcx0"], "PCX0_EVENT_MASS_BALANCE_ONLY")
            self.assertFalse(output.with_name(f".{output.name}.tmp").exists())

    def test_committed_schemas_pin_non_authorized_release(self):
        request_schema = json.loads(
            (runner.ROOT / "schemas" / "pce-pcx-screening-request.schema.json").read_text(
                encoding="utf-8"
            )
        )
        result_schema = json.loads(
            (runner.ROOT / "schemas" / "pce-pcx-screening-stage.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            request_schema["properties"]["requested_release"]["const"],
            "PCE0_PCX0_NUMERIC_SCREENING_ONLY",
        )
        self.assertEqual(
            result_schema["properties"]["release"]["const"],
            "PCE0_PCX0_SCREENING_NOT_DESIGN",
        )
        claims = result_schema["properties"]["authorization_claims"]["properties"]
        self.assertTrue(all(record["const"] is False for record in claims.values()))


if __name__ == "__main__":
    unittest.main()
