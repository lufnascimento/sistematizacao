"""Validate the project parameter catalog and generation-request contract."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = REPO / "config" / "catalogo_parametros_projeto.json"
DEFAULT_SCHEMA = REPO / "schemas" / "project-generation-request.schema.json"

CATALOG_VERSION = "1.2.0"
REQUEST_VERSION = "1.0.0"
PARAMETER_CLASSES = {
    "USER_FACT",
    "CALCULATED",
    "RULE_PACK",
    "OPTIMIZER",
    "E0_ASSUMPTION",
}
CLASS_ORIGINS = {
    "USER_FACT": {"DECLARED", "UPLOADED", "MEASURED", "IMPORTED"},
    "CALCULATED": {"CALCULATED"},
    "RULE_PACK": {"RULE_PACK", "PROFESSIONAL_CALIBRATION"},
    "OPTIMIZER": {"DECLARED", "OPTIMIZER"},
    "E0_ASSUMPTION": {"E0_ASSUMPTION"},
}
EXPECTED_CATEGORIES = {
    "scope",
    "terrain",
    "agronomy",
    "conservation",
    "fleet",
    "constraints",
    "connections",
    "poa",
    "logistics",
    "objectives",
    "qa",
    "e0",
}
POWER_STATUS_ID = "constraints.overhead_power_line_inventory_status"
POWER_AXIS_ID = "constraints.overhead_power_line_axis_dataset_ref"
POWER_WIDTH_ID = "constraints.overhead_power_line_exclusion_half_width_m"
POWER_WORK_ID = "constraints.overhead_power_line_work_behavior"
POWER_TRANSIT_ID = "constraints.overhead_power_line_transit_policy"
POWER_LAYER_TYPE = "OVERHEAD_POWER_LINE_AXIS"

CORE_PARAMETER_IDS = {
    "scope.target_field_ids",
    "scope.target_property_ids",
    "scope.allow_cross_field_work",
    "scope.allow_cross_property_work",
    "scope.cross_property_permission_status",
    "terrain.dtm_dataset_ref",
    "terrain.horizontal_crs",
    "terrain.vertical_reference",
    "terrain.vertical_accuracy_m",
    "terrain.coverage_status",
    "agronomy.row_spacing_m",
    "agronomy.row_pattern_by_zone",
    "agronomy.crop_variety_by_zone",
    "agronomy.correlated_environmental_measurements",
    "agronomy.expected_yield_t_ha",
    "agronomy.soil_zones_dataset_ref",
    "conservation.design_rainfall_ref",
    "conservation.max_furrow_grade_pct",
    "conservation.max_hydraulic_reach_m",
    "conservation.receiver_inventory_status",
    "fleet.profile_dataset_ref",
    "fleet.operations_in_scope",
    "fleet.maximum_operating_height_m",
    "fleet.minimum_turn_radius_m",
    "fleet.minimum_work_path_radius_m",
    "fleet.curvature_transition_limit_ref",
    "fleet.swept_envelope_dataset_ref",
    "fleet.operational_state_measurements",
    "fleet.control_execution_envelope_measurements",
    "fleet.approved_control_execution_envelope_rules",
    "fleet.approved_track_gauge_compatibility_rules",
    "fleet.approved_operating_rules_by_state_and_environment",
    "constraints.inventory_review_status",
    POWER_STATUS_ID,
    POWER_AXIS_ID,
    POWER_WIDTH_ID,
    POWER_WORK_ID,
    POWER_TRANSIT_ID,
    "constraints.power_barrier_geometry_ref",
    "constraints.roads_and_carriers_dataset_ref",
    "constraints.operational_surfaces_dataset_ref",
    "constraints.internal_work_stop_policy",
    "constraints.water_environment_dataset_ref",
    "constraints.linear_utilities_dataset_ref",
    "constraints.irrigation_and_structures_dataset_ref",
    "constraints.soft_soil_flood_and_erosion_dataset_ref",
    "connections.allowed_boundary_ids",
    "connections.portal_dataset_ref",
    "connections.portal_min_width_m",
    "connections.max_gap_grade_pct",
    "connections.minimum_preferred_shot_length_m",
    "connections.operational_graph_ref",
    "poa.existing_sites_dataset_ref",
    "poa.candidate_areas_dataset_ref",
    "poa.site_permission_status",
    "poa.maximum_site_count",
    "poa.minimum_platform_area_m2",
    "poa.maximum_platform_slope_pct",
    "poa.minimum_soil_bearing_kpa",
    "poa.drainage_approval_status",
    "poa.truck_queue_capacity",
    "poa.selected_sites_ref",
    "logistics.road_network_dataset_ref",
    "logistics.harvest_window",
    "logistics.harvester_capacity_t_h",
    "logistics.transshipment_capacity_t",
    "logistics.transshipment_unit_count",
    "logistics.truck_payload_t",
    "logistics.truck_unit_count",
    "logistics.transshipment_loaded_speed_kmh",
    "logistics.transshipment_empty_speed_kmh",
    "logistics.transfer_service_time_min",
    "logistics.road_and_bridge_capacity_status",
    "logistics.traffic_intensity_ref",
    "logistics.logistics_graph_ref",
    "objectives.selection_mode",
    "objectives.weights",
    "objectives.pareto_representative_count",
    "qa.rule_pack_revision",
    "qa.constraint_revision",
    "qa.field_review_status",
    "qa.professional_approval_status",
    "qa.release_block_on_unreviewed_constraints",
    "qa.uncertainty_set_ref",
    "e0.nominal_field_speed_kmh",
    "e0.maneuver_time_s",
    "e0.yield_proxy_t_ha",
}

MEASURED_PARAMETER_IDS = {
    "agronomy.correlated_environmental_measurements",
    "fleet.operational_state_measurements",
    "fleet.control_execution_envelope_measurements",
}

APPROVED_RULE_PARAMETER_IDS = {
    "fleet.approved_control_execution_envelope_rules",
    "fleet.approved_track_gauge_compatibility_rules",
    "fleet.approved_operating_rules_by_state_and_environment",
}

ALWAYS_REQUEST_PARAMETER_IDS = {
    "scope.target_field_ids",
    "scope.target_property_ids",
    "scope.allow_cross_field_work",
    "scope.allow_cross_property_work",
    "scope.cross_property_permission_status",
    "terrain.dtm_dataset_ref",
    "terrain.horizontal_crs",
    "agronomy.row_spacing_m",
    "fleet.operations_in_scope",
    "constraints.inventory_review_status",
    POWER_STATUS_ID,
    "connections.minimum_preferred_shot_length_m",
    "objectives.selection_mode",
    "qa.rule_pack_revision",
    "qa.constraint_revision",
    "qa.release_block_on_unreviewed_constraints",
}

POA_COMMON_PARAMETER_IDS = {
    "agronomy.expected_yield_t_ha",
    "agronomy.soil_wetness_and_trafficability_status",
    "poa.site_permission_status",
    "poa.maximum_platform_slope_pct",
    "poa.minimum_soil_bearing_kpa",
    "poa.truck_queue_capacity",
    "logistics.road_network_dataset_ref",
    "logistics.harvest_window",
    "logistics.shift_duration_h",
    "logistics.harvester_capacity_t_h",
    "logistics.transshipment_capacity_t",
    "logistics.transshipment_unit_count",
    "logistics.truck_payload_t",
    "logistics.truck_unit_count",
    "logistics.transshipment_loaded_speed_kmh",
    "logistics.transshipment_empty_speed_kmh",
    "logistics.truck_speed_profile_kmh",
    "logistics.transfer_service_time_min",
    "logistics.road_and_bridge_capacity_status",
}

E1_REQUIRED_PARAMETER_IDS = {
    "agronomy.crop_variety_by_zone",
    "agronomy.row_pattern_by_zone",
    "agronomy.correlated_environmental_measurements",
    "terrain.vertical_reference",
    "terrain.vertical_accuracy_m",
    "terrain.coverage_status",
    "fleet.profile_dataset_ref",
    "fleet.maximum_operating_height_m",
    "fleet.minimum_turn_radius_m",
    "fleet.minimum_work_path_radius_m",
    "fleet.curvature_transition_limit_ref",
    "fleet.swept_envelope_dataset_ref",
    "fleet.operational_state_measurements",
    "fleet.control_execution_envelope_measurements",
    "fleet.approved_control_execution_envelope_rules",
    "fleet.approved_track_gauge_compatibility_rules",
    "fleet.approved_operating_rules_by_state_and_environment",
    "fleet.required_headland_width_m",
    "fleet.max_cross_slope_pct",
    "fleet.max_longitudinal_grade_pct",
    "constraints.operational_surfaces_dataset_ref",
    "constraints.internal_work_stop_policy",
    "qa.field_review_status",
}

E2_REQUIRED_PARAMETER_IDS = {
    "agronomy.soil_zones_dataset_ref",
    "conservation.design_rainfall_ref",
    "conservation.max_furrow_grade_pct",
    "conservation.max_hydraulic_reach_m",
    "conservation.receiver_inventory_status",
    "constraints.water_environment_dataset_ref",
    "qa.professional_approval_status",
    "qa.uncertainty_set_ref",
}


class ContractError(RuntimeError):
    """Raised when a machine-readable contract violates an invariant."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> dict[str, Any]:
    require(path.is_file(), f"Missing JSON file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ContractError(f"Invalid JSON in {path}: {exc}") from exc
    require(isinstance(value, dict), f"Root JSON value must be an object: {path}")
    return value


def _by_id(items: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    require(all(isinstance(item, dict) for item in items), f"Every {label} entry must be an object.")
    ids = [item.get("id") for item in items]
    require(all(isinstance(item_id, str) and item_id for item_id in ids), f"Every {label} entry needs an id.")
    require(len(ids) == len(set(ids)), f"Duplicate {label} ids are not allowed.")
    return {item["id"]: item for item in items}


def _validate_value_schema_definition(fragment: Any, label: str) -> None:
    require(isinstance(fragment, dict) and fragment, f"{label} must be a non-empty object.")
    expected_type = fragment.get("type")
    supported_types = {"string", "number", "integer", "boolean", "array", "object", "null"}
    if isinstance(expected_type, list):
        require(expected_type and len(expected_type) == len(set(expected_type)), f"{label}.type is invalid.")
        require(set(expected_type) <= supported_types, f"{label}.type contains unsupported values.")
    else:
        require(expected_type in supported_types, f"{label}.type is unsupported or missing.")
    if "enum" in fragment:
        require(isinstance(fragment["enum"], list) and fragment["enum"], f"{label}.enum is invalid.")
    if "required" in fragment:
        required = fragment["required"]
        require(
            isinstance(required, list)
            and all(isinstance(name, str) and name for name in required)
            and len(required) == len(set(required)),
            f"{label}.required is invalid.",
        )
    properties = fragment.get("properties")
    if properties is not None:
        require(isinstance(properties, dict), f"{label}.properties must be an object.")
        for name, child in properties.items():
            require(isinstance(name, str) and name, f"{label}.properties has an invalid name.")
            _validate_value_schema_definition(child, f"{label}.properties.{name}")
        required_names = set(fragment.get("required", []))
        require(required_names <= set(properties), f"{label}.required references unknown properties.")
    items = fragment.get("items")
    if items is not None:
        _validate_value_schema_definition(items, f"{label}.items")
    additional = fragment.get("additionalProperties")
    require(
        additional is None or isinstance(additional, (bool, dict)),
        f"{label}.additionalProperties is invalid.",
    )
    if isinstance(additional, dict):
        _validate_value_schema_definition(additional, f"{label}.additionalProperties")
    property_names = fragment.get("propertyNames")
    if property_names is not None:
        require(isinstance(property_names, dict), f"{label}.propertyNames is invalid.")
        require(
            "enum" in property_names
            and isinstance(property_names["enum"], list)
            and property_names["enum"],
            f"{label}.propertyNames must declare a non-empty enum.",
        )


def validate_catalog(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    require(catalog.get("schema_version") == CATALOG_VERSION, "Unexpected parameter catalog version.")
    require(catalog.get("catalog_id") == "terraflux-project-parameter-catalog", "Unexpected catalog id.")

    class_items = catalog.get("parameter_classes")
    require(isinstance(class_items, list), "parameter_classes must be an array.")
    classes = _by_id(class_items, "parameter class")
    require(set(classes) == PARAMETER_CLASSES, "The five parameter classes are required.")
    all_origins: set[str] = set()
    for class_id, item in classes.items():
        origins = item.get("allowed_origins")
        require(isinstance(origins, list) and origins, f"{class_id} needs allowed_origins.")
        require(len(origins) == len(set(origins)), f"{class_id} has duplicate origins.")
        require(set(origins) == CLASS_ORIGINS[class_id], f"Origin taxonomy changed for {class_id}.")
        all_origins.update(origins)

    categories = catalog.get("categories")
    require(isinstance(categories, list), "categories must be an array.")
    require(set(categories) == EXPECTED_CATEGORIES, "Parameter categories are incomplete or unexpected.")

    provenance = catalog.get("provenance_contract")
    require(isinstance(provenance, dict), "Missing provenance_contract.")
    expected_provenance = {
        "origin",
        "source_ref",
        "captured_at",
        "responsible",
        "confidence",
        "revision",
        "applicability",
    }
    require(set(provenance.get("required_fields", [])) == expected_provenance, "Provenance fields are incomplete.")
    require(set(provenance.get("confidence_values", [])) == {"LOW", "MEDIUM", "HIGH", "VERIFIED"}, "Confidence enum changed.")

    default_contract = catalog.get("default_policy")
    require(isinstance(default_contract, dict), "Missing default_policy contract.")
    allowed_default_policies = set(default_contract.get("allowed_policies", []))
    require(allowed_default_policies == {"NO_DEFAULT", "FAIL_CLOSED", "E0_ONLY"}, "Default policies changed.")

    parameters = catalog.get("parameters")
    require(isinstance(parameters, list) and parameters, "parameters must be a non-empty array.")
    parameter_by_id = _by_id(parameters, "parameter")
    missing_core = sorted(CORE_PARAMETER_IDS - set(parameter_by_id))
    require(not missing_core, f"Missing core parameter ids: {', '.join(missing_core)}")

    id_pattern = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
    required_fields = {
        "id",
        "category",
        "parameter_class",
        "label",
        "value_schema",
        "unit",
        "safety_critical",
        "authority",
        "requirement",
        "default",
    }
    numeric_types = {"number", "integer"}
    for parameter_id, item in parameter_by_id.items():
        require(required_fields.issubset(item), f"{parameter_id} is missing catalog fields.")
        require(id_pattern.fullmatch(parameter_id) is not None, f"Invalid parameter id: {parameter_id}")
        require(item["category"] in EXPECTED_CATEGORIES, f"Unknown category on {parameter_id}.")
        require(parameter_id.startswith(f"{item['category']}."), f"Category/id mismatch on {parameter_id}.")
        parameter_class = item["parameter_class"]
        require(parameter_class in PARAMETER_CLASSES, f"Unknown class on {parameter_id}.")
        require(isinstance(item["value_schema"], dict) and item["value_schema"], f"Missing value_schema on {parameter_id}.")
        _validate_value_schema_definition(item["value_schema"], f"{parameter_id}.value_schema")
        require(isinstance(item["safety_critical"], bool), f"safety_critical must be boolean on {parameter_id}.")
        require(item["unit"] is None or isinstance(item["unit"], str), f"Invalid unit on {parameter_id}.")
        parameter_origins = item.get("allowed_origins", sorted(CLASS_ORIGINS[parameter_class]))
        require(
            isinstance(parameter_origins, list)
            and parameter_origins
            and all(isinstance(origin, str) for origin in parameter_origins)
            and len(parameter_origins) == len(set(parameter_origins))
            and set(parameter_origins) <= CLASS_ORIGINS[parameter_class],
            f"Invalid allowed_origins on {parameter_id}.",
        )

        default = item["default"]
        require(isinstance(default, dict), f"default must be an object on {parameter_id}.")
        policy = default.get("policy")
        require(policy in allowed_default_policies, f"Invalid default policy on {parameter_id}.")
        if policy == "NO_DEFAULT":
            require("value" not in default, f"NO_DEFAULT cannot carry a value on {parameter_id}.")
        else:
            require("value" in default, f"{policy} requires a value on {parameter_id}.")
            _validate_value(default["value"], item["value_schema"], f"{parameter_id}.default.value")

        if parameter_class == "E0_ASSUMPTION":
            require(not item["safety_critical"], f"E0 assumption cannot be safety-critical: {parameter_id}")
            require(policy == "E0_ONLY", f"E0 assumption must use E0_ONLY: {parameter_id}")
        if policy == "E0_ONLY":
            require(not item["safety_critical"], f"Safety-critical E0 default is forbidden: {parameter_id}")

        if item["safety_critical"] and "value" in default:
            require(policy == "FAIL_CLOSED", f"Safety default must fail closed: {parameter_id}")
            require(default.get("default_is_fail_closed") is True, f"Safety default lacks fail-closed marker: {parameter_id}")
            value_type = item["value_schema"].get("type")
            require(value_type not in numeric_types, f"Safety-critical numeric defaults are forbidden: {parameter_id}")

    power_width = parameter_by_id[POWER_WIDTH_ID]
    require(power_width["parameter_class"] == "RULE_PACK", "Power exclusion width must come from RULE_PACK.")
    require(power_width["safety_critical"] is True, "Power exclusion width must be safety-critical.")
    require(power_width["default"]["policy"] == "NO_DEFAULT", "Power exclusion width cannot have a hidden default.")
    require(power_width["unit"] == "m", "Power exclusion width must use metres.")

    power_axis = parameter_by_id[POWER_AXIS_ID]
    require(power_axis["parameter_class"] == "USER_FACT", "Power axis must be a user fact.")
    require(power_axis["default"]["policy"] == "NO_DEFAULT", "Power axis cannot be inferred.")
    require(
        parameter_by_id[POWER_WORK_ID]["default"].get("value") == "SPLIT_AND_EXCLUDE",
        "Power work behavior must default to split and exclude.",
    )
    require(
        parameter_by_id[POWER_TRANSIT_ID]["default"].get("value") == "PROHIBITED",
        "Power transit must be prohibited in V1.",
    )

    for parameter_id in (
        "poa.minimum_platform_area_m2",
        "poa.maximum_platform_slope_pct",
        "poa.minimum_soil_bearing_kpa",
        "connections.portal_min_width_m",
        "connections.max_gap_grade_pct",
    ):
        item = parameter_by_id[parameter_id]
        require(item["safety_critical"] is True, f"{parameter_id} must remain safety-critical.")
        require(item["default"]["policy"] == "NO_DEFAULT", f"{parameter_id} cannot have an implicit value.")

    require(len(parameters) >= 70, "The project catalog is unexpectedly sparse.")
    require(
        parameter_by_id["constraints.internal_work_stop_policy"]["default"].get("value")
        == "ONLY_ON_APPROVED_OPERATIONAL_SURFACE",
        "Internal work stops must fail closed outside approved operational surfaces.",
    )
    require(all_origins >= {"DECLARED", "CALCULATED", "RULE_PACK", "OPTIMIZER", "E0_ASSUMPTION"}, "Origin taxonomy is incomplete.")
    for parameter_id in MEASURED_PARAMETER_IDS:
        item = parameter_by_id[parameter_id]
        require(item["parameter_class"] == "USER_FACT", f"{parameter_id} must remain measured user evidence.")
        require(set(item.get("allowed_origins", [])) == {"MEASURED", "IMPORTED"}, f"{parameter_id} must accept only measured/imported origins.")
        require(item["default"]["policy"] == "NO_DEFAULT", f"{parameter_id} cannot be imputed.")
    for parameter_id in APPROVED_RULE_PARAMETER_IDS:
        item = parameter_by_id[parameter_id]
        require(item["parameter_class"] == "RULE_PACK", f"{parameter_id} must remain an approved rule.")
        require(item["safety_critical"] is True, f"{parameter_id} must remain safety-critical.")
        require(item["default"]["policy"] == "NO_DEFAULT", f"{parameter_id} cannot have a universal rule default.")
    return parameter_by_id


def _resolve_internal_ref(schema: dict[str, Any], ref: str) -> Any:
    require(ref.startswith("#/"), f"Only internal schema refs are expected, got {ref}.")
    current: Any = schema
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        require(isinstance(current, dict) and part in current, f"Broken schema ref: {ref}")
        current = current[part]
    return current


def _walk_refs(value: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(value, dict):
        if "$ref" in value:
            refs.append(value["$ref"])
        for child in value.values():
            refs.extend(_walk_refs(child))
    elif isinstance(value, list):
        for child in value:
            refs.extend(_walk_refs(child))
    return refs


def validate_schema(schema: dict[str, Any], catalog: dict[str, Any]) -> None:
    require(schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema", "Schema must use draft 2020-12.")
    require(schema.get("type") == "object", "Request schema root must be an object.")
    require(schema.get("additionalProperties") is False, "Request schema root must be closed.")
    require(schema.get("properties", {}).get("schema_version", {}).get("const") == REQUEST_VERSION, "Request schema version is wrong.")

    required_root = {
        "schema_version",
        "request_id",
        "project_id",
        "created_at",
        "requested_delivery_level",
        "revisions",
        "scope",
        "input_datasets",
        "constraint_inventory",
        "constraint_layers",
        "parameter_values",
        "scenario_request",
        "objective_policy",
        "release_policy",
        "overrides",
    }
    require(set(schema.get("required", [])) == required_root, "Request schema root fields changed unexpectedly.")
    for ref in _walk_refs(schema):
        _resolve_internal_ref(schema, ref)

    defs = schema.get("$defs")
    require(isinstance(defs, dict), "Request schema needs $defs.")
    required_defs = {
        "deliveryLevel",
        "revisions",
        "scope",
        "inputDataset",
        "constraintInventory",
        "powerInventoryDeclaration",
        "constraintLayer",
        "operationBehavior",
        "barrierPolicy",
        "parameterValue",
        "provenance",
        "responsible",
        "scenarioRequest",
        "objectivePolicy",
        "releasePolicy",
        "override",
    }
    require(required_defs.issubset(defs), "Request schema definitions are incomplete.")
    require(
        defs["revisions"]["properties"]["parameter_catalog_revision"]["const"] == CATALOG_VERSION,
        "Request schema references another parameter catalog revision.",
    )

    schema_classes = set(defs["parameterValue"]["properties"]["parameter_class"]["enum"])
    require(schema_classes == PARAMETER_CLASSES, "Schema/catalog parameter classes differ.")
    schema_provenance = set(defs["provenance"]["required"])
    require(schema_provenance == set(catalog["provenance_contract"]["required_fields"]), "Schema/catalog provenance fields differ.")

    class_origins = {
        origin
        for class_item in catalog["parameter_classes"]
        for origin in class_item["allowed_origins"]
    }
    schema_origins = set(defs["provenance"]["properties"]["origin"]["enum"])
    require(schema_origins == class_origins, "Schema/catalog provenance origins differ.")

    power_statuses = set(defs["powerInventoryDeclaration"]["properties"]["status"]["enum"])
    require(power_statuses == {"PROVIDED", "DECLARED_NONE", "NOT_REVIEWED"}, "Power inventory states are incomplete.")
    power_layer_types = set(defs["constraintLayer"]["properties"]["type"]["enum"])
    require(POWER_LAYER_TYPE in power_layer_types, "Power line constraint type is missing.")
    require(len(power_layer_types) >= 15, "Constraint layer taxonomy is unexpectedly narrow.")

    power_condition = defs["constraintLayer"]["allOf"][0]["then"]
    power_props = power_condition["properties"]
    require(power_props["geometry_type"]["const"] == "LineString", "Power V1 must be a line geometry.")
    require(power_props["dimension"]["const"] == "2D", "Power V1 must be explicitly 2D.")
    require(power_props["geometry_semantics"]["const"] == "AXIS_THROUGH_POST_CENTERS", "Power V1 axis semantics changed.")
    require("barrier_policy" in power_condition["required"], "Power line layer must define a barrier.")
    required_power_behaviors = {
        "FURROW": "SPLIT_WORK",
        "PLANT": "SPLIT_WORK",
        "HARVEST": "SPLIT_WORK",
        "TRANSSHIPMENT": "EXCLUDE",
        "TRUCK": "EXCLUDE",
        "MAINTENANCE": "EXCLUDE",
    }
    schema_power_behaviors = {
        item["contains"]["properties"]["operation"]["const"]: item["contains"]["properties"]["behavior"]["const"]
        for item in power_props["operation_behaviors"]["allOf"]
    }
    require(schema_power_behaviors == required_power_behaviors, "Power operation behaviors are incomplete.")

    barrier = defs["barrierPolicy"]
    require(barrier["properties"]["mode"]["const"] == "HORIZONTAL_EXCLUSION_BUFFER", "Power barrier must be a horizontal buffer.")
    require(barrier["properties"]["exclusion_half_width_parameter_id"]["const"] == POWER_WIDTH_ID, "Power barrier width must reference the catalog.")
    require(barrier["properties"]["work_behavior"]["const"] == "SPLIT_AND_EXCLUDE", "Power barrier must split worked lines.")
    require(barrier["properties"]["transit_behavior"]["const"] == "PROHIBITED", "Power barrier must prohibit crossing in V1.")

    poa_modes = set(defs["scenarioRequest"]["properties"]["poa_strategy_modes"]["items"]["enum"])
    expected_poa_modes = {
        "NOT_REQUESTED",
        "P0_POA_EXISTENTE",
        "P1_SEM_NOVA_OBRA",
        "P2_NOVOS_POAS",
        "P3_INTEGRADO",
    }
    require(poa_modes == expected_poa_modes, "POA strategy modes are incomplete.")
    poa_objectives = set(defs["scenarioRequest"]["properties"]["poa_objective_profiles"]["items"]["enum"])
    require(
        poa_objectives == {"NOT_REQUESTED", "MIN_TRANSBORDO", "MIN_COMPACTACAO", "BALANCEADO_CAPACITADO", "ROBUSTO_PICO"},
        "POA objective profiles are incomplete.",
    )

    release = defs["releasePolicy"]["properties"]
    require(release["block_on_unreviewed_constraints"]["const"] is True, "Unreviewed constraints must block release.")
    require(release["maximum_release_with_unreviewed_power"]["const"] == "E0_GEOMETRIC_ONLY", "Unreviewed power must cap release at E0.")
    require(release["allow_safety_gate_override"]["const"] is False, "Safety hard gates cannot be bypassed.")

    override_effects = set(defs["override"]["properties"]["effect"]["enum"])
    require("BYPASS_HARD_GATE" not in override_effects, "Overrides cannot bypass hard gates.")

    serialized_conditions = json.dumps(schema.get("allOf", []), sort_keys=True)
    for status in ("PROVIDED", "DECLARED_NONE", "NOT_REVIEWED"):
        require(status in serialized_conditions, f"Missing root condition for power status {status}.")


def _parse_datetime(value: Any, label: str) -> None:
    require(isinstance(value, str) and value, f"{label} must be a date-time string.")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"Invalid date-time for {label}: {value}") from exc


def _validate_responsible(value: Any, label: str) -> None:
    require(isinstance(value, dict), f"{label} must be an object.")
    for key in ("name", "organization", "role"):
        require(isinstance(value.get(key), str) and value[key].strip(), f"{label}.{key} is required.")


def _validate_value(value: Any, fragment: dict[str, Any], label: str) -> None:
    expected_type = fragment.get("type")
    expected_types = expected_type if isinstance(expected_type, list) else [expected_type]

    def matches(value_type: str) -> bool:
        if value_type == "string":
            return isinstance(value, str)
        if value_type == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if value_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if value_type == "boolean":
            return isinstance(value, bool)
        if value_type == "array":
            return isinstance(value, list)
        if value_type == "object":
            return isinstance(value, dict)
        if value_type == "null":
            return value is None
        return False

    type_ok = any(matches(value_type) for value_type in expected_types)
    require(type_ok, f"{label} has the wrong value type; expected {expected_type}.")

    if "const" in fragment:
        require(value == fragment["const"], f"{label} must equal {fragment['const']!r}.")
    if "enum" in fragment:
        require(value in fragment["enum"], f"{label} is outside its enum.")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        require(math.isfinite(float(value)), f"{label} must be finite.")
        if "minimum" in fragment:
            require(value >= fragment["minimum"], f"{label} is below minimum.")
        if "exclusiveMinimum" in fragment:
            require(value > fragment["exclusiveMinimum"], f"{label} is below exclusive minimum.")
        if "maximum" in fragment:
            require(value <= fragment["maximum"], f"{label} is above maximum.")
        if "exclusiveMaximum" in fragment:
            require(value < fragment["exclusiveMaximum"], f"{label} is above exclusive maximum.")
    if isinstance(value, str):
        if "minLength" in fragment:
            require(len(value) >= fragment["minLength"], f"{label} is too short.")
        if "maxLength" in fragment:
            require(len(value) <= fragment["maxLength"], f"{label} is too long.")
        if "pattern" in fragment:
            require(re.fullmatch(fragment["pattern"], value) is not None, f"{label} does not match its pattern.")
    if isinstance(value, list):
        if "minItems" in fragment:
            require(len(value) >= fragment["minItems"], f"{label} has too few items.")
        if "maxItems" in fragment:
            require(len(value) <= fragment["maxItems"], f"{label} has too many items.")
        if fragment.get("uniqueItems") is True:
            serialized = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            require(len(serialized) == len(set(serialized)), f"{label} has duplicate items.")
        item_schema = fragment.get("items")
        if isinstance(item_schema, dict):
            for index, child in enumerate(value):
                _validate_value(child, item_schema, f"{label}[{index}]")
    if isinstance(value, dict):
        if "minProperties" in fragment:
            require(len(value) >= fragment["minProperties"], f"{label} has too few properties.")
        if "maxProperties" in fragment:
            require(len(value) <= fragment["maxProperties"], f"{label} has too many properties.")
        property_names = fragment.get("propertyNames")
        if isinstance(property_names, dict) and "enum" in property_names:
            unknown = sorted(set(value) - set(property_names["enum"]))
            require(not unknown, f"{label} has unknown property names: {', '.join(unknown)}")
        properties = fragment.get("properties", {})
        required_names = set(fragment.get("required", []))
        missing = sorted(required_names - set(value))
        require(not missing, f"{label} is missing properties: {', '.join(missing)}")
        for key, child_schema in properties.items():
            if key in value:
                _validate_value(value[key], child_schema, f"{label}.{key}")
        additional = fragment.get("additionalProperties")
        unknown_keys = set(value) - set(properties)
        if additional is False:
            require(not unknown_keys, f"{label} has unknown properties: {', '.join(sorted(unknown_keys))}")
        if isinstance(additional, dict):
            for key in unknown_keys:
                _validate_value(value[key], additional, f"{label}.{key}")


def _validate_structured_parameter_semantics(parameter_id: str, value: Any) -> None:
    if parameter_id == "agronomy.crop_variety_by_zone":
        zone_ids = [record["zone_id"] for record in value]
        require(len(zone_ids) == len(set(zone_ids)), f"{parameter_id} has duplicate zone_id values.")

    if parameter_id == "agronomy.row_pattern_by_zone":
        period_keys = [
            (record["zone_id"], record["valid_from"], record["valid_to"])
            for record in value
        ]
        require(len(period_keys) == len(set(period_keys)), f"{parameter_id} has duplicate zone/period records.")
        periods_by_zone: dict[str, list[tuple[datetime, datetime]]] = {}
        expected_lengths = {"SINGLE": 1, "DOUBLE": 2}
        for record in value:
            try:
                valid_from = datetime.fromisoformat(record["valid_from"])
                valid_to = datetime.fromisoformat(record["valid_to"])
            except ValueError as exc:
                raise ContractError(f"{parameter_id} has an invalid validity date.") from exc
            require(valid_from <= valid_to, f"{parameter_id} valid_from must not exceed valid_to.")
            periods_by_zone.setdefault(record["zone_id"], []).append((valid_from, valid_to))
            pattern = record["row_pattern"]
            spacing_count = len(record["interval_sequence"])
            if pattern in expected_lengths:
                require(
                    spacing_count == expected_lengths[pattern],
                    f"{parameter_id} {pattern} needs exactly {expected_lengths[pattern]} spacing value(s).",
                )
            else:
                require(spacing_count >= 2, f"{parameter_id} ALTERNATING needs at least two spacing values.")
            traffic_mode = record["traffic_band_mode"]
            traffic_source = record["traffic_band_source"]
            geometry_ref = record.get("traffic_band_geometry_dataset_ref")
            if traffic_mode == "NONE":
                require(traffic_source == "NOT_APPLICABLE", f"{parameter_id} NONE needs NOT_APPLICABLE traffic source.")
                require(geometry_ref is None, f"{parameter_id} NONE cannot declare a traffic-band geometry.")
            elif traffic_source == "EXISTING_GEOMETRY":
                require(isinstance(geometry_ref, str) and geometry_ref, f"{parameter_id} existing traffic bands need a dataset reference.")
            else:
                require(traffic_source == "TO_GENERATE", f"{parameter_id} active traffic bands need a valid source.")
                require(geometry_ref is None, f"{parameter_id} generated traffic bands cannot declare existing geometry.")
        for zone_id, periods in periods_by_zone.items():
            ordered = sorted(periods)
            for previous, current in zip(ordered, ordered[1:]):
                require(previous[1] < current[0], f"{parameter_id} has overlapping periods in zone {zone_id}.")

    unique_keys: dict[str, tuple[str, ...]] = {
        "fleet.operational_state_measurements": ("operation", "operational_state", "configuration_id"),
        "fleet.control_execution_envelope_measurements": (
            "operation",
            "operational_state",
            "configuration_id",
            "environmental_observation_set_id",
        ),
        "fleet.approved_control_execution_envelope_rules": ("operation", "operational_state", "configuration_id"),
        "fleet.approved_track_gauge_compatibility_rules": ("zone_id", "operation", "operational_state", "configuration_id"),
        "fleet.approved_operating_rules_by_state_and_environment": (
            "operation",
            "operational_state",
            "configuration_id",
            "environmental_condition_set_id",
        ),
    }
    if parameter_id in unique_keys:
        fields = unique_keys[parameter_id]
        keys = [tuple(str(record[field]) for field in fields) for record in value]
        require(len(keys) == len(set(keys)), f"{parameter_id} has duplicate operation/state records.")

    state_group_fields: dict[str, tuple[str, ...]] = {
        "fleet.operational_state_measurements": ("operation", "configuration_id"),
        "fleet.control_execution_envelope_measurements": ("operation", "configuration_id"),
        "fleet.approved_control_execution_envelope_rules": ("operation", "configuration_id"),
        "fleet.approved_track_gauge_compatibility_rules": ("zone_id", "operation", "configuration_id"),
        "fleet.approved_operating_rules_by_state_and_environment": (
            "operation",
            "configuration_id",
            "environmental_condition_set_id",
        ),
    }
    if parameter_id in state_group_fields:
        grouped_states: dict[tuple[str, ...], set[str]] = {}
        for record in value:
            group_key = tuple(str(record[field]) for field in state_group_fields[parameter_id])
            grouped_states.setdefault(group_key, set()).add(record["operational_state"])
        for group_key, states in grouped_states.items():
            require(
                states == {"LOADED", "EMPTY"},
                f"{parameter_id} must carry LOADED and EMPTY for {group_key}.",
            )

    if parameter_id == "fleet.operational_state_measurements":
        for record in value:
            axes = [
                (axis["unit_id"], axis["axis_id"])
                for axis in record["unit_axis_loads"]
            ]
            require(
                len(axes) == len(set(axes)),
                f"{parameter_id} has duplicate unit_id/axis_id entries in {record['configuration_id']}.",
            )

    if parameter_id == "fleet.control_execution_envelope_measurements":
        for record in value:
            controller = record["controller_gnss_measurements"]
            require(
                controller["cross_track_error_p95_m"] <= controller["cross_track_error_max_m"],
                f"{parameter_id} cross-track P95 exceeds the observed maximum.",
            )
            unit_ids = [unit["unit_id"] for unit in record["unit_geometries"]]
            require(len(unit_ids) == len(set(unit_ids)), f"{parameter_id} has duplicate unit_id values.")
            for unit in record["unit_geometries"]:
                axis_ids = [axis["axis_id"] for axis in unit["axes"]]
                require(
                    len(axis_ids) == len(set(axis_ids)),
                    f"{parameter_id} has duplicate axes in unit {unit['unit_id']}.",
                )
            links = record["articulation_links"]
            link_ids = [link["link_id"] for link in links]
            require(len(link_ids) == len(set(link_ids)), f"{parameter_id} has duplicate articulation link ids.")
            require(len(links) == max(0, len(unit_ids) - 1), f"{parameter_id} articulation links do not form one articulated set.")
            parent_by_child: dict[str, str] = {}
            for link in links:
                upstream = link["upstream_unit_id"]
                downstream = link["downstream_unit_id"]
                require(upstream in unit_ids and downstream in unit_ids, f"{parameter_id} articulation references an unknown unit.")
                require(upstream != downstream, f"{parameter_id} articulation cannot connect a unit to itself.")
                require(downstream not in parent_by_child, f"{parameter_id} articulation gives a unit more than one parent.")
                parent_by_child[downstream] = upstream
            roots = set(unit_ids) - set(parent_by_child)
            require(len(roots) == 1, f"{parameter_id} articulated set must have exactly one root unit.")
            for unit_id in unit_ids:
                visited: set[str] = set()
                current = unit_id
                while current in parent_by_child:
                    require(current not in visited, f"{parameter_id} articulation contains a cycle.")
                    visited.add(current)
                    current = parent_by_child[current]
                require(current in roots, f"{parameter_id} articulation is disconnected.")

    if parameter_id == "fleet.approved_control_execution_envelope_rules":
        for record in value:
            link_ids = [limit["link_id"] for limit in record["articulation_limits"]]
            require(len(link_ids) == len(set(link_ids)), f"{parameter_id} has duplicate articulation limit ids.")

    if parameter_id == "fleet.approved_operating_rules_by_state_and_environment":
        for record in value:
            variables = [criterion["variable"] for criterion in record["condition_criteria"]]
            require(len(variables) == len(set(variables)), f"{parameter_id} has duplicate environmental criteria.")
            for criterion in record["condition_criteria"]:
                lower = criterion["minimum_inclusive"]
                upper = criterion["maximum_inclusive"]
                require(lower is not None or upper is not None, f"{parameter_id} criterion needs at least one bound.")
                if lower is not None and upper is not None:
                    require(lower <= upper, f"{parameter_id} criterion minimum exceeds maximum.")

    if parameter_id == "agronomy.correlated_environmental_measurements":
        observation_ids = [record["observation_set_id"] for record in value]
        require(len(observation_ids) == len(set(observation_ids)), f"{parameter_id} has duplicate observation_set_id values.")
        for record in value:
            correlation_keys = set(record["correlation_keys"])
            require("TIMESTAMP" in correlation_keys, f"{parameter_id} must correlate observations by TIMESTAMP.")
            require("OPERATION" in correlation_keys, f"{parameter_id} must correlate observations by OPERATION.")
            require("OPERATIONAL_STATE" in correlation_keys, f"{parameter_id} must correlate observations by OPERATIONAL_STATE.")
            require(
                bool(correlation_keys & {"FIELD_ID", "ZONE_ID"}),
                f"{parameter_id} must carry FIELD_ID or ZONE_ID correlation.",
            )
            measured_variables = set(record["measured_variables"])
            require(
                set(record["variable_units"]) == measured_variables,
                f"{parameter_id} variable_units must cover exactly the measured variables.",
            )
            require(
                set(record["variable_method_refs"]) == measured_variables,
                f"{parameter_id} variable_method_refs must cover exactly the measured variables.",
            )
            if "SOIL_WATER_CONTENT" in measured_variables:
                require(
                    record["water_content_basis"] in {"VOLUMETRIC", "GRAVIMETRIC"},
                    f"{parameter_id} soil water content needs a volumetric or gravimetric basis.",
                )
            else:
                require(
                    record["water_content_basis"] == "NOT_APPLICABLE",
                    f"{parameter_id} water basis must be NOT_APPLICABLE without soil-water observations.",
                )


def _parameter_map(request: dict[str, Any], catalog_by_id: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    values = request.get("parameter_values")
    require(isinstance(values, list) and values, "parameter_values must be a non-empty array.")
    result: dict[str, dict[str, Any]] = {}
    provenance_fields = {
        "origin",
        "source_ref",
        "captured_at",
        "responsible",
        "confidence",
        "revision",
        "applicability",
    }
    for index, item in enumerate(values):
        require(isinstance(item, dict), f"parameter_values[{index}] must be an object.")
        parameter_id = item.get("parameter_id")
        require(isinstance(parameter_id, str) and parameter_id, f"parameter_values[{index}] needs parameter_id.")
        require(parameter_id in catalog_by_id, f"Unknown parameter id in request: {parameter_id}")
        require(parameter_id not in result, f"Duplicate parameter value: {parameter_id}")
        catalog_item = catalog_by_id[parameter_id]
        require(item.get("parameter_class") == catalog_item["parameter_class"], f"Parameter class mismatch: {parameter_id}")
        require(item.get("unit") == catalog_item["unit"], f"Unit mismatch: {parameter_id}")
        require("value" in item, f"Missing value: {parameter_id}")
        _validate_value(item["value"], catalog_item["value_schema"], parameter_id)
        _validate_structured_parameter_semantics(parameter_id, item["value"])

        provenance = item.get("provenance")
        require(isinstance(provenance, dict), f"Missing provenance: {parameter_id}")
        require(provenance_fields.issubset(provenance), f"Incomplete provenance: {parameter_id}")
        origin = provenance["origin"]
        allowed_origins = set(catalog_item.get("allowed_origins", CLASS_ORIGINS[item["parameter_class"]]))
        require(origin in allowed_origins, f"Origin/class or parameter-evidence mismatch: {parameter_id}")
        require(isinstance(provenance["source_ref"], str) and provenance["source_ref"], f"Missing source_ref: {parameter_id}")
        _parse_datetime(provenance["captured_at"], f"{parameter_id}.provenance.captured_at")
        _validate_responsible(provenance["responsible"], f"{parameter_id}.provenance.responsible")
        require(provenance["confidence"] in {"LOW", "MEDIUM", "HIGH", "VERIFIED"}, f"Invalid confidence: {parameter_id}")
        require(isinstance(provenance["revision"], str) and provenance["revision"], f"Missing revision: {parameter_id}")
        require(provenance["applicability"] in {"PROJECT", "PROPERTY", "FIELD", "ZONE", "FLEET", "SCENARIO"}, f"Invalid applicability: {parameter_id}")

        if item["parameter_class"] == "E0_ASSUMPTION":
            require(request["requested_delivery_level"] == "E0_TRIAGEM", f"E0 assumption present above E0: {parameter_id}")
        result[parameter_id] = item
    return result


def _validate_structured_parameter_links(
    parameters: dict[str, dict[str, Any]],
    dataset_by_id: dict[str, dict[str, Any]],
) -> None:
    def values(parameter_id: str) -> list[dict[str, Any]]:
        item = parameters.get(parameter_id)
        return item["value"] if item is not None else []

    for record in values("agronomy.row_pattern_by_zone"):
        dataset_ref = record.get("traffic_band_geometry_dataset_ref")
        if dataset_ref is None:
            continue
        require(dataset_ref in dataset_by_id, f"Unknown traffic-band geometry dataset: {dataset_ref}")
        dataset = dataset_by_id[dataset_ref]
        require(dataset.get("format") in {"SHP", "GPKG", "GEOJSON", "KML", "DXF"}, f"Traffic-band geometry must use a vector format: {dataset_ref}")
        require(
            dataset.get("geometry_type") in {"LineString", "MultiLineString", "Polygon", "MultiPolygon"},
            f"Traffic-band geometry has an unsupported geometry type: {dataset_ref}",
        )

    environmental_records = values("agronomy.correlated_environmental_measurements")
    environmental_by_observation = {
        record["observation_set_id"]: record for record in environmental_records
    }
    environmental_by_condition: dict[str, list[dict[str, Any]]] = {}
    for record in environmental_records:
        require(record["dataset_ref"] in dataset_by_id, f"Unknown environmental measurement dataset: {record['dataset_ref']}")
        environmental_by_condition.setdefault(record["environmental_condition_set_id"], []).append(record)

    state_records = values("fleet.operational_state_measurements")
    state_by_key = {
        (record["operation"], record["operational_state"], record["configuration_id"]): record
        for record in state_records
    }

    envelope_records = values("fleet.control_execution_envelope_measurements")
    envelope_by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    envelope_states_by_condition: dict[tuple[str, str, str], set[str]] = {}
    for record in envelope_records:
        key = (record["operation"], record["operational_state"], record["configuration_id"])
        require(key in state_by_key, f"Envelope measurement has no matching operational-state measurement: {key}")
        observation_id = record["environmental_observation_set_id"]
        require(observation_id in environmental_by_observation, f"Envelope measurement references an unknown environmental observation set: {observation_id}")
        observed_environment = set(environmental_by_observation[observation_id]["measured_variables"])
        require(
            {"SOIL_WATER_CONTENT", "SOIL_BEARING_CAPACITY"} <= observed_environment,
            f"Envelope measurement needs correlated soil-water and bearing-capacity observations: {observation_id}",
        )
        condition_key = (
            record["operation"],
            record["configuration_id"],
            environmental_by_observation[observation_id]["environmental_condition_set_id"],
        )
        envelope_states_by_condition.setdefault(condition_key, set()).add(record["operational_state"])
        measured_axes = {
            (axis["unit_id"], axis["axis_id"])
            for axis in state_by_key[key]["unit_axis_loads"]
        }
        envelope_axes = {
            (unit["unit_id"], axis["axis_id"])
            for unit in record["unit_geometries"]
            for axis in unit["axes"]
        }
        require(measured_axes == envelope_axes, f"Envelope and load measurements cover different unit/axis sets: {key}")
        envelope_by_key.setdefault(key, []).append(record)
    for condition_key, states in envelope_states_by_condition.items():
        require(
            states == {"LOADED", "EMPTY"},
            f"Envelope measurements must carry LOADED and EMPTY under environmental condition {condition_key}.",
        )

    for rule in values("fleet.approved_control_execution_envelope_rules"):
        key = (rule["operation"], rule["operational_state"], rule["configuration_id"])
        matching_envelopes = envelope_by_key.get(key, [])
        require(matching_envelopes, f"Approved envelope rule has no matching measured envelope: {key}")
        approved_link_limits = {
            item["link_id"]: item["maximum_allowed_articulation_angle_deg"]
            for item in rule["articulation_limits"]
        }
        for envelope in matching_envelopes:
            controller = envelope["controller_gnss_measurements"]
            require(controller["cross_track_error_max_m"] <= rule["maximum_allowed_cross_track_error_m"], f"Measured cross-track error exceeds the approved limit: {key}")
            require(controller["heading_error_max_deg"] <= rule["maximum_allowed_heading_error_deg"], f"Measured heading error exceeds the approved limit: {key}")
            require(controller["latency_p95_s"] <= rule["maximum_allowed_latency_s"], f"Measured controller latency exceeds the approved limit: {key}")
            measured_link_angles = {
                item["link_id"]: item["observed_articulation_angle_max_deg"]
                for item in envelope["articulation_links"]
            }
            require(set(measured_link_angles) == set(approved_link_limits), f"Measured and approved articulation links differ: {key}")
            require(
                all(measured_link_angles[link_id] <= approved_link_limits[link_id] for link_id in measured_link_angles),
                f"Measured articulation exceeds an approved limit: {key}",
            )

    row_patterns_by_zone: dict[str, list[dict[str, Any]]] = {}
    for record in values("agronomy.row_pattern_by_zone"):
        row_patterns_by_zone.setdefault(record["zone_id"], []).append(record)
    for rule in values("fleet.approved_track_gauge_compatibility_rules"):
        state_key = (rule["operation"], rule["operational_state"], rule["configuration_id"])
        require(state_key in state_by_key, f"Track-gauge rule has no matching operational-state measurement: {state_key}")
        zone_patterns = row_patterns_by_zone.get(rule["zone_id"], [])
        require(zone_patterns, f"Track-gauge rule references an unknown row-pattern zone: {rule['zone_id']}")
        measured_gauges = [axis["track_gauge_m"] for axis in state_by_key[state_key]["unit_axis_loads"]]
        maximum_mismatch = rule["maximum_gauge_mismatch_m"]
        for row_pattern in zone_patterns:
            compatible_gauges = row_pattern["compatible_track_gauges_m"]
            require(
                all(any(abs(measured - compatible) <= maximum_mismatch for compatible in compatible_gauges) for measured in measured_gauges),
                f"Measured track gauge is incompatible with row pattern in zone {rule['zone_id']}.",
            )

    for rule in values("fleet.approved_operating_rules_by_state_and_environment"):
        state_key = (rule["operation"], rule["operational_state"], rule["configuration_id"])
        require(state_key in state_by_key, f"Environmental operating rule has no matching operational-state measurement: {state_key}")
        observations = environmental_by_condition.get(rule["environmental_condition_set_id"], [])
        require(observations, f"Environmental operating rule references an unknown condition set: {rule['environmental_condition_set_id']}")
        for criterion in rule["condition_criteria"]:
            for observation in observations:
                variable = criterion["variable"]
                require(variable in observation["measured_variables"], f"Environmental criterion is absent from observation set {observation['observation_set_id']}: {variable}")
                require(observation["variable_units"][variable] == criterion["unit"], f"Environmental criterion unit differs from measured data: {variable}")


def validate_request(
    request: dict[str, Any],
    schema: dict[str, Any],
    catalog_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    required_root = set(schema["required"])
    require(required_root.issubset(request), f"Request is missing root fields: {', '.join(sorted(required_root - set(request)))}")
    require(set(request).issubset(schema["properties"]), f"Unknown request root fields: {', '.join(sorted(set(request) - set(schema['properties'])))}")
    require(request["schema_version"] == REQUEST_VERSION, "Unexpected generation request version.")
    _parse_datetime(request["created_at"], "created_at")
    require(request["requested_delivery_level"] in {"E0_TRIAGEM", "E1_OPERACIONAL", "E2_CONSERVACIONISTA", "E3_EXECUTIVO"}, "Invalid delivery level.")

    revisions = request["revisions"]
    require(isinstance(revisions, dict), "revisions must be an object.")
    require(revisions.get("parameter_catalog_revision") == CATALOG_VERSION, "Request uses another parameter catalog revision.")
    for key in ("terrain_revision", "fleet_revision", "rule_pack_revision", "constraint_revision"):
        require(isinstance(revisions.get(key), str) and revisions[key], f"Missing revision: {key}")

    scope = request["scope"]
    require(isinstance(scope, dict), "scope must be an object.")
    require(isinstance(scope.get("field_ids"), list) and scope["field_ids"], "scope.field_ids is required.")
    require(isinstance(scope.get("property_ids"), list) and scope["property_ids"], "scope.property_ids is required.")
    require(len(scope["field_ids"]) == len(set(scope["field_ids"])), "scope.field_ids must be unique.")
    require(len(scope["property_ids"]) == len(set(scope["property_ids"])), "scope.property_ids must be unique.")

    datasets = request["input_datasets"]
    require(isinstance(datasets, list) and datasets, "input_datasets must be a non-empty array.")
    dataset_by_id: dict[str, dict[str, Any]] = {}
    for index, dataset in enumerate(datasets):
        require(isinstance(dataset, dict), f"input_datasets[{index}] must be an object.")
        dataset_id = dataset.get("dataset_id")
        require(isinstance(dataset_id, str) and dataset_id, f"input_datasets[{index}] needs dataset_id.")
        require(dataset_id not in dataset_by_id, f"Duplicate dataset id: {dataset_id}")
        require(isinstance(dataset.get("source_ref"), str) and dataset["source_ref"], f"Missing dataset source_ref: {dataset_id}")
        _parse_datetime(dataset.get("captured_at"), f"{dataset_id}.captured_at")
        _validate_responsible(dataset.get("responsible"), f"{dataset_id}.responsible")
        dataset_by_id[dataset_id] = dataset

    field_boundary_datasets = [
        dataset for dataset in datasets if dataset.get("role") == "FIELD_BOUNDARY"
    ]
    require(
        len(field_boundary_datasets) == 1,
        "A generation request must contain exactly one FIELD_BOUNDARY input dataset.",
    )
    field_boundary_dataset = field_boundary_datasets[0]
    require(
        field_boundary_dataset.get("format") in {"SHP", "GPKG", "GEOJSON", "KML", "DXF"},
        "FIELD_BOUNDARY must use a supported vector format.",
    )
    boundary_geometry_type = field_boundary_dataset.get("geometry_type")
    require(
        boundary_geometry_type in {"Polygon", "MultiPolygon"},
        "FIELD_BOUNDARY must declare Polygon or MultiPolygon geometry.",
    )
    require(
        isinstance(field_boundary_dataset.get("id_field"), str)
        and field_boundary_dataset["id_field"].strip(),
        "FIELD_BOUNDARY must declare the attribute used by scope.field_ids in id_field.",
    )
    if field_boundary_dataset.get("format") == "GPKG":
        require(
            isinstance(field_boundary_dataset.get("layer_name"), str)
            and field_boundary_dataset["layer_name"].strip(),
            "A GeoPackage FIELD_BOUNDARY must declare layer_name.",
        )

    parameters = _parameter_map(request, catalog_by_id)
    dependency_requirements = {
        "fleet.control_execution_envelope_measurements": {
            "agronomy.correlated_environmental_measurements",
            "fleet.operational_state_measurements",
        },
        "fleet.approved_control_execution_envelope_rules": {
            "fleet.control_execution_envelope_measurements",
        },
        "fleet.approved_track_gauge_compatibility_rules": {
            "agronomy.row_pattern_by_zone",
            "fleet.operational_state_measurements",
        },
        "fleet.approved_operating_rules_by_state_and_environment": {
            "agronomy.correlated_environmental_measurements",
            "fleet.operational_state_measurements",
        },
    }
    for parameter_id, dependencies in dependency_requirements.items():
        if parameter_id in parameters:
            missing_dependencies = sorted(dependencies - set(parameters))
            require(
                not missing_dependencies,
                f"{parameter_id} is missing evidence parameters: {', '.join(missing_dependencies)}",
            )
    _validate_structured_parameter_links(parameters, dataset_by_id)
    missing_always = sorted(ALWAYS_REQUEST_PARAMETER_IDS - set(parameters))
    require(not missing_always, f"Missing required request parameters: {', '.join(missing_always)}")
    delivery_level = request["requested_delivery_level"]
    if delivery_level != "E0_TRIAGEM":
        missing_e1 = sorted(E1_REQUIRED_PARAMETER_IDS - set(parameters))
        require(not missing_e1, f"Missing E1 operational parameters: {', '.join(missing_e1)}")
        require(
            parameters["constraints.internal_work_stop_policy"]["value"]
            == "ONLY_ON_APPROVED_OPERATIONAL_SURFACE",
            "E1 work stops must be restricted to approved operational surfaces.",
        )
        surface_dataset_id = parameters["constraints.operational_surfaces_dataset_ref"]["value"]
        require(surface_dataset_id in dataset_by_id, "Operational surface parameter references an unknown dataset.")
        require(
            dataset_by_id[surface_dataset_id].get("role") == "CONSTRAINT_LAYER",
            "Operational surfaces must use a CONSTRAINT_LAYER dataset.",
        )
    if delivery_level in {"E2_CONSERVACIONISTA", "E3_EXECUTIVO"}:
        missing_e2 = sorted(E2_REQUIRED_PARAMETER_IDS - set(parameters))
        require(not missing_e2, f"Missing E2 conservational parameters: {', '.join(missing_e2)}")
    require(parameters["scope.target_field_ids"]["value"] == scope["field_ids"], "Scope field ids differ from parameter values.")
    require(parameters["scope.target_property_ids"]["value"] == scope["property_ids"], "Scope property ids differ from parameter values.")
    require(parameters["scope.allow_cross_field_work"]["value"] == scope["cross_field_generation"], "Cross-field scope differs from its parameter.")
    require(parameters["scope.allow_cross_property_work"]["value"] == scope["cross_property_generation"], "Cross-property scope differs from its parameter.")
    require(parameters["objectives.selection_mode"]["value"] == request["objective_policy"]["selection_mode"], "Objective selection modes differ.")
    dtm_dataset_id = parameters["terrain.dtm_dataset_ref"]["value"]
    require(dtm_dataset_id in dataset_by_id, "terrain.dtm_dataset_ref points to an unknown input dataset.")
    require(
        dataset_by_id[dtm_dataset_id].get("role") == "DTM",
        "terrain.dtm_dataset_ref must point to an input dataset with role DTM.",
    )

    release = request["release_policy"]
    require(release.get("block_on_unreviewed_constraints") is True, "Unreviewed constraints must block release.")
    require(release.get("maximum_release_with_unreviewed_power") == "E0_GEOMETRIC_ONLY", "Unreviewed power must cap release at E0.")
    require(release.get("allow_safety_gate_override") is False, "Safety hard gates cannot be bypassed.")
    require(parameters["qa.release_block_on_unreviewed_constraints"]["value"] is True, "QA parameter must block unreviewed constraints.")

    layers = request["constraint_layers"]
    require(isinstance(layers, list), "constraint_layers must be an array.")
    layer_by_id: dict[str, dict[str, Any]] = {}
    for index, layer in enumerate(layers):
        require(isinstance(layer, dict), f"constraint_layers[{index}] must be an object.")
        layer_id = layer.get("layer_id")
        require(isinstance(layer_id, str) and layer_id, f"constraint_layers[{index}] needs layer_id.")
        require(layer_id not in layer_by_id, f"Duplicate constraint layer id: {layer_id}")
        require(layer.get("dataset_ref") in dataset_by_id, f"Constraint layer {layer_id} references an unknown dataset.")
        require(isinstance(layer.get("operation_behaviors"), list) and layer["operation_behaviors"], f"Constraint layer {layer_id} needs operation behaviors.")
        layer_by_id[layer_id] = layer

    inventory = request["constraint_inventory"]
    require(isinstance(inventory, dict), "constraint_inventory must be an object.")
    require(inventory.get("general_review_status") in {"COMPLETE", "PARTIAL", "NOT_REVIEWED"}, "Invalid general constraint review status.")
    power = inventory.get("overhead_power_line")
    require(isinstance(power, dict), "Power line inventory declaration is required.")
    power_status = power.get("status")
    require(power_status in {"PROVIDED", "DECLARED_NONE", "NOT_REVIEWED"}, "Invalid power line inventory status.")
    _parse_datetime(power.get("declared_at"), "overhead_power_line.declared_at")
    _validate_responsible(power.get("responsible"), "overhead_power_line.responsible")
    require(parameters[POWER_STATUS_ID]["value"] == power_status, "Power inventory status differs from its parameter value.")
    power_layers = {layer_id: layer for layer_id, layer in layer_by_id.items() if layer.get("type") == POWER_LAYER_TYPE}

    if power_status == "PROVIDED":
        declared_layer_ids = power.get("layer_ids")
        require(isinstance(declared_layer_ids, list) and declared_layer_ids, "PROVIDED power inventory needs layer_ids.")
        require(set(declared_layer_ids) == set(power_layers), "Power inventory layer_ids must match the supplied power layers.")
        required_power_params = {POWER_AXIS_ID, POWER_WIDTH_ID, POWER_WORK_ID, POWER_TRANSIT_ID}
        missing = sorted(required_power_params - set(parameters))
        require(not missing, f"Power barrier parameters are missing: {', '.join(missing)}")
        require(parameters[POWER_WIDTH_ID]["value"] > 0, "Power exclusion half width must be positive.")
        require(parameters[POWER_WIDTH_ID]["provenance"]["origin"] in {"RULE_PACK", "PROFESSIONAL_CALIBRATION"}, "Power exclusion width needs technical provenance.")
        require(parameters[POWER_WORK_ID]["value"] == "SPLIT_AND_EXCLUDE", "Power line must split and exclude worked segments.")
        require(parameters[POWER_TRANSIT_ID]["value"] == "PROHIBITED", "Power-line crossing must be prohibited in V1.")
        require(parameters[POWER_AXIS_ID]["value"] in dataset_by_id, "Power axis parameter must reference an input dataset id.")

        power_dataset_refs: set[str] = set()
        for layer_id, layer in power_layers.items():
            require(layer.get("geometry_type") == "LineString", f"Power layer {layer_id} must be LineString.")
            require(layer.get("dimension") == "2D", f"Power layer {layer_id} must be 2D in V1.")
            require(layer.get("geometry_semantics") == "AXIS_THROUGH_POST_CENTERS", f"Power layer {layer_id} must follow post centers.")
            barrier = layer.get("barrier_policy")
            require(isinstance(barrier, dict), f"Power layer {layer_id} needs barrier_policy.")
            require(barrier.get("mode") == "HORIZONTAL_EXCLUSION_BUFFER", f"Power layer {layer_id} needs a horizontal buffer.")
            require(barrier.get("exclusion_half_width_parameter_id") == POWER_WIDTH_ID, f"Power layer {layer_id} uses the wrong width parameter.")
            require(barrier.get("work_behavior") == "SPLIT_AND_EXCLUDE", f"Power layer {layer_id} must split work.")
            require(barrier.get("transit_behavior") == parameters[POWER_TRANSIT_ID]["value"], f"Power layer {layer_id} transit policy differs from its parameter.")
            operation_behaviors = {item.get("operation"): item.get("behavior") for item in layer["operation_behaviors"]}
            required_behaviors = {
                "FURROW": "SPLIT_WORK",
                "PLANT": "SPLIT_WORK",
                "HARVEST": "SPLIT_WORK",
                "TRANSSHIPMENT": "EXCLUDE",
                "TRUCK": "EXCLUDE",
                "MAINTENANCE": "EXCLUDE",
            }
            require(
                all(operation_behaviors.get(operation) == behavior for operation, behavior in required_behaviors.items()),
                f"Power layer {layer_id} does not block every V1 operation.",
            )
            power_dataset_refs.add(layer["dataset_ref"])
            dataset = dataset_by_id[layer["dataset_ref"]]
            require(dataset.get("format") in {"SHP", "GPKG", "GEOJSON", "KML", "DXF"}, f"Power dataset {dataset['dataset_id']} must be vector.")
            require(dataset.get("role") == "CONSTRAINT_LAYER", f"Power dataset {dataset['dataset_id']} must have CONSTRAINT_LAYER role.")
        require(parameters[POWER_AXIS_ID]["value"] in power_dataset_refs, "Power axis parameter does not reference a supplied power layer dataset.")
    elif power_status == "DECLARED_NONE":
        require(not power_layers, "DECLARED_NONE cannot coexist with a power line layer.")
        require(isinstance(power.get("declaration_ref"), str) and power["declaration_ref"], "DECLARED_NONE needs a traceable declaration_ref.")
        require(not power.get("layer_ids"), "DECLARED_NONE cannot list layer ids.")
    else:
        require(not power_layers, "NOT_REVIEWED cannot claim a reviewed power layer.")
        require(not power.get("layer_ids"), "NOT_REVIEWED cannot list layer ids.")
        blockers.append("OVERHEAD_POWER_LINE_NOT_REVIEWED")
        warnings.append("Power line absence was not declared; output is capped at E0 geometric screening.")

    if inventory["general_review_status"] != "COMPLETE":
        blockers.append("CONSTRAINT_INVENTORY_INCOMPLETE")

    if scope["cross_property_generation"]:
        require(parameters["scope.cross_property_permission_status"]["value"] == "APPROVED", "Cross-property generation needs explicit permission.")
    if not scope["cross_field_generation"]:
        connection_modes = set(request["scenario_request"]["connection_modes"])
        require(connection_modes == {"OC0_ISOLADO"}, "Cross-field modes were requested while cross-field generation is disabled.")

    scenario = request["scenario_request"]
    poa_modes = set(scenario.get("poa_strategy_modes", []))
    poa_objectives = set(scenario.get("poa_objective_profiles", []))
    require(poa_modes, "At least one POA strategy is required, including NOT_REQUESTED.")
    require(poa_objectives, "At least one POA objective profile is required, including NOT_REQUESTED.")
    if "NOT_REQUESTED" in poa_modes:
        require(poa_modes == {"NOT_REQUESTED"}, "NOT_REQUESTED cannot be combined with POA strategies.")
        require(poa_objectives == {"NOT_REQUESTED"}, "POA objectives must also be NOT_REQUESTED.")
    else:
        require("NOT_REQUESTED" not in poa_objectives, "NOT_REQUESTED cannot be combined with POA objectives.")
        missing_poa = sorted(POA_COMMON_PARAMETER_IDS - set(parameters))
        require(not missing_poa, f"POA/logistics parameters are missing: {', '.join(missing_poa)}")
        require(set(scenario.get("logistics_simulation_modes", [])) != {"NOT_REQUESTED"}, "POA modes require a logistics simulation.")
        require(parameters["poa.site_permission_status"]["value"] == "APPROVED", "POA sites need approved permission.")
        if poa_modes & {"P0_POA_EXISTENTE", "P1_SEM_NOVA_OBRA"}:
            require("poa.existing_sites_dataset_ref" in parameters, "Existing-infrastructure POA strategies need existing sites.")
        if poa_modes & {"P2_NOVOS_POAS", "P3_INTEGRADO"}:
            required_new_poa = {"poa.candidate_areas_dataset_ref", "poa.maximum_site_count", "poa.minimum_platform_area_m2"}
            missing_new = sorted(required_new_poa - set(parameters))
            require(not missing_new, f"New POA modes are missing parameters: {', '.join(missing_new)}")
        if power_status == "NOT_REVIEWED":
            blockers.append("POA_BLOCKED_BY_UNREVIEWED_POWER")

    override_ids: set[str] = set()
    for index, override in enumerate(request["overrides"]):
        require(isinstance(override, dict), f"overrides[{index}] must be an object.")
        override_id = override.get("override_id")
        require(isinstance(override_id, str) and override_id, f"overrides[{index}] needs override_id.")
        require(override_id not in override_ids, f"Duplicate override id: {override_id}")
        override_ids.add(override_id)
        parameter_id = override.get("parameter_id")
        require(parameter_id in catalog_by_id, f"Override references unknown parameter: {parameter_id}")
        require(override.get("effect") in {"REPLACE_PARAMETER", "RELAX_SOFT_PREFERENCE"}, f"Invalid override effect: {override_id}")
        _parse_datetime(override.get("requested_at"), f"{override_id}.requested_at")
        _validate_responsible(override.get("requested_by"), f"{override_id}.requested_by")
        approval = override.get("approval")
        require(isinstance(approval, dict), f"Override {override_id} needs approval state.")
        status = approval.get("status")
        require(status in {"PENDING", "APPROVED", "REJECTED"}, f"Invalid override approval: {override_id}")
        catalog_item = catalog_by_id[parameter_id]
        if catalog_item["safety_critical"]:
            require(override["effect"] == "REPLACE_PARAMETER", f"Safety parameter cannot be relaxed: {override_id}")
            if status == "APPROVED":
                _validate_responsible(approval.get("approved_by"), f"{override_id}.approval.approved_by")
                _parse_datetime(approval.get("approved_at"), f"{override_id}.approval.approved_at")
                require(isinstance(approval.get("evidence_ref"), str) and approval["evidence_ref"], f"Safety override needs evidence: {override_id}")
            elif status == "PENDING":
                blockers.append(f"PENDING_SAFETY_PARAMETER_REPLACEMENT:{override_id}")

    return {
        "request_id": request["request_id"],
        "parameter_count": len(parameters),
        "dataset_count": len(dataset_by_id),
        "field_boundary_dataset_id": field_boundary_dataset["dataset_id"],
        "dtm_dataset_id": dtm_dataset_id,
        "constraint_layer_count": len(layer_by_id),
        "power_inventory_status": power_status,
        "poa_strategy_modes": sorted(poa_modes),
        "poa_objective_profiles": sorted(poa_objectives),
        "release_blockers": sorted(set(blockers)),
        "warnings": warnings,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument(
        "--request",
        type=Path,
        action="append",
        default=[],
        help="Optional generation request JSON. May be repeated.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        catalog = load_json(args.catalog)
        schema = load_json(args.schema)
        catalog_by_id = validate_catalog(catalog)
        validate_schema(schema, catalog)
        report: dict[str, Any] = {
            "status": "VALID",
            "catalog": {
                "path": str(args.catalog),
                "schema_version": catalog["schema_version"],
                "parameter_count": len(catalog_by_id),
                "safety_critical_count": sum(1 for item in catalog_by_id.values() if item["safety_critical"]),
                "parameter_classes": sorted(PARAMETER_CLASSES),
            },
            "request_schema": {
                "path": str(args.schema),
                "schema_version": schema["properties"]["schema_version"]["const"],
                "constraint_type_count": len(schema["$defs"]["constraintLayer"]["properties"]["type"]["enum"]),
                "poa_strategy_mode_count": len(schema["$defs"]["scenarioRequest"]["properties"]["poa_strategy_modes"]["items"]["enum"]),
                "poa_objective_profile_count": len(schema["$defs"]["scenarioRequest"]["properties"]["poa_objective_profiles"]["items"]["enum"]),
            },
            "requests": [],
        }
        for request_path in args.request:
            request = load_json(request_path)
            request_report = validate_request(request, schema, catalog_by_id)
            request_report["path"] = str(request_path)
            report["requests"].append(request_report)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    except (ContractError, OSError, KeyError, TypeError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
