import unittest

from scripts.validate_operational_surfaces import validate_manifest


class OperationalSurfaceContractTests(unittest.TestCase):
    def test_authorized_maneuver_requires_fleet_and_evidence(self):
        manifest = {
            "schema_version": "1.0.0",
            "surface_layers": [
                {
                    "surface_id": "H1",
                    "surface_type": "HEADLAND",
                    "dataset_ref": "headland.gpkg",
                    "geometry_type": "Polygon",
                    "review_status": "APPROVED",
                    "termination_policy": "SUPPORTED",
                    "maneuver_policy": "AUTHORIZED",
                    "operations": ["FURROW", "PLANT"],
                    "fleet_profile_ref": "fleet-v1",
                    "evidence_ref": "swept-envelope-v1",
                }
            ],
        }
        self.assertEqual(validate_manifest(manifest)["authorized_maneuver_surface_count"], 1)

    def test_power_edge_cannot_authorize_maneuver(self):
        manifest = {
            "schema_version": "1.0.0",
            "surface_layers": [
                {
                    "surface_id": "P1",
                    "surface_type": "POWER_BARRIER_EDGE",
                    "dataset_ref": "power.gpkg",
                    "geometry_type": "LineString",
                    "review_status": "APPROVED",
                    "termination_policy": "REQUIRED_BREAK",
                    "maneuver_policy": "AUTHORIZED",
                    "operations": ["FURROW"],
                    "fleet_profile_ref": "fleet-v1",
                    "evidence_ref": "wrong",
                }
            ],
        }
        with self.assertRaisesRegex(RuntimeError, "cannot authorize"):
            validate_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
