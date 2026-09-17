import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from terraflux_api.models import SulcationConfiguration


class SmoothingConfigurationTests(unittest.TestCase):
    def test_legacy_radius_is_not_reinterpreted_as_sigma(self):
        config = SulcationConfiguration(terrain_smoothing_radius_m=17)
        self.assertEqual(config.terrain_smoothing_sigma_m, 4)
        self.assertEqual(config.model_dump()["terrain_smoothing_radius_m"], 17)

    def test_sigma_round_trip_including_disabled_smoothing(self):
        for sigma in (0, 2.5, 100):
            with self.subTest(sigma=sigma):
                config = SulcationConfiguration(terrain_smoothing_sigma_m=sigma)
                restored = SulcationConfiguration.model_validate_json(config.model_dump_json())
                self.assertEqual(restored.terrain_smoothing_sigma_m, sigma)

    def test_invalid_sigma_is_rejected(self):
        for sigma in (-1, 101, float("nan"), float("inf")):
            with self.subTest(sigma=sigma), self.assertRaises(ValidationError):
                SulcationConfiguration(terrain_smoothing_sigma_m=sigma)
