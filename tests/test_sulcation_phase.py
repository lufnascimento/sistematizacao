import unittest

import numpy as np

from scripts.sulcation_phase import adaptive_phase_levels, physical_spacing_from_levels


class AdaptivePhaseLevelsTests(unittest.TestCase):
    def test_unit_gradient_keeps_requested_step(self):
        phi = np.tile(np.arange(0.0, 15.1, 0.1)[:, None], (1, 4))
        magnitude = np.ones_like(phi)
        levels = adaptive_phase_levels(phi, magnitude, np.ones_like(phi, dtype=bool), 1.5)
        np.testing.assert_allclose(np.diff(levels[:-1]), 1.5, atol=1e-9)

    def test_constant_scaled_gradient_targets_physical_spacing(self):
        phi = np.tile(np.arange(0.0, 30.1, 0.2)[:, None], (1, 4))
        magnitude = np.full_like(phi, 2.0)
        levels = adaptive_phase_levels(phi, magnitude, np.ones_like(phi, dtype=bool), 1.5)
        physical_spacing = np.diff(levels[:-1]) / 2.0
        np.testing.assert_allclose(physical_spacing, 1.5, atol=1e-9)

        local_spacing = physical_spacing_from_levels(
            phi,
            magnitude,
            np.ones_like(phi, dtype=bool),
            levels,
        )
        np.testing.assert_allclose(local_spacing, 1.5, atol=1e-9)

    def test_spacing_proxy_uses_each_extracted_level_interval(self):
        phi = np.asarray([0.2, 1.4, 1.6, 4.2])
        magnitude = np.asarray([1.0, 1.0, 2.0, 2.0])
        levels = np.asarray([0.0, 1.5, 4.5])
        spacing = physical_spacing_from_levels(
            phi,
            magnitude,
            np.ones_like(phi, dtype=bool),
            levels,
        )
        np.testing.assert_allclose(spacing, [1.5, 1.5, 1.5, 1.5])

    def test_variable_gradient_along_isoline_remains_a_documented_limitation(self):
        x = np.linspace(0.0, 20.0, 201)
        y = np.linspace(0.0, 20.0, 201)
        x_grid, y_grid = np.meshgrid(x, y)
        phi = y_grid * (1.0 + 0.05 * x_grid)
        phi_x = 0.05 * y_grid
        phi_y = 1.0 + 0.05 * x_grid
        magnitude = np.hypot(phi_x, phi_y)
        levels = adaptive_phase_levels(phi, magnitude, np.ones_like(phi, dtype=bool), 1.5)

        representative_delta = float(np.median(np.diff(levels)))
        local_spacing = representative_delta / (1.0 + 0.05 * x)
        outside = np.mean((local_spacing < 1.2) | (local_spacing > 1.8))
        self.assertGreater(outside, 0.20)
        self.assertAlmostEqual(float(np.median(local_spacing)), 1.5, delta=0.25)


if __name__ == "__main__":
    unittest.main()
