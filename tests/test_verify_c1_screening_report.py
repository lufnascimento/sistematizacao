import unittest

from scripts.generate_c1_screening_report import SEAL, SOURCE_RELEASE
from scripts.verify_c1_screening_report import VerificationError, validate_release_boundary


def boundary():
    return {
        "document_role": "C1_E0_GEOMETRIC_SENSITIVITY_COMPARISON",
        "release": SOURCE_RELEASE,
        "seal": SEAL,
        "ti_status": "GENERATED_SCREENING_ONLY_NOT_DIMENSIONED",
        "td_status": "NOT_GENERATED_RECEIVER_MISSING",
        "pce_status": "NOT_EVALUATED",
        "pcx_status": "NOT_EVALUATED",
        "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
        "guidance_status": "NOT_AUTHORIZED",
        "power_inventory_status": "DECLARED_NONE",
        "power_constraint_effect": "NOT_APPLICABLE_DECLARED_NONE",
        "general_constraint_inventory_status": "NOT_REVIEWED",
    }


class C1ScreeningReportVerifierTests(unittest.TestCase):
    def test_release_boundary_accepts_declared_none_as_non_blocking(self):
        validate_release_boundary(boundary())

    def test_release_boundary_rejects_td_geometry_promotion(self):
        value = boundary()
        value["td_status"] = "GENERATED"
        with self.assertRaises(VerificationError):
            validate_release_boundary(value)

    def test_release_boundary_rejects_guidance_authorization(self):
        value = boundary()
        value["guidance_status"] = "AUTHORIZED"
        with self.assertRaises(VerificationError):
            validate_release_boundary(value)

    def test_release_boundary_rejects_power_regression(self):
        value = boundary()
        value["power_constraint_effect"] = "BLOCKED_REQUIRES_BARRIER_STAGE"
        with self.assertRaises(VerificationError):
            validate_release_boundary(value)


if __name__ == "__main__":
    unittest.main()
