"""Validate system presets and publish the selectable option matrix.

System presets are E0 references, collection templates, or fail-closed values.
They never replace project evidence and never promote readiness by themselves.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .audit_project_readiness import (
        ContractError,
        validate_acquisition_catalog,
        validate_parameter_catalog,
    )
except ImportError:  # Direct script execution adds scripts/ to sys.path.
    from audit_project_readiness import (
        ContractError,
        validate_acquisition_catalog,
        validate_parameter_catalog,
    )


REPO = Path(__file__).resolve().parents[1]
DEFAULT_PARAMETER_CATALOG = REPO / "config" / "catalogo_parametros_projeto.json"
DEFAULT_ACQUISITION_CATALOG = REPO / "config" / "catalogo_aquisicao_insumos.json"
DEFAULT_MODEL_CATALOG = REPO / "config" / "catalogo_modelos_reais_cana.json"
DEFAULT_PRESET_CATALOG = REPO / "config" / "catalogo_presets_sistema.json"
DEFAULT_OUTPUT = REPO / "dataset" / "derived" / "system_parameter_options.json"

CONFIGURABLE_CLASSES = {"USER_FACT", "RULE_PACK", "OPTIMIZER", "E0_ASSUMPTION"}
EXTERNAL_CLASSES = {"USER_FACT", "RULE_PACK"}
MODEL_RELEASE_CEILINGS = {"E0_TRIAGEM", "NO_PROJECT_RELEASE"}
MODEL_STATUSES = {"ACTIVE", "REFERENCE_ONLY", "VERIFICATION_REQUIRED", "LEGACY"}
MARKET_POSITIONS = {
    "PUBLISHED_AS_COMMON",
    "MARKET_CENSUS_RANKED",
    "REPRESENTATIVE_MAJOR_OEM",
    "OFFICIAL_METHOD",
    "OFFICIAL_DATA_SOURCE",
    "SYSTEM_TEMPLATE",
    "FAIL_CLOSED_POLICY",
    "FIELD_STUDY_PRIOR",
    "REGULATORY_GOVERNANCE",
    "RESEARCH_PROTOCOL",
    "PROCESS_ONLY",
}
RESOLUTION_MODES = {
    "FIXED_E0_VALUE",
    "FAIL_CLOSED_VALUE",
    "REFERENCE_LOOKUP",
    "MODEL_TEMPLATE",
    "REFERENCE_ONLY",
    "CATALOG_SELECTION",
    "FORMULA_DERIVED",
}
DIRECT_VALUE_MODES = {"FIXED_E0_VALUE", "FAIL_CLOSED_VALUE"}
SOURCE_KINDS = {
    "OFFICIAL_DATA",
    "OFFICIAL_METHOD",
    "OFFICIAL_MARKET_CENSUS",
    "OEM",
    "LAW_OR_STANDARD",
    "PRIMARY_RESEARCH",
    "SYSTEM_CONTRACT",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ContractError(f"Invalid JSON in {path}: {exc}") from exc
    require(isinstance(value, dict), f"JSON root must be an object: {path}")
    return value


def _index(items: Any, label: str) -> dict[str, dict[str, Any]]:
    require(isinstance(items, list) and items, f"{label} must be a non-empty array.")
    indexed: dict[str, dict[str, Any]] = {}
    for position, item in enumerate(items):
        require(isinstance(item, dict), f"{label}[{position}] must be an object.")
        item_id = item.get("id")
        require(isinstance(item_id, str) and item_id, f"{label}[{position}] needs id.")
        require(item_id not in indexed, f"Duplicate {label} id: {item_id}")
        indexed[item_id] = item
    return indexed


def _json_type_matches(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return False


def validate_value(value: Any, schema: dict[str, Any], label: str) -> None:
    """Validate the value-schema subset used by the parameter catalog."""
    expected = schema.get("type")
    if isinstance(expected, list):
        require(any(_json_type_matches(value, item) for item in expected), f"Invalid type for {label}.")
    elif isinstance(expected, str):
        require(_json_type_matches(value, expected), f"Invalid type for {label}: expected {expected}.")

    if "const" in schema:
        require(value == schema["const"], f"Value violates const for {label}.")
    if "enum" in schema:
        require(value in schema["enum"], f"Value is outside enum for {label}: {value!r}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema:
            require(value >= schema["minimum"], f"Value is below minimum for {label}.")
        if "exclusiveMinimum" in schema:
            require(value > schema["exclusiveMinimum"], f"Value is below exclusiveMinimum for {label}.")
        if "maximum" in schema:
            require(value <= schema["maximum"], f"Value is above maximum for {label}.")
        if "exclusiveMaximum" in schema:
            require(value < schema["exclusiveMaximum"], f"Value is above exclusiveMaximum for {label}.")
    if isinstance(value, str):
        if "minLength" in schema:
            require(len(value) >= schema["minLength"], f"String is too short for {label}.")
    if isinstance(value, list):
        if "minItems" in schema:
            require(len(value) >= schema["minItems"], f"Array is too short for {label}.")
        if schema.get("uniqueItems"):
            normalized = [json.dumps(item, sort_keys=True, ensure_ascii=True) for item in value]
            require(len(normalized) == len(set(normalized)), f"Array has duplicate values for {label}.")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                validate_value(item, item_schema, f"{label}[{index}]")


def validate_model_catalog(
    catalog: dict[str, Any],
    parameters: dict[str, dict[str, Any]],
    packages: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    require(catalog.get("schema_version") == "1.0.0", "Unexpected reference model catalog version.")
    require(catalog.get("catalog_id") == "terraflux-sugarcane-reference-model-catalog", "Unexpected reference model catalog id.")
    try:
        date.fromisoformat(str(catalog.get("as_of", "")))
    except ValueError as exc:
        raise ContractError("Reference model catalog as_of is not an ISO date.") from exc

    policy = catalog.get("market_claim_policy")
    require(isinstance(policy, dict) and isinstance(policy.get("rules"), list) and policy["rules"], "Missing market claim policy.")
    sources = _index(catalog.get("source_registry"), "reference source")
    models = _index(catalog.get("models"), "reference model")

    for source_id, source in sources.items():
        require(source.get("source_kind") in SOURCE_KINDS, f"Unknown source kind: {source_id}")
        require(isinstance(source.get("url"), str) and source["url"].startswith(("https://", "config/", "docs/")), f"Invalid source URL: {source_id}")
        require(isinstance(source.get("locators"), list) and source["locators"], f"Missing source locators: {source_id}")
        require(isinstance(source.get("limitations"), list) and source["limitations"], f"Missing source limitations: {source_id}")

    primary_owner = {
        parameter_id: package_id
        for package_id, package in packages.items()
        for parameter_id in package["primary_parameter_ids"]
    }

    for model_id, model in models.items():
        require(model.get("status") in MODEL_STATUSES, f"Invalid model status: {model_id}")
        require(model.get("market_position") in MARKET_POSITIONS, f"Invalid market position: {model_id}")
        require(model.get("release_ceiling") in MODEL_RELEASE_CEILINGS, f"Invalid release ceiling: {model_id}")
        source_ids = model.get("source_ids")
        require(isinstance(source_ids, list) and source_ids, f"No sources on model: {model_id}")
        require(len(source_ids) == len(set(source_ids)), f"Duplicate source on model: {model_id}")
        require(set(source_ids) <= set(sources), f"Unknown source on model: {model_id}")
        supported = model.get("supported_package_ids")
        require(isinstance(supported, list) and supported, f"No supported packages on model: {model_id}")
        require(len(supported) == len(set(supported)), f"Duplicate supported package: {model_id}")
        require(set(supported) <= set(packages), f"Unknown supported package on model: {model_id}")
        require(isinstance(model.get("local_confirmation_requirements"), list) and model["local_confirmation_requirements"], f"No local confirmation contract: {model_id}")
        require(isinstance(model.get("limitations"), list) and model["limitations"], f"No limitations: {model_id}")

        candidates = model.get("parameter_candidates")
        require(isinstance(candidates, list), f"parameter_candidates must be an array: {model_id}")
        candidate_ids: set[str] = set()
        for candidate in candidates:
            require(isinstance(candidate, dict), f"Invalid parameter candidate on {model_id}.")
            parameter_id = candidate.get("parameter_id")
            require(parameter_id in parameters, f"Unknown parameter {parameter_id} on {model_id}")
            require(parameter_id not in candidate_ids, f"Duplicate parameter {parameter_id} on {model_id}")
            candidate_ids.add(parameter_id)
            parameter = parameters[parameter_id]
            require(parameter["parameter_class"] != "CALCULATED", f"Calculated parameter cannot be supplied by a model: {parameter_id}")
            require(candidate.get("resolution_mode") in RESOLUTION_MODES, f"Invalid resolution mode on {model_id}/{parameter_id}")
            require(candidate.get("unit") == parameter.get("unit"), f"Unit mismatch on {model_id}/{parameter_id}")
            require(isinstance(candidate.get("requires_user_confirmation"), bool), f"Missing confirmation flag on {model_id}/{parameter_id}")

            owner = primary_owner.get(parameter_id)
            if owner is not None:
                require(owner in supported, f"Model {model_id} supplies {parameter_id} but does not support owner package {owner}.")

            resolution_mode = candidate["resolution_mode"]
            if resolution_mode in DIRECT_VALUE_MODES:
                require("value" in candidate, f"Direct candidate has no value: {model_id}/{parameter_id}")
                validate_value(candidate["value"], parameter["value_schema"], f"{model_id}/{parameter_id}")
            if resolution_mode == "FIXED_E0_VALUE":
                require(model["release_ceiling"] == "E0_TRIAGEM", f"Fixed prior must have E0 ceiling: {model_id}")
                if parameter.get("safety_critical"):
                    require(candidate["requires_user_confirmation"] is True, f"Safety-critical E0 prior must require confirmation: {model_id}/{parameter_id}")
            if resolution_mode == "FAIL_CLOSED_VALUE":
                default = parameter.get("default", {})
                require(default.get("policy") == "FAIL_CLOSED", f"Fail-closed model targets non fail-closed parameter: {model_id}/{parameter_id}")
                require(default.get("default_is_fail_closed") is True, f"Catalog does not mark fail-closed value: {parameter_id}")
                require(candidate["value"] == default.get("value"), f"Fail-closed value diverges from catalog: {model_id}/{parameter_id}")

        non_system_sources = [sources[source_id] for source_id in source_ids if sources[source_id]["source_kind"] != "SYSTEM_CONTRACT"]
        if non_system_sources:
            require(model["release_ceiling"] in MODEL_RELEASE_CEILINGS, f"External reference attempts project release: {model_id}")
        if model["market_position"] in {"REGULATORY_GOVERNANCE", "RESEARCH_PROTOCOL", "PROCESS_ONLY"}:
            require(model["release_ceiling"] == "NO_PROJECT_RELEASE", f"Process or governance model cannot release project values: {model_id}")
        if model["market_position"] == "PROCESS_ONLY":
            require(model["model_data"].get("automatic_numeric_solver") is False, f"Process-only model exposes a numeric solver: {model_id}")
            require(model["model_data"].get("execution_status_without_overlay") == "FAIL_CLOSED", f"Process-only model must fail closed: {model_id}")

    return sources, models


def validate_preset_catalog(
    catalog: dict[str, Any],
    parameters: dict[str, dict[str, Any]],
    packages: dict[str, dict[str, Any]],
    models: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    require(catalog.get("schema_version") == "1.0.0", "Unexpected system preset catalog version.")
    require(catalog.get("catalog_id") == "terraflux-system-preset-catalog", "Unexpected system preset catalog id.")
    contracts = catalog.get("contracts", {})
    require(contracts.get("parameter_catalog_revision") == "terraflux-project-parameter-catalog-1.2.0", "Preset catalog references another parameter catalog.")
    require(contracts.get("acquisition_catalog_revision") == "terraflux-input-acquisition-catalog-1.0.0", "Preset catalog references another acquisition catalog.")
    require(contracts.get("reference_model_catalog_revision") == "terraflux-sugarcane-reference-model-catalog-1.0.0", "Preset catalog references another model catalog.")
    policy = catalog.get("selection_policy", {})
    require(policy.get("default_application") == "E0_ONLY_UNLESS_REPLACED_BY_PROJECT_EVIDENCE", "Unsafe default application policy.")

    package_profiles_list = catalog.get("package_profiles")
    require(isinstance(package_profiles_list, list) and package_profiles_list, "package_profiles must be a non-empty array.")
    package_profiles: dict[str, dict[str, Any]] = {}
    for profile in package_profiles_list:
        package_id = profile.get("package_id")
        require(package_id in packages, f"Unknown package profile: {package_id}")
        require(package_id not in package_profiles, f"Duplicate package profile: {package_id}")
        package_profiles[package_id] = profile
        defaults = profile.get("default_model_ids")
        selectable = profile.get("selectable_model_ids")
        require(isinstance(defaults, list) and defaults, f"No default model on {package_id}")
        require(isinstance(selectable, list) and selectable, f"No selectable model on {package_id}")
        require(len(defaults) == len(set(defaults)), f"Duplicate default model on {package_id}")
        require(len(selectable) == len(set(selectable)), f"Duplicate selectable model on {package_id}")
        require(set(defaults) <= set(selectable), f"Default is not selectable on {package_id}")
        require(set(selectable) <= set(models), f"Unknown selectable model on {package_id}")
        for model_id in selectable:
            require(package_id in models[model_id]["supported_package_ids"], f"Model {model_id} does not support {package_id}")
        require(profile.get("unbound_parameter_policy") in {"LOCAL_INPUT_TEMPLATE", "FAIL_CLOSED_OR_LOCAL_INPUT_TEMPLATE"}, f"Invalid unbound policy on {package_id}")
        custom_mode = profile.get("custom_mode", {})
        require(custom_mode == {"enabled": True, "preserve_provenance": True, "validate_against_parameter_schema": True}, f"Unsafe custom mode on {package_id}")
        require(profile.get("release_effect") == "DOES_NOT_RESOLVE_PROJECT_EVIDENCE", f"Preset attempts to resolve evidence: {package_id}")
    require(set(package_profiles) == set(packages), "Every evidence package must have exactly one system preset profile.")

    standalone_list = catalog.get("standalone_parameter_profiles")
    require(isinstance(standalone_list, list), "standalone_parameter_profiles must be an array.")
    standalone: dict[str, dict[str, Any]] = {}
    for profile in standalone_list:
        parameter_id = profile.get("parameter_id")
        require(parameter_id in parameters, f"Unknown standalone parameter: {parameter_id}")
        require(parameter_id not in standalone, f"Duplicate standalone parameter: {parameter_id}")
        standalone[parameter_id] = profile
        require(parameters[parameter_id]["parameter_class"] in {"OPTIMIZER", "E0_ASSUMPTION"}, f"External parameter cannot be standalone: {parameter_id}")
        selectable = profile.get("selectable_model_ids")
        require(isinstance(selectable, list) and selectable, f"No selectable model for {parameter_id}")
        require(len(selectable) == len(set(selectable)), f"Duplicate selectable model for {parameter_id}")
        require(set(selectable) <= set(models), f"Unknown standalone model for {parameter_id}")
        require(profile.get("default_model_id") in selectable, f"Standalone default is not selectable: {parameter_id}")
        for model_id in selectable:
            supplied = {item["parameter_id"] for item in models[model_id]["parameter_candidates"]}
            require(parameter_id in supplied, f"Model {model_id} does not supply standalone parameter {parameter_id}")
        custom_mode = profile.get("custom_mode", {})
        require(custom_mode == {"enabled": True, "preserve_provenance": True, "validate_against_parameter_schema": True}, f"Unsafe standalone custom mode: {parameter_id}")
        require(profile.get("release_effect") == "DOES_NOT_RESOLVE_PROJECT_EVIDENCE", f"Standalone preset attempts to resolve evidence: {parameter_id}")

    expected_standalone = {
        parameter_id
        for parameter_id, parameter in parameters.items()
        if parameter["parameter_class"] in {"OPTIMIZER", "E0_ASSUMPTION"}
    }
    require(set(standalone) == expected_standalone, "Every optimizer and E0 assumption must have exactly one standalone profile.")
    return package_profiles, standalone


def build_option_matrix(
    parameters: dict[str, dict[str, Any]],
    packages: dict[str, dict[str, Any]],
    sources: dict[str, dict[str, Any]],
    models: dict[str, dict[str, Any]],
    package_profiles: dict[str, dict[str, Any]],
    standalone: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    owner = {
        parameter_id: package_id
        for package_id, package in packages.items()
        for parameter_id in package["primary_parameter_ids"]
    }
    model_candidates: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for model_id, model in models.items():
        for candidate in model["parameter_candidates"]:
            model_candidates.setdefault(candidate["parameter_id"], []).append((model_id, candidate))

    option_rows: list[dict[str, Any]] = []
    coverage_counter: Counter[str] = Counter()
    for parameter_id, parameter in parameters.items():
        if parameter["parameter_class"] not in CONFIGURABLE_CLASSES:
            continue
        package_id = owner.get(parameter_id)
        if package_id:
            profile = package_profiles[package_id]
            default_ids = profile["default_model_ids"]
            selectable_ids = set(profile["selectable_model_ids"])
            unbound_policy = profile["unbound_parameter_policy"]
        else:
            profile = standalone[parameter_id]
            default_ids = [profile["default_model_id"]]
            selectable_ids = set(profile["selectable_model_ids"])
            unbound_policy = "LOCAL_INPUT_TEMPLATE"

        options: list[dict[str, Any]] = []
        for model_id, candidate in model_candidates.get(parameter_id, []):
            if model_id not in selectable_ids:
                continue
            options.append(
                {
                    "model_id": model_id,
                    "model_title": models[model_id]["title"],
                    "resolution_mode": candidate["resolution_mode"],
                    "value": candidate.get("value"),
                    "unit": candidate["unit"],
                    "release_ceiling": models[model_id]["release_ceiling"],
                    "source_ids": models[model_id]["source_ids"],
                    "requires_user_confirmation": candidate["requires_user_confirmation"],
                    "is_default_model": model_id in default_ids,
                }
            )

        default_options = [item for item in options if item["is_default_model"]]
        if any(item["resolution_mode"] == "FAIL_CLOSED_VALUE" for item in default_options):
            default_behavior = "FAIL_CLOSED"
        elif any(item["resolution_mode"] == "FIXED_E0_VALUE" for item in default_options):
            default_behavior = "E0_VALUE"
        elif default_options:
            default_behavior = "REFERENCE_OR_TEMPLATE"
        else:
            default_behavior = unbound_policy
        coverage_counter[default_behavior] += 1

        option_rows.append(
            {
                "parameter_id": parameter_id,
                "label": parameter["label"],
                "parameter_class": parameter["parameter_class"],
                "unit": parameter.get("unit"),
                "safety_critical": parameter["safety_critical"],
                "owner_package_id": package_id,
                "default_model_ids": default_ids,
                "default_behavior": default_behavior,
                "system_options": options,
                "local_input": {
                    "allowed": True,
                    "value_schema": parameter["value_schema"],
                    "required_provenance": True,
                },
                "release_effect": "E0_ONLY_AND_DOES_NOT_RESOLVE_PROJECT_EVIDENCE",
            }
        )

    require(len(option_rows) == sum(item["parameter_class"] in CONFIGURABLE_CLASSES for item in parameters.values()), "Option matrix does not cover every configurable parameter.")
    return {
        "schema_version": "1.0.0",
        "artifact_id": "terraflux-system-parameter-options",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "contracts": {
            "parameter_catalog": "terraflux-project-parameter-catalog-1.2.0",
            "acquisition_catalog": "terraflux-input-acquisition-catalog-1.0.0",
            "reference_model_catalog": "terraflux-sugarcane-reference-model-catalog-1.0.0",
            "system_preset_catalog": "terraflux-system-preset-catalog-1.0.0",
        },
        "summary": {
            "parameter_count": len(parameters),
            "configurable_parameter_count": len(option_rows),
            "external_parameter_count": sum(item["parameter_class"] in EXTERNAL_CLASSES for item in parameters.values()),
            "calculated_parameter_count": sum(item["parameter_class"] == "CALCULATED" for item in parameters.values()),
            "evidence_package_count": len(packages),
            "reference_source_count": len(sources),
            "reference_model_count": len(models),
            "coverage_by_default_behavior": dict(sorted(coverage_counter.items())),
            "custom_value_supported_count": len(option_rows),
            "project_evidence_resolved_by_presets": 0,
            "maximum_delivery_level_from_presets": "E0_TRIAGEM",
        },
        "interpretation": [
            "A preset resolves how to prefill, collect or block a parameter; it does not prove the project value.",
            "Official data, market census, OEM catalogs and studies are screening references only.",
            "Every configurable parameter accepts a custom value with schema, unit and provenance validation.",
            "Project readiness remains governed by the evidence inventory and professional gates.",
        ],
        "parameters": option_rows,
    }


def audit(
    parameter_catalog_path: Path,
    acquisition_catalog_path: Path,
    model_catalog_path: Path,
    preset_catalog_path: Path,
) -> dict[str, Any]:
    parameters = validate_parameter_catalog(load_json(parameter_catalog_path))
    packages = validate_acquisition_catalog(load_json(acquisition_catalog_path), parameters)
    sources, models = validate_model_catalog(load_json(model_catalog_path), parameters, packages)
    package_profiles, standalone = validate_preset_catalog(
        load_json(preset_catalog_path), parameters, packages, models
    )
    return build_option_matrix(parameters, packages, sources, models, package_profiles, standalone)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parameter-catalog", type=Path, default=DEFAULT_PARAMETER_CATALOG)
    parser.add_argument("--acquisition-catalog", type=Path, default=DEFAULT_ACQUISITION_CATALOG)
    parser.add_argument("--model-catalog", type=Path, default=DEFAULT_MODEL_CATALOG)
    parser.add_argument("--preset-catalog", type=Path, default=DEFAULT_PRESET_CATALOG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        report = audit(
            args.parameter_catalog,
            args.acquisition_catalog,
            args.model_catalog,
            args.preset_catalog,
        )
    except (ContractError, OSError) as exc:
        print(f"PRESET AUDIT FAILED: {exc}")
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    summary = report["summary"]
    print(
        "PRESET AUDIT OK: "
        f"{summary['configurable_parameter_count']}/{summary['configurable_parameter_count']} configurable parameters, "
        f"{summary['evidence_package_count']} packages, "
        f"{summary['reference_model_count']} models, "
        "0 project evidence items auto-resolved."
    )
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
