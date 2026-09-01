import copy
import unittest

from scripts import validate_project_inputs as validator


class ProjectInputCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = validator.load_json(validator.DEFAULT_CATALOG)
        cls.catalog_by_id = validator.validate_catalog(cls.catalog)

    def test_request_schema_pins_current_parameter_catalog_revision(self):
        schema = validator.load_json(validator.DEFAULT_SCHEMA)
        validator.validate_schema(schema, self.catalog)
        self.assertEqual(
            validator.CATALOG_VERSION,
            schema["$defs"]["revisions"]["properties"]["parameter_catalog_revision"]["const"],
        )

    def test_crop_variety_is_structured_by_zone_not_operation_numeric_map(self):
        schema = self.catalog_by_id["agronomy.crop_variety_by_zone"]["value_schema"]
        value = [
            {
                "zone_id": "Z1",
                "variety_id": "RB-TEST",
                "maturity_group": "MEDIUM",
                "crop_cycle_stage": "RATOON",
            }
        ]
        validator._validate_value(value, schema, "crop")
        validator._validate_structured_parameter_semantics(
            "agronomy.crop_variety_by_zone", value
        )
        with self.assertRaises(validator.ContractError):
            validator._validate_value({"FURROW": 1.5}, schema, "legacy-bug")

    def test_row_pattern_carries_period_interval_roles_and_track_gauges(self):
        schema = self.catalog_by_id["agronomy.row_pattern_by_zone"]["value_schema"]
        value = [
            {
                "zone_id": "Z1",
                "valid_from": "2026-01-01",
                "valid_to": "2026-12-31",
                "row_pattern": "DOUBLE",
                "interval_sequence": [
                    {"interval_m": 0.9, "role": "CROP_TO_CROP"},
                    {"interval_m": 1.5, "role": "CROP_TO_TRAFFIC"},
                ],
                "compatible_track_gauges_m": [1.5, 1.8],
                "traffic_band_mode": "DEDICATED",
                "traffic_band_source": "EXISTING_GEOMETRY",
                "traffic_band_geometry_dataset_ref": "traffic-bands-v1",
            }
        ]
        validator._validate_value(value, schema, "row-pattern")
        validator._validate_structured_parameter_semantics(
            "agronomy.row_pattern_by_zone", value
        )

        malformed = copy.deepcopy(value)
        malformed[0]["interval_sequence"] = malformed[0]["interval_sequence"][:1]
        validator._validate_value(malformed, schema, "row-pattern")
        with self.assertRaises(validator.ContractError):
            validator._validate_structured_parameter_semantics(
                "agronomy.row_pattern_by_zone", malformed
            )

    def test_measured_parameter_rejects_declared_origin(self):
        parameter_id = "agronomy.correlated_environmental_measurements"
        parameter_value = {
            "parameter_id": parameter_id,
            "parameter_class": "USER_FACT",
            "unit": None,
            "value": [
                {
                    "observation_set_id": "ENV-1",
                    "dataset_ref": "environment-measurements",
                    "correlation_keys": [
                        "FIELD_ID",
                        "TIMESTAMP",
                        "OPERATION",
                        "OPERATIONAL_STATE",
                    ],
                    "measured_variables": ["SOIL_WATER_CONTENT"],
                    "environmental_condition_set_id": "WET-SOIL",
                    "variable_units": {"SOIL_WATER_CONTENT": "%"},
                    "variable_method_refs": {"SOIL_WATER_CONTENT": "method-tdr-v1"},
                    "water_content_basis": "VOLUMETRIC",
                    "qa_status": "REVIEWED",
                }
            ],
            "provenance": {
                "origin": "DECLARED",
                "source_ref": "client-note",
                "captured_at": "2026-08-20T12:00:00Z",
                "responsible": {
                    "name": "Test",
                    "organization": "Test",
                    "role": "Test",
                },
                "confidence": "HIGH",
                "revision": "1",
                "applicability": "FIELD",
            },
        }
        with self.assertRaises(validator.ContractError):
            validator._parameter_map(
                {
                    "requested_delivery_level": "E1_OPERACIONAL",
                    "parameter_values": [parameter_value],
                },
                self.catalog_by_id,
            )

    def test_articulated_envelope_rejects_duplicate_unit_axis(self):
        parameter_id = "fleet.control_execution_envelope_measurements"
        record = {
            "operation": "HARVEST",
            "operational_state": "LOADED",
            "configuration_id": "HARVESTER-WAGON",
            "environmental_observation_set_id": "ENV-1",
            "controller_gnss_measurements": {
                "controller_id": "CTRL-1",
                "firmware_revision": "FW-1",
                "correction_mode": "RTK_FIXED",
                "cross_track_error_p95_m": 0.04,
                "cross_track_error_max_m": 0.05,
                "heading_error_max_deg": 1.0,
                "latency_p95_s": 0.2,
                "test_ref": "controller-test-1",
            },
            "unit_geometries": [
                {
                    "unit_id": "WAGON-1",
                    "unit_role": "WAGON",
                    "body_length_m": 8.0,
                    "body_width_m": 3.0,
                    "execution_height_m": 4.0,
                    "reference_point": "CENTER",
                    "axes": [
                        {
                            "axis_id": "A1",
                            "axis_offset_from_unit_reference_m": 3.0,
                            "track_gauge_m": 2.5,
                            "wheel_or_track_width_m": 0.7,
                            "control_half_width_m": 0.05,
                            "execution_half_width_m": 1.8,
                        }
                    ],
                }
            ],
            "articulation_links": [],
            "measurement_ref": "envelope-survey-1",
        }
        schema = self.catalog_by_id[parameter_id]["value_schema"]
        empty_record = copy.deepcopy(record)
        empty_record["operational_state"] = "EMPTY"
        empty_record["measurement_ref"] = "envelope-survey-2"
        records = [record, empty_record]
        validator._validate_value(records, schema, "envelope")
        validator._validate_structured_parameter_semantics(parameter_id, records)
        duplicate = copy.deepcopy(records)
        duplicate[0]["unit_geometries"][0]["axes"].append(
            copy.deepcopy(duplicate[0]["unit_geometries"][0]["axes"][0])
        )
        with self.assertRaises(validator.ContractError):
            validator._validate_structured_parameter_semantics(
                parameter_id, duplicate
            )

    def test_environmental_measurements_require_method_unit_and_water_basis(self):
        parameter_id = "agronomy.correlated_environmental_measurements"
        record = {
            "observation_set_id": "ENV-1",
            "environmental_condition_set_id": "WET-SOIL",
            "dataset_ref": "environment-measurements",
            "correlation_keys": ["ZONE_ID", "TIMESTAMP", "OPERATION", "OPERATIONAL_STATE"],
            "measured_variables": ["SOIL_WATER_CONTENT", "SOIL_BEARING_CAPACITY"],
            "variable_units": {"SOIL_WATER_CONTENT": "%", "SOIL_BEARING_CAPACITY": "kPa"},
            "variable_method_refs": {
                "SOIL_WATER_CONTENT": "method-tdr-v1",
                "SOIL_BEARING_CAPACITY": "method-plate-test-v1",
            },
            "water_content_basis": "VOLUMETRIC",
            "qa_status": "VERIFIED",
        }
        schema = self.catalog_by_id[parameter_id]["value_schema"]
        validator._validate_value([record], schema, "environment")
        validator._validate_structured_parameter_semantics(parameter_id, [record])

        ambiguous = copy.deepcopy(record)
        ambiguous["water_content_basis"] = "NOT_APPLICABLE"
        with self.assertRaises(validator.ContractError):
            validator._validate_structured_parameter_semantics(parameter_id, [ambiguous])

    def test_track_gauge_rule_is_checked_against_both_load_states(self):
        row_pattern = {
            "zone_id": "Z1",
            "valid_from": "2026-01-01",
            "valid_to": "2026-12-31",
            "row_pattern": "SINGLE",
            "interval_sequence": [{"interval_m": 1.5, "role": "CROP_TO_CROP"}],
            "compatible_track_gauges_m": [1.5],
            "traffic_band_mode": "NONE",
            "traffic_band_source": "NOT_APPLICABLE",
        }
        state_records = []
        gauge_rules = []
        for state in ("LOADED", "EMPTY"):
            state_records.append(
                {
                    "operation": "HARVEST",
                    "operational_state": state,
                    "configuration_id": "HARVESTER-1",
                    "gross_mass_t": 20.0,
                    "unit_axis_loads": [
                        {"unit_id": "H1", "axis_id": "A1", "load_t": 10.0, "track_gauge_m": 1.5}
                    ],
                    "measurement_ref": f"mass-{state.lower()}",
                }
            )
            gauge_rules.append(
                {
                    "zone_id": "Z1",
                    "operation": "HARVEST",
                    "operational_state": state,
                    "configuration_id": "HARVESTER-1",
                    "maximum_gauge_mismatch_m": 0.02,
                    "rule_ref": "gauge-rule-v1",
                }
            )
        validator._validate_structured_parameter_semantics(
            "fleet.operational_state_measurements", state_records
        )
        validator._validate_structured_parameter_semantics(
            "fleet.approved_track_gauge_compatibility_rules", gauge_rules
        )
        parameters = {
            "agronomy.row_pattern_by_zone": {"value": [row_pattern]},
            "fleet.operational_state_measurements": {"value": state_records},
            "fleet.approved_track_gauge_compatibility_rules": {"value": gauge_rules},
        }
        validator._validate_structured_parameter_links(parameters, {})

        incompatible = copy.deepcopy(parameters)
        incompatible["fleet.operational_state_measurements"]["value"][0]["unit_axis_loads"][0]["track_gauge_m"] = 1.8
        with self.assertRaises(validator.ContractError):
            validator._validate_structured_parameter_links(incompatible, {})

    def test_e1_requires_structured_machine_soil_and_row_evidence(self):
        request = validator.load_json(
            validator.REPO / "config" / "exemplo_pedido_e0_dataset_atual.json"
        )
        request["requested_delivery_level"] = "E1_OPERACIONAL"
        schema = validator.load_json(validator.DEFAULT_SCHEMA)
        with self.assertRaises(validator.ContractError) as context:
            validator.validate_request(request, schema, self.catalog_by_id)
        message = str(context.exception)
        self.assertIn("agronomy.row_pattern_by_zone", message)
        self.assertIn("fleet.operational_state_measurements", message)
        self.assertIn("fleet.approved_control_execution_envelope_rules", message)

    def test_current_e0_request_remains_compatible(self):
        request = validator.load_json(
            validator.REPO / "config" / "exemplo_pedido_e0_dataset_atual.json"
        )
        schema = validator.load_json(validator.DEFAULT_SCHEMA)
        result = validator.validate_request(request, schema, self.catalog_by_id)
        self.assertEqual(result["parameter_count"], 16)
        self.assertEqual(result["power_inventory_status"], "DECLARED_NONE")
        self.assertEqual(
            result["release_blockers"],
            ["CONSTRAINT_INVENTORY_INCOMPLETE"],
        )


if __name__ == "__main__":
    unittest.main()
