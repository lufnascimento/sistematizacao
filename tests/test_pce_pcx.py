import unittest

from scripts.pce_pcx import (
    PcxInterval,
    RusleFactors,
    calculate_pce0_rusle,
    calculate_pcx0_mass_balance,
    validate_release_boundary,
)


class PcePcxKernelTests(unittest.TestCase):
    def test_pce_factor_product_and_declared_tolerance_are_deterministic(self):
        result = calculate_pce0_rusle(
            RusleFactors(
                rainfall_erosivity_r=1000.0,
                soil_erodibility_k=0.02,
                slope_length_steepness_ls=1.5,
                cover_management_c=0.1,
                support_practice_p=0.5,
                soil_loss_tolerance_t_ha_year=2.0,
            )
        )

        self.assertAlmostEqual(result["estimated_soil_loss_t_ha_year"], 1.5)
        self.assertAlmostEqual(result["soil_loss_to_tolerance_ratio"], 0.75)
        self.assertEqual(result["screening_status"], "WITHIN_DECLARED_TOLERANCE_SCREENING")
        self.assertFalse(result["hydraulic_capacity_evaluated"])
        validate_release_boundary(result)

    def test_pce_without_tolerance_does_not_invent_a_classification(self):
        result = calculate_pce0_rusle(RusleFactors(500.0, 0.03, 2.0, 0.2, 0.8))
        self.assertEqual(result["screening_status"], "TOLERANCE_NOT_DECLARED")
        self.assertIsNone(result["soil_loss_to_tolerance_ratio"])

    def test_pce_rejects_invalid_dimensionless_factors(self):
        with self.assertRaisesRegex(ValueError, "in \[0, 1\]"):
            calculate_pce0_rusle(RusleFactors(500.0, 0.03, 2.0, 1.1, 0.8))

    def test_pcx_closes_an_analytic_two_interval_event(self):
        result = calculate_pcx0_mass_balance(
            1.0,
            [
                PcxInterval(600.0, 10.0, 0.0, 40.0, 0.0, 60.0),
                PcxInterval(600.0, 0.0, 20.0, 50.0, 60.0, 30.0),
            ],
        )

        self.assertAlmostEqual(result["rainfall_excess_volume_m3"], 100.0)
        self.assertAlmostEqual(result["total_input_volume_m3"], 120.0)
        self.assertAlmostEqual(result["controlled_outflow_volume_m3"], 90.0)
        self.assertAlmostEqual(result["storage_change_m3"], 30.0)
        self.assertAlmostEqual(result["mass_balance_residual_m3"], 0.0)
        self.assertEqual(result["numeric_continuity_status"], "PASS")
        self.assertFalse(result["hydraulic_capacity_evaluated"])
        validate_release_boundary(result)

    def test_pcx_reports_imbalance_without_calling_it_hydraulic_failure(self):
        result = calculate_pcx0_mass_balance(
            1.0,
            [PcxInterval(600.0, 10.0, 0.0, 20.0, 0.0, 60.0)],
            relative_residual_tolerance=0.01,
        )
        self.assertEqual(result["numeric_continuity_status"], "FAIL")
        self.assertAlmostEqual(result["mass_balance_residual_m3"], 20.0)
        self.assertFalse(result["hydraulic_capacity_evaluated"])

    def test_pcx_rejects_discontinuous_storage(self):
        with self.assertRaisesRegex(ValueError, "storage is not continuous"):
            calculate_pcx0_mass_balance(
                1.0,
                [
                    PcxInterval(60.0, 1.0, 0.0, 0.0, 0.0, 10.0),
                    PcxInterval(60.0, 0.0, 0.0, 0.0, 9.0, 9.0),
                ],
            )

    def test_release_boundary_rejects_promotion(self):
        result = calculate_pce0_rusle(RusleFactors(500.0, 0.03, 2.0, 0.2, 0.8))
        result["guidance_authorized"] = True
        with self.assertRaisesRegex(ValueError, "guidance_authorized cannot be true"):
            validate_release_boundary(result)


if __name__ == "__main__":
    unittest.main()
