import copy
import unittest

from scripts import audit_project_readiness as readiness


class ProjectReadinessAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parameter_catalog = readiness.load_json(readiness.DEFAULT_PARAMETER_CATALOG)
        cls.acquisition_catalog = readiness.load_json(readiness.DEFAULT_ACQUISITION_CATALOG)
        cls.capability_catalog = readiness.load_json(readiness.DEFAULT_CAPABILITY_CATALOG)
        cls.inventory = readiness.load_json(readiness.DEFAULT_INVENTORY)

    def test_committed_contracts_cover_every_external_parameter(self):
        parameters = readiness.validate_parameter_catalog(self.parameter_catalog)
        packages = readiness.validate_acquisition_catalog(self.acquisition_catalog, parameters)
        capabilities = readiness.validate_capability_catalog(self.capability_catalog, packages)
        entries, assessments = readiness.validate_inventory(
            self.inventory,
            packages,
            capabilities,
            parameters,
        )
        self.assertEqual(151, len(parameters))
        self.assertEqual(26, len(packages))
        self.assertEqual(set(packages), set(entries))
        self.assertEqual(set(capabilities), set(assessments))

    def test_current_dataset_is_e0_only(self):
        report = readiness.build_report(
            self.parameter_catalog,
            self.acquisition_catalog,
            self.capability_catalog,
            self.inventory,
        )
        self.assertTrue(report["delivery_readiness"]["E0_TRIAGEM"]["ready"])
        self.assertFalse(report["delivery_readiness"]["E1_OPERACIONAL"]["ready"])
        self.assertFalse(report["delivery_readiness"]["E2_CONSERVACIONISTA"]["ready"])
        self.assertFalse(report["delivery_readiness"]["E3_EXECUTIVO"]["ready"])
        self.assertEqual(18, report["summary"]["evidence_status_counts"]["MISSING"])
        self.assertEqual(1, report["summary"]["evidence_status_counts"]["NOT_APPLICABLE"])
        self.assertEqual(116, report["summary"]["unresolved_external_parameter_count"])

    def test_missing_input_and_missing_solver_are_reported_separately(self):
        report = readiness.build_report(
            self.parameter_catalog,
            self.acquisition_catalog,
            self.capability_catalog,
            self.inventory,
        )
        c1 = report["scenario_readiness"]["C1_CURVA_EMBUTIDA"]
        self.assertIn("SOIL_PROFILE_HYDRAULIC", c1["evidence_blockers"])
        self.assertIn("SOIL_OPERATION_STATE", c1["evidence_blockers"])
        self.assertIn("C1_CURVA_EMBUTIDA", c1["implementation_blockers"])
        cf0 = report["scenario_readiness"]["CF0_GEOMETRY"]
        self.assertEqual([], cf0["implementation_blockers"])
        self.assertIn("TERRAIN_SURFACE_CONTEXT", cf0["evidence_blockers"])

    def test_declared_absent_power_is_not_a_scenario_blocker(self):
        report = readiness.build_report(
            self.parameter_catalog,
            self.acquisition_catalog,
            self.capability_catalog,
            self.inventory,
        )
        for scenario in readiness.SCENARIOS:
            self.assertNotIn(
                "POWER_CONSTRAINT",
                report["scenario_readiness"][scenario]["evidence_blockers"],
            )

    def test_scenario_readiness_closes_capability_evidence_dependencies(self):
        report = readiness.build_report(
            self.parameter_catalog,
            self.acquisition_catalog,
            self.capability_catalog,
            self.inventory,
        )
        c3 = report["scenario_readiness"]["C3_ESD"]
        self.assertIn("SOIL_OPERATION_STATE", c3["required_evidence_packages"])
        self.assertIn("SOIL_OPERATION_STATE", c3["evidence_blockers"])

    def test_public_data_cannot_release_a_project_by_itself(self):
        catalog = copy.deepcopy(self.acquisition_catalog)
        package = next(item for item in catalog["evidence_packages"] if item["id"] == "RAINFALL_DESIGN")
        public_mode = next(item for item in package["acquisition_modes"] if item["mode"] == "OFFICIAL_DATA")
        public_mode["can_release"] = True
        parameters = readiness.validate_parameter_catalog(self.parameter_catalog)
        with self.assertRaisesRegex(readiness.ContractError, "Public screening data cannot release"):
            readiness.validate_acquisition_catalog(catalog, parameters)

    def test_available_evidence_requires_a_reference(self):
        inventory = copy.deepcopy(self.inventory)
        entry = next(item for item in inventory["entries"] if item["package_id"] == "TERRAIN_SURFACE_CONTEXT")
        entry["evidence_refs"] = []
        parameters = readiness.validate_parameter_catalog(self.parameter_catalog)
        packages = readiness.validate_acquisition_catalog(self.acquisition_catalog, parameters)
        capabilities = readiness.validate_capability_catalog(self.capability_catalog, packages)
        with self.assertRaisesRegex(readiness.ContractError, "Available evidence needs a reference"):
            readiness.validate_inventory(inventory, packages, capabilities, parameters)


if __name__ == "__main__":
    unittest.main()
