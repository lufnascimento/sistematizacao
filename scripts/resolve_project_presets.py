"""Resolve project preset choices into an E0-only, evidence-neutral manifest."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .audit_project_readiness import ContractError, validate_acquisition_catalog, validate_parameter_catalog
    from .audit_system_presets import (
        DEFAULT_ACQUISITION_CATALOG,
        DEFAULT_MODEL_CATALOG,
        DEFAULT_PARAMETER_CATALOG,
        DEFAULT_PRESET_CATALOG,
        load_json,
        require,
        validate_model_catalog,
        validate_preset_catalog,
        validate_value,
    )
except ImportError:  # Direct script execution adds scripts/ to sys.path.
    from audit_project_readiness import ContractError, validate_acquisition_catalog, validate_parameter_catalog
    from audit_system_presets import (
        DEFAULT_ACQUISITION_CATALOG,
        DEFAULT_MODEL_CATALOG,
        DEFAULT_PARAMETER_CATALOG,
        DEFAULT_PRESET_CATALOG,
        load_json,
        require,
        validate_model_catalog,
        validate_preset_catalog,
        validate_value,
    )


REPO = Path(__file__).resolve().parents[1]
DEFAULT_SELECTION = REPO / "config" / "exemplo_selecao_presets_sistema.json"
DEFAULT_OUTPUT = REPO / "dataset" / "derived" / "example_preset_resolution.json"

DELIVERY_LEVELS = {"E0_TRIAGEM", "E1_OPERACIONAL", "E2_CONSERVACIONISTA", "E3_EXECUTIVO"}
PACKAGE_SELECTION_MODES = {"SYSTEM_DEFAULT", "SYSTEM_MODELS", "CUSTOM", "HYBRID"}
STANDALONE_SELECTION_MODES = {"SYSTEM_DEFAULT", "SYSTEM_MODEL", "CUSTOM"}
CLASS_ORIGINS = {
    "USER_FACT": {"DECLARED", "UPLOADED", "MEASURED", "IMPORTED"},
    "RULE_PACK": {"RULE_PACK", "PROFESSIONAL_CALIBRATION"},
    "OPTIMIZER": {"DECLARED", "OPTIMIZER"},
    "E0_ASSUMPTION": {"E0_ASSUMPTION"},
}
REGION_BY_STATE = {
    "AC": {"NORTH"}, "AL": {"NORTHEAST"}, "AP": {"NORTH"}, "AM": {"NORTH"},
    "BA": {"NORTHEAST"}, "CE": {"NORTHEAST"}, "DF": {"CENTER_WEST"}, "ES": {"SOUTHEAST"},
    "GO": {"CENTER_WEST"}, "MA": {"NORTHEAST"}, "MT": {"CENTER_WEST"}, "MS": {"CENTER_WEST"},
    "MG": {"SOUTHEAST"}, "PA": {"NORTH"}, "PB": {"NORTHEAST"}, "PR": {"SOUTH"},
    "PE": {"NORTHEAST"}, "PI": {"NORTHEAST"}, "RJ": {"SOUTHEAST"}, "RN": {"NORTHEAST"},
    "RS": {"SOUTH"}, "RO": {"NORTH"}, "RR": {"NORTH"}, "SC": {"SOUTH"},
    "SP": {"SOUTHEAST"}, "SE": {"NORTHEAST"}, "TO": {"NORTH"},
}


def _parse_datetime(value: Any, label: str) -> None:
    require(isinstance(value, str) and value, f"{label} must be an ISO date-time.")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{label} must be an ISO date-time.") from exc


def _validate_applicability(
    model: dict[str, Any],
    context: dict[str, str],
    justification: str | None,
) -> str:
    applicability = model["applicability"]
    countries = set(applicability["country"])
    crops = set(applicability["crop"])
    jurisdictions = set(applicability["jurisdiction"])
    require(context["country"] in countries or "ALL" in countries, f"Model {model['id']} does not apply to country {context['country']}.")
    require(context["crop"] in crops or "ANY" in crops, f"Model {model['id']} does not apply to crop {context['crop']}.")
    regional_tags = set(REGION_BY_STATE.get(context["state"], set()))
    if regional_tags & {"SOUTHEAST", "SOUTH", "CENTER_WEST"}:
        regional_tags.add("CENTER_SOUTH")
    exact_jurisdictions = {
        context["state"],
        f"{context['country']}-{context['state']}",
        *regional_tags,
    }
    if "ALL" in jurisdictions or jurisdictions & exact_jurisdictions:
        return "EXACT_OR_NATIONAL"
    conditional = {item for item in jurisdictions if item.endswith("WITH_JUSTIFICATION")}
    require(bool(conditional), f"Model {model['id']} does not apply to jurisdiction {context['state']}.")
    require(isinstance(justification, str) and justification.strip(), f"Model {model['id']} needs an applicability justification for {context['state']}.")
    return "JUSTIFIED_EXTRAPOLATION"


def validate_selection(
    selection: dict[str, Any],
    parameters: dict[str, dict[str, Any]],
    packages: dict[str, dict[str, Any]],
    models: dict[str, dict[str, Any]],
    package_profiles: dict[str, dict[str, Any]],
    standalone_profiles: dict[str, dict[str, Any]],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    require(selection.get("schema_version") == "1.0.0", "Unexpected project preset selection version.")
    require(isinstance(selection.get("selection_id"), str) and selection["selection_id"], "Missing selection_id.")
    require(isinstance(selection.get("project_id"), str) and selection["project_id"], "Missing project_id.")
    _parse_datetime(selection.get("created_at"), "created_at")
    require(selection.get("requested_delivery_level") in DELIVERY_LEVELS, "Unknown requested delivery level.")
    require(isinstance(selection.get("use_system_defaults_for_unselected_packages"), bool), "Missing default selection policy.")
    context = selection.get("project_context")
    require(isinstance(context, dict) and set(context) == {"country", "state", "crop"}, "project_context must contain country, state and crop.")
    require(all(isinstance(context[key], str) and context[key] for key in context), "Invalid project_context.")
    require(context["crop"] == "SUGARCANE", "This preset catalog is for sugarcane.")

    package_selections: dict[str, dict[str, Any]] = {}
    raw_package_selections = selection.get("package_selections")
    require(isinstance(raw_package_selections, list), "package_selections must be an array.")
    for item in raw_package_selections:
        require(isinstance(item, dict), "Package selection must be an object.")
        package_id = item.get("package_id")
        require(package_id in packages, f"Unknown selected package: {package_id}")
        require(package_id not in package_selections, f"Duplicate package selection: {package_id}")
        mode = item.get("mode")
        model_ids = item.get("model_ids")
        justification = item.get("applicability_justification")
        require(mode in PACKAGE_SELECTION_MODES, f"Invalid package selection mode: {package_id}")
        require(isinstance(model_ids, list) and len(model_ids) == len(set(model_ids)), f"Invalid model list: {package_id}")
        if mode == "SYSTEM_DEFAULT":
            require(not model_ids, f"SYSTEM_DEFAULT cannot list models: {package_id}")
            effective_models = package_profiles[package_id]["default_model_ids"]
        elif mode in {"SYSTEM_MODELS", "HYBRID"}:
            require(model_ids, f"{mode} needs at least one model: {package_id}")
            require(set(model_ids) <= set(package_profiles[package_id]["selectable_model_ids"]), f"Non-selectable model on {package_id}")
            effective_models = model_ids
        else:
            require(not model_ids, f"CUSTOM cannot list system models: {package_id}")
            effective_models = []
        applicability_results = {
            model_id: _validate_applicability(models[model_id], context, justification)
            for model_id in effective_models
        }
        package_selections[package_id] = {
            "package_id": package_id,
            "mode": mode,
            "model_ids": list(effective_models),
            "applicability_justification": justification,
            "applicability_results": applicability_results,
        }

    for package_id in packages:
        if package_id in package_selections:
            continue
        if selection["use_system_defaults_for_unselected_packages"]:
            model_ids = package_profiles[package_id]["default_model_ids"]
            applicability_results = {
                model_id: _validate_applicability(models[model_id], context, None)
                for model_id in model_ids
            }
            package_selections[package_id] = {
                "package_id": package_id,
                "mode": "SYSTEM_DEFAULT",
                "model_ids": list(model_ids),
                "applicability_justification": None,
                "applicability_results": applicability_results,
            }
        else:
            package_selections[package_id] = {
                "package_id": package_id,
                "mode": "CUSTOM",
                "model_ids": [],
                "applicability_justification": None,
                "applicability_results": {},
            }

    standalone_selections: dict[str, dict[str, Any]] = {}
    raw_standalone = selection.get("standalone_parameter_selections")
    require(isinstance(raw_standalone, list), "standalone_parameter_selections must be an array.")
    for item in raw_standalone:
        require(isinstance(item, dict), "Standalone selection must be an object.")
        parameter_id = item.get("parameter_id")
        require(parameter_id in standalone_profiles, f"Unknown standalone parameter selection: {parameter_id}")
        require(parameter_id not in standalone_selections, f"Duplicate standalone selection: {parameter_id}")
        mode = item.get("mode")
        model_id = item.get("model_id")
        require(mode in STANDALONE_SELECTION_MODES, f"Invalid standalone mode: {parameter_id}")
        if mode == "SYSTEM_DEFAULT":
            require(model_id is None, f"SYSTEM_DEFAULT cannot name a model: {parameter_id}")
            effective_model = standalone_profiles[parameter_id]["default_model_id"]
        elif mode == "SYSTEM_MODEL":
            require(model_id in standalone_profiles[parameter_id]["selectable_model_ids"], f"Non-selectable standalone model: {parameter_id}")
            effective_model = model_id
        else:
            require(model_id is None, f"CUSTOM cannot name a model: {parameter_id}")
            effective_model = None
        if effective_model:
            _validate_applicability(models[effective_model], context, None)
        standalone_selections[parameter_id] = {
            "parameter_id": parameter_id,
            "mode": mode,
            "model_id": effective_model,
        }

    for parameter_id, profile in standalone_profiles.items():
        if parameter_id not in standalone_selections:
            effective_model = profile["default_model_id"]
            _validate_applicability(models[effective_model], context, None)
            standalone_selections[parameter_id] = {
                "parameter_id": parameter_id,
                "mode": "SYSTEM_DEFAULT",
                "model_id": effective_model,
            }

    custom_values: dict[str, dict[str, Any]] = {}
    raw_custom = selection.get("custom_parameter_values")
    require(isinstance(raw_custom, list), "custom_parameter_values must be an array.")
    for item in raw_custom:
        require(isinstance(item, dict), "Custom parameter value must be an object.")
        parameter_id = item.get("parameter_id")
        require(parameter_id in parameters, f"Unknown custom parameter: {parameter_id}")
        require(parameter_id not in custom_values, f"Duplicate custom parameter: {parameter_id}")
        parameter = parameters[parameter_id]
        require(parameter["parameter_class"] in CLASS_ORIGINS, f"Calculated parameter cannot be customized: {parameter_id}")
        require(item.get("unit") == parameter.get("unit"), f"Custom unit mismatch: {parameter_id}")
        require("value" in item, f"Custom value missing: {parameter_id}")
        validate_value(item["value"], parameter["value_schema"], parameter_id)
        provenance = item.get("provenance")
        require(isinstance(provenance, dict), f"Custom provenance missing: {parameter_id}")
        require(set(provenance) == {"origin", "source_ref", "captured_at", "responsible", "revision"}, f"Invalid custom provenance fields: {parameter_id}")
        require(provenance["origin"] in CLASS_ORIGINS[parameter["parameter_class"]], f"Origin is not allowed for {parameter_id}")
        for field in ("source_ref", "responsible", "revision"):
            require(isinstance(provenance[field], str) and provenance[field], f"Missing {field} on {parameter_id}")
        _parse_datetime(provenance["captured_at"], f"{parameter_id}.captured_at")
        custom_values[parameter_id] = item

    for parameter_id, item in standalone_selections.items():
        if item["mode"] == "CUSTOM":
            require(parameter_id in custom_values, f"Standalone CUSTOM selection needs a custom value: {parameter_id}")
    return package_selections, standalone_selections, custom_values


def _candidate_for(model: dict[str, Any], parameter_id: str) -> dict[str, Any] | None:
    return next(
        (candidate for candidate in model["parameter_candidates"] if candidate["parameter_id"] == parameter_id),
        None,
    )


def _merge_direct_values(contributions: list[dict[str, Any]], parameter_id: str) -> Any:
    values = [item["value"] for item in contributions]
    if values and all(isinstance(value, dict) for value in values):
        merged: dict[str, Any] = {}
        for value in values:
            for key, item in value.items():
                if key in merged:
                    require(merged[key] == item, f"Conflicting direct preset values for {parameter_id}.{key}")
                merged[key] = item
        return merged
    normalized_values = {json.dumps(value, sort_keys=True, ensure_ascii=True) for value in values}
    require(len(normalized_values) == 1, f"Conflicting direct preset values for {parameter_id}")
    return values[0]


def build_resolution(
    selection: dict[str, Any],
    parameters: dict[str, dict[str, Any]],
    packages: dict[str, dict[str, Any]],
    models: dict[str, dict[str, Any]],
    package_profiles: dict[str, dict[str, Any]],
    package_selections: dict[str, dict[str, Any]],
    standalone_selections: dict[str, dict[str, Any]],
    custom_values: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    primary_owner = {
        parameter_id: package_id
        for package_id, package in packages.items()
        for parameter_id in package["primary_parameter_ids"]
    }
    resolution_rows: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()

    for parameter_id, parameter in parameters.items():
        if parameter["parameter_class"] == "CALCULATED":
            continue
        owner_package_id = primary_owner.get(parameter_id)
        if owner_package_id:
            selected_model_ids = package_selections[owner_package_id]["model_ids"]
            unbound_policy = package_profiles[owner_package_id]["unbound_parameter_policy"]
        else:
            model_id = standalone_selections[parameter_id]["model_id"]
            selected_model_ids = [model_id] if model_id else []
            unbound_policy = "LOCAL_INPUT_TEMPLATE"

        contributions: list[dict[str, Any]] = []
        for model_id in selected_model_ids:
            candidate = _candidate_for(models[model_id], parameter_id)
            if candidate is None:
                continue
            contributions.append(
                {
                    "model_id": model_id,
                    "model_title": models[model_id]["title"],
                    "resolution_mode": candidate["resolution_mode"],
                    "value": candidate.get("value"),
                    "unit": candidate["unit"],
                    "source_ids": models[model_id]["source_ids"],
                    "release_ceiling": models[model_id]["release_ceiling"],
                    "requires_user_confirmation": candidate["requires_user_confirmation"],
                }
            )

        proposed_value: Any = None
        custom = custom_values.get(parameter_id)
        if custom:
            status = "CUSTOM_VALUE_PENDING_EVIDENCE_VALIDATION"
            proposed_value = custom["value"]
        else:
            direct = [item for item in contributions if item["resolution_mode"] in {"FIXED_E0_VALUE", "FAIL_CLOSED_VALUE"}]
            if direct:
                proposed_value = _merge_direct_values(direct, parameter_id)
                status = "FAIL_CLOSED" if any(item["resolution_mode"] == "FAIL_CLOSED_VALUE" for item in direct) else "SYSTEM_E0_VALUE"
            else:
                reference_values = [item for item in contributions if item["resolution_mode"] == "REFERENCE_ONLY" and item["value"] is not None]
                modes = {item["resolution_mode"] for item in contributions}
                if len(reference_values) == 1 and len(contributions) == 1:
                    status = "REFERENCE_SELECTED_E0"
                    proposed_value = reference_values[0]["value"]
                elif reference_values:
                    status = "REFERENCE_ASSEMBLY_REQUIRED"
                elif "REFERENCE_LOOKUP" in modes:
                    status = "REFERENCE_LOOKUP_PENDING"
                elif modes & {"MODEL_TEMPLATE", "CATALOG_SELECTION", "FORMULA_DERIVED"}:
                    status = "MODEL_TEMPLATE_REQUIRED"
                elif parameter.get("default", {}).get("policy") == "FAIL_CLOSED" and unbound_policy == "FAIL_CLOSED_OR_LOCAL_INPUT_TEMPLATE":
                    status = "FAIL_CLOSED"
                    proposed_value = parameter["default"]["value"]
                elif unbound_policy == "FAIL_CLOSED_OR_LOCAL_INPUT_TEMPLATE":
                    status = "FAIL_CLOSED_OR_LOCAL_INPUT_REQUIRED"
                else:
                    status = "LOCAL_INPUT_REQUIRED"

        status_counts[status] += 1
        source_ids = sorted({source_id for item in contributions for source_id in item["source_ids"]})
        required_actions = sorted({requirement for model_id in selected_model_ids for requirement in models[model_id]["local_confirmation_requirements"]})
        parameter_release_ceiling = (
            "NO_PROJECT_RELEASE"
            if any(models[model_id]["release_ceiling"] == "NO_PROJECT_RELEASE" for model_id in selected_model_ids)
            else "E0_TRIAGEM"
        )
        resolution_rows.append(
            {
                "parameter_id": parameter_id,
                "label": parameter["label"],
                "parameter_class": parameter["parameter_class"],
                "unit": parameter.get("unit"),
                "safety_critical": parameter["safety_critical"],
                "owner_package_id": owner_package_id,
                "status": status,
                "proposed_value": proposed_value,
                "custom_provenance": custom["provenance"] if custom else None,
                "selected_model_ids": selected_model_ids,
                "contributions": contributions,
                "source_ids": source_ids,
                "required_actions": required_actions,
                "project_evidence_resolved": False,
                "release_ceiling": parameter_release_ceiling,
            }
        )

    package_rows: list[dict[str, Any]] = []
    for package_id, selected in package_selections.items():
        model_ids = selected["model_ids"]
        package_release_ceiling = (
            "NO_PROJECT_RELEASE"
            if any(models[model_id]["release_ceiling"] == "NO_PROJECT_RELEASE" for model_id in model_ids)
            else "E0_TRIAGEM"
        )
        package_rows.append(
            {
                **selected,
                "model_titles": [models[model_id]["title"] for model_id in model_ids],
                "local_confirmation_requirements": sorted({requirement for model_id in model_ids for requirement in models[model_id]["local_confirmation_requirements"]}),
                "release_ceiling": package_release_ceiling,
                "project_evidence_resolved": False,
            }
        )

    requested = selection["requested_delivery_level"]
    return {
        "schema_version": "1.0.0",
        "artifact_id": "terraflux-project-preset-resolution",
        "selection_id": selection["selection_id"],
        "project_id": selection["project_id"],
        "project_context": selection["project_context"],
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "requested_delivery_level": requested,
        "summary": {
            "configurable_parameter_count": len(resolution_rows),
            "parameter_status_counts": dict(sorted(status_counts.items())),
            "package_count": len(package_rows),
            "selected_reference_model_count": len({model_id for row in package_rows for model_id in row["model_ids"]} | {item["model_id"] for item in standalone_selections.values() if item["model_id"]}),
            "custom_parameter_count": len(custom_values),
            "project_evidence_resolved_by_selection": 0,
            "maximum_delivery_level_from_selection": "E0_TRIAGEM",
            "requested_level_released_by_selection": requested == "E0_TRIAGEM",
        },
        "delivery_gates": {
            "E0_TRIAGEM": {"eligible_from_preset_resolution": True, "still_requires_project_request_validation": True},
            "E1_OPERACIONAL": {"eligible_from_preset_resolution": False, "reason": "project evidence and machine trials required"},
            "E2_CONSERVACIONISTA": {"eligible_from_preset_resolution": False, "reason": "approved PCE/PCX, hydrology, soil, receivers and professional review required"},
            "E3_EXECUTIVO": {"eligible_from_preset_resolution": False, "reason": "field acceptance, as-built and lifecycle approvals required"},
        },
        "warnings": [
            "This artifact is not a generation request and does not approve a scenario.",
            "OEM and public values are E0 priors; exact units, configurations and local evidence prevail.",
            "C3 ESD remains fail-closed without a signed numeric project overlay.",
            "Custom values must still enter the evidence inventory and the applicable QA gates.",
        ],
        "package_resolutions": package_rows,
        "standalone_resolutions": list(standalone_selections.values()),
        "parameters": resolution_rows,
    }


def resolve(
    selection_path: Path,
    parameter_catalog_path: Path = DEFAULT_PARAMETER_CATALOG,
    acquisition_catalog_path: Path = DEFAULT_ACQUISITION_CATALOG,
    model_catalog_path: Path = DEFAULT_MODEL_CATALOG,
    preset_catalog_path: Path = DEFAULT_PRESET_CATALOG,
) -> dict[str, Any]:
    parameters = validate_parameter_catalog(load_json(parameter_catalog_path))
    packages = validate_acquisition_catalog(load_json(acquisition_catalog_path), parameters)
    _, models = validate_model_catalog(load_json(model_catalog_path), parameters, packages)
    package_profiles, standalone_profiles = validate_preset_catalog(
        load_json(preset_catalog_path), parameters, packages, models
    )
    selection = load_json(selection_path)
    package_selections, standalone_selections, custom_values = validate_selection(
        selection,
        parameters,
        packages,
        models,
        package_profiles,
        standalone_profiles,
    )
    return build_resolution(
        selection,
        parameters,
        packages,
        models,
        package_profiles,
        package_selections,
        standalone_selections,
        custom_values,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--parameter-catalog", type=Path, default=DEFAULT_PARAMETER_CATALOG)
    parser.add_argument("--acquisition-catalog", type=Path, default=DEFAULT_ACQUISITION_CATALOG)
    parser.add_argument("--model-catalog", type=Path, default=DEFAULT_MODEL_CATALOG)
    parser.add_argument("--preset-catalog", type=Path, default=DEFAULT_PRESET_CATALOG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        report = resolve(
            args.selection,
            args.parameter_catalog,
            args.acquisition_catalog,
            args.model_catalog,
            args.preset_catalog,
        )
    except (ContractError, OSError) as exc:
        print(f"PRESET RESOLUTION FAILED: {exc}")
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    summary = report["summary"]
    print(
        "PRESET RESOLUTION OK: "
        f"{summary['configurable_parameter_count']} parameters, "
        f"{summary['selected_reference_model_count']} selected models, "
        f"{summary['custom_parameter_count']} custom values, "
        "maximum E0, 0 evidence items auto-resolved."
    )
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
