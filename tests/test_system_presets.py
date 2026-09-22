import copy
import unittest

from scripts import audit_project_readiness as readiness
from scripts import audit_system_presets as presets
from scripts import resolve_project_presets as resolver


class SystemPresetCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parameter_catalog = presets.load_json(presets.DEFAULT_PARAMETER_CATALOG)
        cls.acquisition_catalog = presets.load_json(presets.DEFAULT_ACQUISITION_CATALOG)
        cls.model_catalog = presets.load_json(presets.DEFAULT_MODEL_CATALOG)
        cls.preset_catalog = presets.load_json(presets.DEFAULT_PRESET_CATALOG)
        cls.selection = presets.load_json(resolver.DEFAULT_SELECTION)

        cls.parameters = readiness.validate_parameter_catalog(cls.parameter_catalog)
        cls.packages = readiness.validate_acquisition_catalog(
            cls.acquisition_catalog, cls.parameters
        )
        cls.sources, cls.models = presets.validate_model_catalog(
            cls.model_catalog, cls.parameters, cls.packages
        )
        cls.package_profiles, cls.standalone_profiles = presets.validate_preset_catalog(
            cls.preset_catalog,
            cls.parameters,
            cls.packages,
            cls.models,
        )

    def _resolve_selection(self, selection):
        package_selections, standalone_selections, custom_values = resolver.validate_selection(
            selection,
            self.parameters,
            self.packages,
            self.models,
            self.package_profiles,
            self.standalone_profiles,
        )
        return resolver.build_resolution(
            selection,
            self.parameters,
            self.packages,
            self.models,
            self.package_profiles,
            package_selections,
            standalone_selections,
            custom_values,
        )

    def test_every_configurable_parameter_has_a_system_or_custom_path(self):
        report = presets.build_option_matrix(
            self.parameters,
            self.packages,
            self.sources,
            self.models,
            self.package_profiles,
            self.standalone_profiles,
        )
        self.assertEqual(153, report["summary"]["parameter_count"])
        self.assertEqual(141, report["summary"]["configurable_parameter_count"])
        self.assertEqual(129, report["summary"]["external_parameter_count"])
        self.assertEqual(12, report["summary"]["calculated_parameter_count"])
        self.assertEqual(26, report["summary"]["evidence_package_count"])
        self.assertEqual(65, report["summary"]["reference_model_count"])
        self.assertEqual(141, report["summary"]["custom_value_supported_count"])
        self.assertEqual(0, report["summary"]["project_evidence_resolved_by_presets"])
        self.assertEqual(
            {parameter_id for parameter_id, item in self.parameters.items() if item["parameter_class"] != "CALCULATED"},
            {item["parameter_id"] for item in report["parameters"]},
        )

    def test_fail_closed_values_exactly_match_parameter_contract(self):
        checked = 0
        for model in self.models.values():
            for candidate in model["parameter_candidates"]:
                if candidate["resolution_mode"] != "FAIL_CLOSED_VALUE":
                    continue
                parameter = self.parameters[candidate["parameter_id"]]
                self.assertEqual("FAIL_CLOSED", parameter["default"]["policy"])
                self.assertTrue(parameter["default"]["default_is_fail_closed"])
                self.assertEqual(parameter["default"]["value"], candidate["value"])
                checked += 1
        self.assertGreaterEqual(checked, 17)

    def test_public_oem_and_study_models_never_release_a_project(self):
        external_kinds = {
            "OFFICIAL_DATA",
            "OFFICIAL_METHOD",
            "OFFICIAL_MARKET_CENSUS",
            "OEM",
            "LAW_OR_STANDARD",
            "PRIMARY_RESEARCH",
        }
        checked = 0
        for model in self.models.values():
            kinds = {self.sources[source_id]["source_kind"] for source_id in model["source_ids"]}
            if not kinds & external_kinds:
                continue
            self.assertIn(model["release_ceiling"], {"E0_TRIAGEM", "NO_PROJECT_RELEASE"})
            checked += 1
        self.assertGreater(checked, 30)

    def test_published_market_references_are_traceable_and_not_recommendations(self):
        row = self.models["SYS-CANA-ROW-SIMPLE-1P50-E0"]
        row_candidate = next(
            item for item in row["parameter_candidates"]
            if item["parameter_id"] == "agronomy.row_spacing_m"
        )
        self.assertEqual(1.5, row_candidate["value"])
        self.assertTrue(row_candidate["requires_user_confirmation"])

        varieties = self.models["SYS-CANA-VARIETY-CENSUS-CS-2024-E0"]
        self.assertEqual("MARKET_CENSUS_RANKED", varieties["market_position"])
        self.assertEqual(6_173_189, varieties["model_data"]["surveyed_area_ha"])
        self.assertEqual(
            [("CTC4", 12.6), ("RB966928", 10.7), ("RB867515", 10.3), ("RB975242", 6.0)],
            [(item["id"], item["share_pct"]) for item in varieties["model_data"]["varieties"]],
        )
        self.assertIn(
            "major_oem_examples_are_not_market_share_rankings",
            self.model_catalog["market_claim_policy"]["rules"],
        )

    def test_esd_is_process_only_and_fails_closed_without_project_overlay(self):
        esd = self.models["SYS-CONSERVATION-ESD-PROCESS-CLOSED"]
        self.assertEqual("PROCESS_ONLY", esd["market_position"])
        self.assertEqual("NO_PROJECT_RELEASE", esd["release_ceiling"])
        self.assertFalse(esd["model_data"]["automatic_numeric_solver"])
        self.assertTrue(esd["model_data"]["project_overlay_required"])
        self.assertEqual("FAIL_CLOSED", esd["model_data"]["execution_status_without_overlay"])

        report = self._resolve_selection(copy.deepcopy(self.selection))
        conservation = next(
            item for item in report["package_resolutions"]
            if item["package_id"] == "CONSERVATION_RULE_PACK"
        )
        self.assertEqual("NO_PROJECT_RELEASE", conservation["release_ceiling"])
        self.assertFalse(report["delivery_gates"]["E2_CONSERVACIONISTA"]["eligible_from_preset_resolution"])
        self.assertFalse(report["delivery_gates"]["E3_EXECUTIVO"]["eligible_from_preset_resolution"])

    def test_bt71_is_parana_pce_only(self):
        model = self.models["SYS-CONSERVATION-PR-BT71-CANA-PCE"]
        self.assertEqual(["PR"], model["applicability"]["jurisdiction"])
        self.assertEqual(4, model["model_data"]["crop_group"])
        self.assertEqual([12, 16, 20, 24, 28], model["model_data"]["table_numbers"])
        self.assertIn("PCX_SECTION", model["model_data"]["not_supported"])
        self.assertIn("ESD", model["model_data"]["not_supported"])

    def test_wrong_state_rejects_regional_method(self):
        selection = copy.deepcopy(self.selection)
        conservation = next(
            item for item in selection["package_selections"]
            if item["package_id"] == "CONSERVATION_RULE_PACK"
        )
        conservation["model_ids"] = ["SYS-CONSERVATION-PR-BT71-CANA-PCE"]
        with self.assertRaisesRegex(readiness.ContractError, "does not apply"):
            resolver.validate_selection(
                selection,
                self.parameters,
                self.packages,
                self.models,
                self.package_profiles,
                self.standalone_profiles,
            )

    def test_custom_value_overrides_system_prior_but_does_not_resolve_evidence(self):
        report = self._resolve_selection(copy.deepcopy(self.selection))
        row = next(
            item for item in report["parameters"]
            if item["parameter_id"] == "agronomy.row_spacing_m"
        )
        self.assertEqual("CUSTOM_VALUE_PENDING_EVIDENCE_VALIDATION", row["status"])
        self.assertEqual(1.5, row["proposed_value"])
        self.assertEqual("demo-agronomist", row["custom_provenance"]["responsible"])
        self.assertFalse(row["project_evidence_resolved"])
        self.assertEqual(0, report["summary"]["project_evidence_resolved_by_selection"])

    def test_operation_keyed_oem_heights_compose_without_losing_context(self):
        report = self._resolve_selection(copy.deepcopy(self.selection))
        height = next(
            item for item in report["parameters"]
            if item["parameter_id"] == "fleet.maximum_operating_height_m"
        )
        self.assertEqual({"PLANT": 3.0, "TRANSSHIPMENT": 4.7}, height["proposed_value"])
        self.assertTrue(height["safety_critical"])
        self.assertFalse(height["project_evidence_resolved"])

    def test_presets_do_not_change_current_project_readiness(self):
        report = readiness.build_report(
            readiness.load_json(readiness.DEFAULT_PARAMETER_CATALOG),
            readiness.load_json(readiness.DEFAULT_ACQUISITION_CATALOG),
            readiness.load_json(readiness.DEFAULT_CAPABILITY_CATALOG),
            readiness.load_json(readiness.DEFAULT_INVENTORY),
        )
        self.assertTrue(report["delivery_readiness"]["E0_TRIAGEM"]["ready"])
        self.assertFalse(report["delivery_readiness"]["E1_OPERACIONAL"]["ready"])
        self.assertFalse(report["delivery_readiness"]["E2_CONSERVACIONISTA"]["ready"])
        self.assertFalse(report["delivery_readiness"]["E3_EXECUTIVO"]["ready"])
        self.assertEqual(116, report["summary"]["unresolved_external_parameter_count"])


if __name__ == "__main__":
    unittest.main()
