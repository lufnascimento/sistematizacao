import json
import unittest

from scripts.verify_embedded_terrace_screening import ContractError, run_self_test, validate_forbidden_claims


class EmbeddedTerraceScreeningVerifierTests(unittest.TestCase):
    def test_self_test_rejects_hydraulic_td_and_guidance_claims(self):
        result = run_self_test()
        self.assertEqual(result["status"], "SELF_TEST_VERIFIED")
        self.assertIn("INTERVAL_GRID_TAMPERING_REJECTED", result["checks"])
        self.assertIn("HYDRAULIC_CLAIM_REJECTED", result["checks"])
        self.assertIn("TD_GEOMETRY_REJECTED", result["checks"])
        self.assertIn("GUIDANCE_CLAIM_REJECTED", result["checks"])

    def test_missing_release_limitation_is_rejected(self):
        # Reuse the verifier's own valid synthetic fixture, then remove a hard limit.
        from scripts import verify_embedded_terrace_screening as verifier

        original = verifier.validate_forbidden_claims
        self.assertIs(original, validate_forbidden_claims)
        # A compact malformed object reaches the limitation gate before deeper checks.
        payload = {
            "release": verifier.RELEASE,
            "release_limitations": sorted(verifier.EXPECTED_LIMITATIONS - {"NOT_FOR_GUIDANCE"}),
        }
        with self.assertRaisesRegex(ContractError, "Release limitations changed"):
            validate_forbidden_claims(json.loads(json.dumps(payload)))


if __name__ == "__main__":
    unittest.main()
